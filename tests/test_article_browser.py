import tempfile
import unittest
from pathlib import Path

from portfolio_news_agent.article_browser import (
    ArticleAccessError,
    CDPArticleBrowser,
    PlaywrightArticleBrowser,
    detect_access_state,
    extract_article_from_html,
    fetch_article_with_session,
)


class FakeBrowserSession:
    def __init__(self, pages):
        self.pages = list(pages)
        self.opened_urls = []

    def open(self, url):
        self.opened_urls.append(url)
        if not self.pages:
            raise AssertionError("No fake page content left")
        return self.pages.pop(0)


class FakePrompt:
    def __init__(self):
        self.messages = []

    def __call__(self, message):
        self.messages.append(message)


class ArticleBrowserTests(unittest.TestCase):
    def test_extract_article_metadata_and_clean_body_text(self):
        html = """
        <html>
          <head>
            <link rel="canonical" href="https://seekingalpha.com/article/123-aem-update" />
            <meta name="author" content="Jane Analyst" />
            <meta property="article:published_time" content="2026-05-23T10:00:00Z" />
            <title>Browser title</title>
          </head>
          <body>
            <nav>Subscribe now</nav>
            <article>
              <h1>Agnico Eagle margin outlook improves</h1>
              <script>window.secret = true;</script>
              <p>Margins improved as gold prices stayed firm.</p>
              <p>Management guided to higher cash flow.</p>
            </article>
          </body>
        </html>
        """

        article = extract_article_from_html(
            html,
            source_url="https://email.example/link",
        )

        self.assertEqual(
            article.canonical_url,
            "https://seekingalpha.com/article/123-aem-update",
        )
        self.assertEqual(article.headline, "Agnico Eagle margin outlook improves")
        self.assertEqual(article.author, "Jane Analyst")
        self.assertEqual(article.article_date, "2026-05-23T10:00:00Z")
        self.assertIn("Margins improved as gold prices stayed firm.", article.body_text)
        self.assertNotIn("Subscribe now", article.body_text)
        self.assertNotIn("window.secret", article.body_text)

    def test_detects_login_and_challenge_pages(self):
        self.assertEqual(
            detect_access_state("<html>Please sign in to continue</html>"),
            "login_required",
        )
        self.assertEqual(
            detect_access_state("<html><a>Log In</a><article>Readable public page.</article></html>"),
            "accessible",
        )
        self.assertEqual(
            detect_access_state(
                "<html><style>.px-captcha-visible{display:flex}</style>"
                "<article>Readable public page.</article></html>"
            ),
            "accessible",
        )
        self.assertEqual(
            detect_access_state("<html>Checking if the site connection is secure</html>"),
            "challenge_required",
        )
        self.assertEqual(
            detect_access_state("<html><title>Access to this page has been denied</title></html>"),
            "challenge_required",
        )
        self.assertEqual(
            detect_access_state(
                "<article><h1>AEM update</h1>"
                "<p>" + ("Readable article text. " * 40) + "</p></article>"
                "<footer>Please enable Javascript and cookies. "
                "If you have an ad-blocker enabled you may be blocked from proceeding.</footer>"
            ),
            "accessible",
        )
        self.assertEqual(
            detect_access_state(
                "<html>Please enable Javascript and cookies. "
                "If you have an ad-blocker enabled you may be blocked from proceeding.</html>"
            ),
            "challenge_required",
        )
        self.assertEqual(
            detect_access_state("<article><h1>AEM update</h1><p>Readable.</p></article>"),
            "accessible",
        )

    def test_fetch_prompts_for_manual_login_then_retries(self):
        session = FakeBrowserSession(
            [
                "<html>Please sign in to continue</html>",
                "<article><h1>AEM update</h1><p>Readable article text.</p></article>",
            ]
        )
        prompt = FakePrompt()

        article = fetch_article_with_session(
            "https://seekingalpha.com/article/123-aem-update",
            session=session,
            prompt=prompt,
        )

        self.assertEqual(len(session.opened_urls), 2)
        self.assertIn("log in", prompt.messages[0])
        self.assertEqual(article.headline, "AEM update")

    def test_fetch_uses_manual_session_when_browser_can_hold_login_window(self):
        class ManualSession:
            def __init__(self):
                self.calls = []

            def open(self, url):
                self.calls.append(("open", url))
                return "<html>Please sign in to continue</html>"

            def open_for_manual_session(self, url, *, prompt, prompt_message):
                self.calls.append(("manual", url, prompt_message))
                prompt(prompt_message)
                return "<article><h1>AEM update</h1><p>Readable.</p></article>"

        session = ManualSession()
        prompt = FakePrompt()

        article = fetch_article_with_session(
            "https://seekingalpha.com/article/123-aem-update",
            session=session,
            prompt=prompt,
        )

        self.assertEqual(article.headline, "AEM update")
        self.assertEqual(session.calls[0][0], "open")
        self.assertEqual(session.calls[1][0], "manual")
        self.assertIn("log in", prompt.messages[0])

    def test_fetch_fails_when_access_still_blocked_after_prompt(self):
        session = FakeBrowserSession(
            [
                "<html>Please sign in to continue</html>",
                "<html>Please sign in to continue</html>",
            ]
        )

        with self.assertRaises(ArticleAccessError):
            fetch_article_with_session(
                "https://seekingalpha.com/article/123-aem-update",
                session=session,
                prompt=lambda message: None,
            )

    def test_playwright_browser_uses_persistent_profile_and_visible_default(self):
        calls = []

        class FakePage:
            url = "https://seekingalpha.com/article/123-aem-update"

            def goto(self, url, wait_until):
                calls.append(("goto", url, wait_until))

            def content(self):
                return "<article><h1>AEM update</h1><p>Readable.</p></article>"

        class FakeContext:
            def new_page(self):
                return FakePage()

            def close(self):
                calls.append(("close",))

        class FakeChromium:
            def launch_persistent_context(self, user_data_dir, headless):
                calls.append(("launch", Path(user_data_dir), headless))
                return FakeContext()

        class FakePlaywright:
            chromium = FakeChromium()

        with tempfile.TemporaryDirectory() as tmp_dir:
            browser = PlaywrightArticleBrowser(
                profile_dir=Path(tmp_dir) / "browser-profile",
                playwright_factory=lambda: FakePlaywright(),
            )
            html = browser.open("https://seekingalpha.com/article/123-aem-update")

        self.assertIn(("launch", Path(tmp_dir) / "browser-profile", False), calls)
        self.assertIn(
            ("goto", "https://seekingalpha.com/article/123-aem-update", "domcontentloaded"),
            calls,
        )
        self.assertEqual(html, "<article><h1>AEM update</h1><p>Readable.</p></article>")
        self.assertIn(("close",), calls)

    def test_playwright_browser_can_launch_installed_browser_channel(self):
        calls = []

        class FakePage:
            def goto(self, url, wait_until):
                calls.append(("goto", url, wait_until))

            def content(self):
                return "<article><h1>AEM update</h1><p>Readable.</p></article>"

        class FakeContext:
            def new_page(self):
                return FakePage()

            def close(self):
                calls.append(("close",))

        class FakeChromium:
            def launch_persistent_context(self, **kwargs):
                calls.append(("launch", kwargs))
                return FakeContext()

        class FakePlaywright:
            chromium = FakeChromium()

        with tempfile.TemporaryDirectory() as tmp_dir:
            browser = PlaywrightArticleBrowser(
                profile_dir=Path(tmp_dir) / "browser-profile",
                browser_channel="chrome",
                playwright_factory=lambda: FakePlaywright(),
            )
            browser.open("https://seekingalpha.com")

        self.assertEqual(calls[0][1]["channel"], "chrome")
        self.assertEqual(calls[0][1]["headless"], False)

    def test_playwright_browser_manual_session_keeps_context_open_until_prompt_returns(self):
        calls = []

        class FakePage:
            def __init__(self):
                self.goto_count = 0

            def goto(self, url, wait_until):
                self.goto_count += 1
                calls.append(("goto", self.goto_count, url, wait_until))

            def content(self):
                return "<article><h1>AEM update</h1><p>Readable.</p></article>"

        class FakeContext:
            def __init__(self):
                self.page = FakePage()

            def new_page(self):
                return self.page

            def close(self):
                calls.append(("close",))

        class FakeChromium:
            def launch_persistent_context(self, user_data_dir, headless):
                calls.append(("launch", Path(user_data_dir), headless))
                return FakeContext()

        class FakePlaywright:
            chromium = FakeChromium()

        prompts = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            browser = PlaywrightArticleBrowser(
                profile_dir=Path(tmp_dir) / "browser-profile",
                playwright_factory=lambda: FakePlaywright(),
            )
            html = browser.open_for_manual_session(
                "https://seekingalpha.com/article/123-aem-update",
                prompt=prompts.append,
                prompt_message="Complete login, then press Enter.",
            )

        self.assertEqual(prompts, ["Complete login, then press Enter."])
        self.assertIn(
            ("goto", 1, "https://seekingalpha.com/article/123-aem-update", "domcontentloaded"),
            calls,
        )
        self.assertIn(
            ("goto", 2, "https://seekingalpha.com/article/123-aem-update", "domcontentloaded"),
            calls,
        )
        self.assertEqual(html, "<article><h1>AEM update</h1><p>Readable.</p></article>")
        self.assertIn(("close",), calls)

    def test_cdp_browser_reuses_visible_tab_and_keeps_it_open(self):
        calls = []

        class FakePage:
            def goto(self, url, wait_until):
                calls.append(("goto", url, wait_until))

            def content(self):
                return "<article><h1>AEM update</h1><p>Readable.</p></article>"

            def close(self):
                calls.append(("page_close",))

        class FakeContext:
            def new_page(self):
                calls.append(("new_page",))
                return FakePage()

        class FakeBrowser:
            contexts = [FakeContext()]

            def close(self):
                raise AssertionError("CDP browser should not close the user browser")

        class FakeChromium:
            def connect_over_cdp(self, endpoint_url):
                calls.append(("connect", endpoint_url))
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def stop(self):
                calls.append(("playwright_stop",))

        browser = CDPArticleBrowser(
            cdp_url="http://127.0.0.1:9222",
            playwright_factory=lambda: FakePlaywright(),
        )

        html = browser.open("https://seekingalpha.com/article/123-aem-update")

        self.assertEqual(html, "<article><h1>AEM update</h1><p>Readable.</p></article>")
        self.assertEqual(calls[0], ("connect", "http://127.0.0.1:9222"))
        self.assertIn(("new_page",), calls)
        self.assertIn(
            ("goto", "https://seekingalpha.com/article/123-aem-update", "domcontentloaded"),
            calls,
        )
        self.assertNotIn(("page_close",), calls)
        self.assertEqual(calls[-1], ("playwright_stop",))

    def test_cdp_browser_can_close_tab_when_explicitly_requested(self):
        calls = []

        class FakePage:
            def goto(self, url, wait_until):
                calls.append(("goto", url, wait_until))

            def content(self):
                return "<article><h1>AEM update</h1><p>Readable.</p></article>"

            def close(self):
                calls.append(("page_close",))

        class FakeContext:
            def new_page(self):
                return FakePage()

        class FakeBrowser:
            contexts = [FakeContext()]

        class FakeChromium:
            def connect_over_cdp(self, endpoint_url):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def stop(self):
                calls.append(("playwright_stop",))

        browser = CDPArticleBrowser(
            cdp_url="http://127.0.0.1:9222",
            close_after_read=True,
            playwright_factory=lambda: FakePlaywright(),
        )

        browser.open("https://seekingalpha.com/article/123-aem-update")

        self.assertIn(("page_close",), calls)

    def test_cdp_browser_connection_failure_raises_actionable_access_error(self):
        calls = []

        class FakeChromium:
            def connect_over_cdp(self, endpoint_url):
                calls.append(("connect", endpoint_url))
                raise RuntimeError("connect ECONNREFUSED 127.0.0.1:9222")

        class FakePlaywright:
            chromium = FakeChromium()

            def stop(self):
                calls.append(("playwright_stop",))

        browser = CDPArticleBrowser(
            cdp_url="http://127.0.0.1:9222",
            playwright_factory=lambda: FakePlaywright(),
        )

        with self.assertRaisesRegex(ArticleAccessError, "CDP browser endpoint"):
            browser.open("https://seekingalpha.com")

        self.assertEqual(calls[0], ("connect", "http://127.0.0.1:9222"))
        self.assertEqual(calls[-1], ("playwright_stop",))

    def test_cdp_browser_returns_visible_html_when_navigation_times_out(self):
        calls = []

        class FakePage:
            def goto(self, url, wait_until, timeout=None):
                calls.append(("goto", url, wait_until, timeout))
                raise TimeoutError("Page.goto: Timeout 15000ms exceeded")

            def content(self):
                calls.append(("content",))
                return "<article><h1>AEM update</h1><p>Readable after timeout.</p></article>"

            def close(self):
                calls.append(("page_close",))

        class FakeContext:
            def new_page(self):
                return FakePage()

        class FakeBrowser:
            contexts = [FakeContext()]

        class FakeChromium:
            def connect_over_cdp(self, endpoint_url):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def stop(self):
                calls.append(("playwright_stop",))

        browser = CDPArticleBrowser(
            cdp_url="http://127.0.0.1:9222",
            playwright_factory=lambda: FakePlaywright(),
        )

        html = browser.open("https://seekingalpha.com/article/123-aem-update")

        self.assertIn("Readable after timeout", html)
        self.assertIn(("content",), calls)
        self.assertNotIn(("page_close",), calls)
        self.assertEqual(calls[-1], ("playwright_stop",))

    def test_cdp_browser_manual_session_keeps_tab_open_until_prompt_returns(self):
        calls = []

        class FakePage:
            def __init__(self):
                self.goto_count = 0

            def goto(self, url, wait_until):
                self.goto_count += 1
                calls.append(("goto", self.goto_count, url, wait_until))

            def content(self):
                return "<article><h1>AEM update</h1><p>Readable.</p></article>"

            def close(self):
                calls.append(("page_close",))

        class FakeContext:
            def new_page(self):
                calls.append(("new_page",))
                return FakePage()

        class FakeBrowser:
            contexts = [FakeContext()]

        class FakeChromium:
            def connect_over_cdp(self, endpoint_url):
                calls.append(("connect", endpoint_url))
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def stop(self):
                calls.append(("playwright_stop",))

        prompts = []
        browser = CDPArticleBrowser(
            cdp_url="http://127.0.0.1:9222",
            playwright_factory=lambda: FakePlaywright(),
        )

        html = browser.open_for_manual_session(
            "https://seekingalpha.com/article/123-aem-update",
            prompt=prompts.append,
            prompt_message="Complete login, then press Enter.",
        )

        self.assertEqual(prompts, ["Complete login, then press Enter."])
        self.assertIn(
            ("goto", 1, "https://seekingalpha.com/article/123-aem-update", "domcontentloaded"),
            calls,
        )
        self.assertIn(
            ("goto", 2, "https://seekingalpha.com/article/123-aem-update", "domcontentloaded"),
            calls,
        )
        self.assertEqual(html, "<article><h1>AEM update</h1><p>Readable.</p></article>")
        self.assertNotIn(("page_close",), calls)

    def test_cdp_browser_manual_session_opens_fresh_user_tab_even_when_pages_exist(self):
        calls = []

        class ExistingPage:
            def goto(self, url, wait_until):
                calls.append(("existing_goto", url, wait_until))

        class FakePage:
            def goto(self, url, wait_until):
                calls.append(("new_goto", url, wait_until))

            def content(self):
                return "<html>Logged in</html>"

        class FakeContext:
            pages = [ExistingPage()]

            def new_page(self):
                calls.append(("new_page",))
                return FakePage()

        class FakeBrowser:
            contexts = [FakeContext()]

        class FakeChromium:
            def connect_over_cdp(self, endpoint_url):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def stop(self):
                calls.append(("playwright_stop",))

        browser = CDPArticleBrowser(
            cdp_url="http://127.0.0.1:9222",
            playwright_factory=lambda: FakePlaywright(),
        )

        browser.open_for_manual_session(
            "https://seekingalpha.com",
            prompt=lambda message: None,
            prompt_message="Complete login.",
        )

        self.assertIn(("new_page",), calls)
        self.assertNotIn(("existing_goto", "https://seekingalpha.com", "domcontentloaded"), calls)
        self.assertIn(("new_goto", "https://seekingalpha.com", "domcontentloaded"), calls)

    def test_cdp_browser_manual_session_leaves_tab_open_when_prompt_has_no_stdin(self):
        calls = []

        class FakePage:
            def goto(self, url, wait_until):
                calls.append(("goto", url, wait_until))

            def close(self):
                calls.append(("page_close",))

        class FakeContext:
            def new_page(self):
                return FakePage()

        class FakeBrowser:
            contexts = [FakeContext()]

        class FakeChromium:
            def connect_over_cdp(self, endpoint_url):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def stop(self):
                calls.append(("playwright_stop",))

        browser = CDPArticleBrowser(
            cdp_url="http://127.0.0.1:9222",
            playwright_factory=lambda: FakePlaywright(),
        )

        with self.assertRaisesRegex(ArticleAccessError, "Manual browser action is required"):
            browser.open_for_manual_session(
                "https://seekingalpha.com/article/123-aem-update",
                prompt=lambda message: (_ for _ in ()).throw(EOFError()),
                prompt_message="Complete challenge.",
            )

        self.assertNotIn(("page_close",), calls)
        self.assertEqual(calls[-1], ("playwright_stop",))
