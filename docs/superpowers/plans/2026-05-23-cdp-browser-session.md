# CDP Browser Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the agent reuse a user-logged-in Chrome browser session to open each Seeking Alpha email link in a new tab, extract/analyze content, and close the tab.

**Architecture:** Add a CDP-backed article browser that connects to a running Chrome remote-debugging endpoint and owns only per-article tabs. Add a safe launcher command that starts Chrome with a dedicated profile and debugging port. Existing Gmail, portfolio import, LLM analysis, and Gmail mark-read rules stay unchanged.

**Tech Stack:** Python, Playwright CDP (`chromium.connect_over_cdp`), Chrome remote debugging, unittest.

---

### Task 1: CDP Browser Session

**Files:**
- Modify: `portfolio_news_agent/article_browser.py`
- Test: `tests/test_article_browser.py`

- [x] Add tests proving `CDPArticleBrowser.open()` connects to CDP, opens a new tab, reads page HTML, closes the tab, and disconnects the Playwright driver without closing the user browser.
- [x] Add tests proving `CDPArticleBrowser.open_for_manual_session()` keeps the tab open while prompting, then reloads and closes the tab.
- [x] Implement the CDP browser class with the same `BrowserSession`/manual session interface used by the orchestrator.

### Task 2: Browser Launcher and Config

**Files:**
- Create: `portfolio_news_agent/browser_launcher.py`
- Modify: `portfolio_news_agent/config.py`
- Modify: `config.example.yaml`
- Modify: `config.yaml`
- Test: `tests/test_browser_launcher.py`
- Test: `tests/test_config.py`

- [x] Add config support for `browser_cdp_url`.
- [x] Add launcher that starts Chrome or Edge with `--remote-debugging-port`, `--user-data-dir`, and a normal visible window.
- [x] If the CDP endpoint is already reachable, report that the browser is already running instead of starting another one.

### Task 3: CLI and Orchestrator Wiring

**Files:**
- Modify: `portfolio_news_agent/cli.py`
- Modify: `portfolio_news_agent/orchestrator.py`
- Modify: `portfolio_news_agent/capability_checks.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_orchestrator.py`

- [x] Add `--start-browser` to launch the dedicated SA browser.
- [x] Add `--check-sa-browser` to connect to the running browser and verify access state.
- [x] Make `--analyze-url` and `--once` use CDP mode when `browser_cdp_url` is configured.

### Task 4: Docs and Verification

**Files:**
- Modify: `docs/operator_setup.md`
- Modify: `docs/acceptance_checklist.md`

- [x] Document the new flow: `--start-browser`, login once, `--check-sa-browser`, `--analyze-url`, then `--once`.
- [x] Run `.\.venv\Scripts\python.exe -m unittest discover`.
- [x] Run `.\.venv\Scripts\python.exe -m compileall portfolio_news_agent run_agent.py tests`.
