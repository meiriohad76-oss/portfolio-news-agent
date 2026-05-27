from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Literal, Protocol


AccessState = Literal["accessible", "login_required", "challenge_required"]
NAVIGATION_TIMEOUT_MS = 15_000


class BrowserSession(Protocol):
    def open(self, url: str) -> str:
        """Open a URL and return page HTML."""


class ManualBrowserSession(BrowserSession, Protocol):
    def open_for_manual_session(
        self,
        url: str,
        *,
        prompt: Callable[[str], None],
        prompt_message: str,
    ) -> str:
        """Open a URL, keep the browser available for manual action, and return HTML."""


class ArticleAccessError(RuntimeError):
    """Raised when the article cannot be accessed through the browser session."""


@dataclass(frozen=True)
class ExtractedArticle:
    source_url: str
    canonical_url: str
    headline: str | None
    author: str | None
    article_date: str | None
    body_text: str


class PlaywrightArticleBrowser:
    def __init__(
        self,
        *,
        profile_dir: str | Path,
        headless: bool = False,
        browser_channel: str | None = None,
        playwright_factory: Callable[[], object] | None = None,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.browser_channel = browser_channel
        self._playwright_factory = playwright_factory

    def open(self, url: str) -> str:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        playwright = self._make_playwright()
        context = self._launch_context(playwright)
        try:
            page = context.new_page()
            _goto_for_content(page, url)
            return page.content()
        finally:
            context.close()

    def open_for_manual_session(
        self,
        url: str,
        *,
        prompt: Callable[[str], None] = input,
        prompt_message: str = (
            "Use the opened browser to complete Seeking Alpha login or access checks, "
            "then press Enter here to continue."
        ),
    ) -> str:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        playwright = self._make_playwright()
        context = self._launch_context(playwright)
        try:
            page = context.new_page()
            _goto_for_content(page, url)
            _prompt_for_manual_action(prompt, prompt_message)
            _goto_for_content(page, url)
            return page.content()
        finally:
            context.close()

    def _make_playwright(self) -> object:
        if self._playwright_factory is not None:
            return self._playwright_factory()

        from playwright.sync_api import sync_playwright

        return sync_playwright().start()

    def _launch_context(self, playwright: object) -> object:
        options = {
            "user_data_dir": self.profile_dir,
            "headless": self.headless,
        }
        if self.browser_channel:
            options["channel"] = self.browser_channel
        return playwright.chromium.launch_persistent_context(**options)


class CDPArticleBrowser:
    def __init__(
        self,
        *,
        cdp_url: str,
        playwright_factory: Callable[[], object] | None = None,
    ) -> None:
        self.cdp_url = cdp_url
        self._playwright_factory = playwright_factory

    def open(self, url: str) -> str:
        playwright = self._make_playwright()
        try:
            browser = self._connect_browser(playwright)
            context = _default_cdp_context(browser)
            page = context.new_page()
            try:
                _goto_for_content(page, url)
                return page.content()
            finally:
                page.close()
        finally:
            _stop_playwright(playwright)

    def open_for_manual_session(
        self,
        url: str,
        *,
        prompt: Callable[[str], None] = input,
        prompt_message: str = (
            "Use the opened browser tab to complete Seeking Alpha login or access checks, "
            "then press Enter here to continue."
        ),
    ) -> str:
        playwright = self._make_playwright()
        try:
            browser = self._connect_browser(playwright)
            context = _default_cdp_context(browser)
            page = context.new_page()
            should_close_page = True
            try:
                _goto_for_content(page, url)
                try:
                    _prompt_for_manual_action(prompt, prompt_message)
                except ArticleAccessError:
                    should_close_page = False
                    raise
                _goto_for_content(page, url)
                return page.content()
            finally:
                if should_close_page:
                    page.close()
        finally:
            _stop_playwright(playwright)

    def _make_playwright(self) -> object:
        if self._playwright_factory is not None:
            return self._playwright_factory()

        from playwright.sync_api import sync_playwright

        return sync_playwright().start()

    def _connect_browser(self, playwright: object) -> object:
        try:
            return playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:
            raise ArticleAccessError(_cdp_connection_error(self.cdp_url)) from exc


def _default_cdp_context(browser: object) -> object:
    contexts = getattr(browser, "contexts", [])
    if contexts:
        return contexts[0]
    return browser.new_context()


def _stop_playwright(playwright: object) -> None:
    stop = getattr(playwright, "stop", None)
    if callable(stop):
        stop()


def _goto_for_content(page: object, url: str) -> None:
    set_timeout = getattr(page, "set_default_navigation_timeout", None)
    if callable(set_timeout):
        set_timeout(NAVIGATION_TIMEOUT_MS)
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:
        if _is_navigation_timeout(exc):
            return
        raise


def _is_navigation_timeout(exc: Exception) -> bool:
    error_name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return "timeout" in error_name and "goto" in message


def _prompt_for_manual_action(prompt: Callable[[str], None], prompt_message: str) -> None:
    try:
        prompt(prompt_message)
    except EOFError as exc:
        raise ArticleAccessError(
            "Manual browser action is required, but this command is not attached to interactive stdin. "
            "Complete the login/challenge in the opened browser, then rerun the command from PowerShell."
        ) from exc


def _cdp_connection_error(cdp_url: str) -> str:
    return (
        f"CDP browser endpoint is not reachable at {cdp_url}. "
        "Open the dedicated browser with: .\\.venv\\Scripts\\python run_agent.py --start-browser. "
        "A normal Chrome window opened by hand cannot be controlled unless it was started with "
        "--remote-debugging-port."
    )


def fetch_article_with_session(
    url: str,
    *,
    session: BrowserSession,
    prompt: Callable[[str], None] = input,
    allow_manual_recovery: bool = True,
) -> ExtractedArticle:
    html = session.open(url)
    state = detect_access_state(html)
    if state != "accessible":
        if not allow_manual_recovery:
            raise ArticleAccessError(f"Article access failed: {state}")
        prompt_message = _manual_prompt_for_state(state)
        manual_open = getattr(session, "open_for_manual_session", None)
        if callable(manual_open):
            html = manual_open(url, prompt=prompt, prompt_message=prompt_message)
        else:
            prompt(prompt_message)
            html = session.open(url)
        state = detect_access_state(html)
    if state != "accessible":
        raise ArticleAccessError(f"Article access failed: {state}")
    return extract_article_from_html(html, source_url=url)


def detect_access_state(html: str) -> AccessState:
    visible_html = _remove_nonvisible_blocks(html)
    text = unescape(_strip_tags(visible_html)).lower()
    if _has_readable_article(visible_html):
        return "accessible"
    if any(
        marker in text
        for marker in (
            "please sign in",
            "sign in to continue",
            "log in to continue",
            "login to continue",
            "sign in to read",
            "log in to read",
        )
    ):
        return "login_required"
    if any(
        marker in text
        for marker in (
            "checking if the site connection is secure",
            "complete the security check",
            "verify you are human",
            "enable javascript and cookies",
            "ad-blocker enabled",
            "blocked from proceeding",
            "captcha",
        )
    ):
        return "challenge_required"
    return "accessible"


def extract_article_from_html(html: str, *, source_url: str) -> ExtractedArticle:
    parser = _ArticleHTMLParser()
    parser.feed(html)
    parser.close()
    headline = parser.headline or parser.title
    canonical_url = parser.canonical_url or source_url
    body_text = _normalize_text("\n".join(parser.article_text_parts or parser.body_text_parts))
    return ExtractedArticle(
        source_url=source_url,
        canonical_url=canonical_url,
        headline=headline,
        author=parser.author,
        article_date=parser.article_date,
        body_text=body_text,
    )


def _manual_prompt_for_state(state: AccessState) -> str:
    if state == "challenge_required":
        return "Please complete the Seeking Alpha access challenge in the opened browser, then press Enter here to continue."
    return "Please log in to Seeking Alpha in the opened browser, then press Enter here to continue."


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def _remove_nonvisible_blocks(html: str) -> str:
    return re.sub(
        r"<(script|style|svg)\b[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _has_readable_article(html: str) -> bool:
    parser = _ArticleHTMLParser()
    parser.feed(html)
    parser.close()
    if not (parser.headline or parser.title):
        return False
    body_text = _normalize_text("\n".join(parser.article_text_parts))
    return len(body_text) >= 500


def _normalize_text(text: str) -> str:
    lines = []
    for line in unescape(text).splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


class _ArticleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_url: str | None = None
        self.author: str | None = None
        self.article_date: str | None = None
        self.headline: str | None = None
        self.title: str | None = None
        self.article_text_parts: list[str] = []
        self.body_text_parts: list[str] = []
        self._tag_stack: list[str] = []
        self._skip_depth = 0
        self._capture_title = False
        self._capture_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()
        if tag in {"script", "style", "nav", "header", "footer", "aside", "button"}:
            self._skip_depth += 1
        self._tag_stack.append(tag)

        if tag == "link" and attrs_dict.get("rel", "").lower() == "canonical":
            self.canonical_url = attrs_dict.get("href") or self.canonical_url
        if tag == "meta":
            self._handle_meta(attrs_dict)
        if tag == "time" and not self.article_date:
            self.article_date = attrs_dict.get("datetime") or None
        if tag == "title":
            self._capture_title = True
        if tag == "h1" and not self.headline:
            self._capture_heading = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "nav", "header", "footer", "aside", "button"}:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag == "title":
            self._capture_title = False
        if tag == "h1":
            self._capture_heading = False
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text or self._skip_depth:
            return
        if self._capture_title:
            self.title = _append_text(self.title, text)
        if self._capture_heading:
            self.headline = _append_text(self.headline, text)
        if "article" in self._tag_stack:
            self.article_text_parts.append(text)
        elif "body" in self._tag_stack:
            self.body_text_parts.append(text)

    def _handle_meta(self, attrs: dict[str, str]) -> None:
        name = attrs.get("name", "").lower()
        prop = attrs.get("property", "").lower()
        content = attrs.get("content", "").strip()
        if not content:
            return
        if name == "author" and not self.author:
            self.author = content
        if prop == "article:published_time" and not self.article_date:
            self.article_date = content
        if prop == "og:title" and not self.headline:
            self.headline = content


def _append_text(current: str | None, text: str) -> str:
    if not current:
        return text
    return f"{current} {text}"
