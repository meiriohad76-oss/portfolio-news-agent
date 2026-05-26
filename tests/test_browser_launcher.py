import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from portfolio_news_agent.browser_launcher import start_debug_browser


class BrowserLauncherTests(unittest.TestCase):
    def test_start_debug_browser_starts_chrome_with_remote_debugging(self):
        popen_calls = []
        checks = []

        class FakeProcess:
            pid = 12345

        def fake_popen(command, **kwargs):
            popen_calls.append((command, kwargs))
            return FakeProcess()

        def endpoint_checker(url):
            checks.append(url)
            return len(checks) >= 2

        with tempfile.TemporaryDirectory() as tmp_dir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp_dir)
                result = start_debug_browser(
                    profile_dir=Path("sa-browser-profile"),
                    cdp_url="http://127.0.0.1:9222",
                    browser_channel="chrome",
                    endpoint_checker=endpoint_checker,
                    popen=fake_popen,
                    sleeper=lambda seconds: None,
                    wait_seconds=1.0,
                    poll_interval=0.1,
                )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(result.status, "started")
        self.assertTrue(result.ready)
        self.assertEqual(result.pid, 12345)
        command = popen_calls[0][0]
        self.assertIn("--remote-debugging-port=9222", command)
        self.assertIn("--remote-debugging-address=127.0.0.1", command)
        self.assertIn("--new-window", command)
        self.assertIn("--start-maximized", command)
        user_data_arg = next(part for part in command if part.startswith("--user-data-dir="))
        user_data_dir = Path(user_data_arg.split("=", 1)[1])
        self.assertTrue(user_data_dir.is_absolute())
        self.assertIn("https://seekingalpha.com", command)
        if os.name == "nt":
            self.assertFalse(popen_calls[0][1]["creationflags"] & subprocess.DETACHED_PROCESS)

    def test_start_debug_browser_waits_until_endpoint_is_ready(self):
        checks = []
        sleeps = []

        class FakeProcess:
            pid = 12345

        def endpoint_checker(url):
            checks.append(url)
            return len(checks) >= 3

        result = start_debug_browser(
            profile_dir=Path("data/sa-browser-profile"),
            cdp_url="http://127.0.0.1:9222",
            browser_channel="chrome",
            endpoint_checker=endpoint_checker,
            popen=lambda command, **kwargs: FakeProcess(),
            sleeper=sleeps.append,
            wait_seconds=2.0,
            poll_interval=0.1,
        )

        self.assertEqual(result.status, "started")
        self.assertTrue(result.ready)
        self.assertEqual(checks, ["http://127.0.0.1:9222"] * 3)
        self.assertEqual(sleeps, [0.1])

    def test_start_debug_browser_reuses_existing_endpoint(self):
        popen_calls = []

        result = start_debug_browser(
            profile_dir=Path("data/sa-browser-profile"),
            cdp_url="http://127.0.0.1:9222",
            browser_channel="chrome",
            endpoint_checker=lambda url: True,
            popen=lambda command, **kwargs: popen_calls.append((command, kwargs)),
        )

        self.assertEqual(result.status, "already_running")
        self.assertTrue(result.ready)
        self.assertEqual(popen_calls, [])


if __name__ == "__main__":
    unittest.main()
