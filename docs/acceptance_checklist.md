# V1 Acceptance Checklist

Run this checklist with real credentials, a current portfolio file, and at least one unread Seeking Alpha email.

## Preflight

- `config.yaml` points at the current portfolio workbook.
- `.env` contains the configured LLM values. For the local Pi model, `config.yaml`
  should set `llm_provider: "local_ollama"` and point at the Pi Ollama endpoint.
- `data/secrets/gmail_credentials.json` exists.
- Dependencies are installed.
- Chromium is installed for Playwright.
- `browser_cdp_url` points at the dedicated browser endpoint, normally `http://127.0.0.1:9222`.

## Command

Capability checks:

```bash
.\.venv\Scripts\python run_agent.py --init-local
.\.venv\Scripts\python run_agent.py --preflight
.\.venv\Scripts\python run_agent.py --check-gmail
.\.venv\Scripts\python run_agent.py --start-browser
.\.venv\Scripts\python run_agent.py --check-sa-browser
.\.venv\Scripts\python run_agent.py --analyze-url "https://seekingalpha.com/article/example"
```

Full run:

```bash
.\.venv\Scripts\python run_agent.py --once
```

## Expected Results

- `--init-local` creates missing local templates/directories and does not overwrite existing `.env`.
- `--preflight` prints which capabilities are ready and names missing local blockers.
- `--check-gmail` completes OAuth when needed and prints unread Seeking Alpha message previews.
- `--start-browser` starts a visible Chrome/Edge browser using the configured dedicated profile and CDP endpoint.
- `--check-sa-browser` opens a tab in that running browser, prompts only if login/challenge completion is needed, then reports the access state.
- `--analyze-url` imports the portfolio, opens the article, and prints the structured relevant/irrelevant result without Gmail mark-read side effects.
- Portfolio import creates one `portfolio_imports` row.
- Current holdings appear in `assets`.
- Gmail scan uses `from:account@seekingalpha.com is:unread`.
- Each extracted Seeking Alpha URL creates one `gmail_article_links` row.
- Browser opens visibly and uses `data/sa-browser-profile`.
- A normal hand-opened Chrome window is not treated as attachable unless it was started with remote debugging.
- After manual Seeking Alpha login in the dedicated browser, article tabs open and close without requiring a new login for every email.
- The configured LLM returns valid structured JSON.
- Relevant article-stock rows appear in `article_asset_summaries`.
- A relevant Gmail message is marked read only after every extracted link reaches an acceptable terminal state.
- Irrelevant-only messages remain unread and their links are `irrelevant_seen`.
- Failed links remain unread and store a failure status.
- Full article text is not stored in SQLite.

## Useful SQLite Checks

```bash
.\.venv\Scripts\python -c "import sqlite3; c=sqlite3.connect('data/portfolio_news.db'); print(c.execute('select status, count(*) from gmail_article_links group by status').fetchall())"
.\.venv\Scripts\python -c "import sqlite3; c=sqlite3.connect('data/portfolio_news.db'); print(c.execute('select symbol, inferred_sentiment, action_relevance from article_asset_summaries').fetchall())"
```
