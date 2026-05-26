# Portfolio News Agent Operator Setup

## Python Setup

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
```

If the Playwright browser download fails on Windows with `UNABLE_TO_VERIFY_LEAF_SIGNATURE`, rerun the install with Node using the Windows certificate store:

```powershell
$env:NODE_OPTIONS="--use-system-ca"
.\.venv\Scripts\python -m playwright install chromium
```

## Local Files

Create safe local scaffolding:

```bash
.\.venv\Scripts\python run_agent.py --init-local
```

This creates missing local templates and directories without overwriting existing `.env` secrets.

Review `config.yaml` and set:

```yaml
portfolio_file: "C:/Users/meiri/Downloads/Portfolio 2026-05-23.xlsx"
gmail_sender: "account@seekingalpha.com"
database_path: "data/portfolio_news.db"
browser_profile_dir: "data/sa-browser-profile"
browser_channel: "chrome"
browser_cdp_url: "http://127.0.0.1:9222"
gmail_credentials_path: "data/secrets/gmail_credentials.json"
gmail_token_path: "data/secrets/gmail_token.json"
openai_model: "gpt-5-nano"
prompt_version: "v1"
telegram_enabled: true
mark_relevant_as_read: true
leave_irrelevant_unread: true
```

Create `.env` from `.env.example`:

```text
OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Keep `.env`, `data/`, Gmail tokens, browser profile data, and SQLite databases out of git.

## Gmail OAuth

1. Create a Google OAuth desktop client for the Gmail API.
2. Download the OAuth client JSON to `data/secrets/gmail_credentials.json`.
3. Run the agent once. The first run opens the local OAuth browser flow and writes `data/secrets/gmail_token.json`.
4. The application requests Gmail modify access so it can search unread messages, read message bodies, and remove the `UNREAD` label only after successful relevant processing.

## Telegram

1. Create a bot with BotFather.
2. Send a message to the bot from the private chat that should receive alerts.
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

## Seeking Alpha

The recommended Seeking Alpha flow uses one visible Chrome or Edge browser launched with remote debugging. You log in once in that browser, then the agent reuses the same logged-in session. For each article it opens a new tab, extracts the article, closes that tab, and moves to the next email link. The agent does not bypass access controls.

A normal Chrome window opened by hand cannot be controlled by the agent unless it was started with `--remote-debugging-port`. Use `--start-browser`, or let `--check-sa-browser` start the dedicated browser for you.

Use installed Chrome with a dedicated profile and CDP endpoint:

```yaml
browser_profile_dir: "data/sa-browser-profile"
browser_channel: "chrome"
browser_cdp_url: "http://127.0.0.1:9222"
```

Start the dedicated browser:

```bash
.\.venv\Scripts\python run_agent.py --start-browser
```

Log in to Seeking Alpha in that browser. Leave it open while the agent runs.

Verify that the running browser session can access Seeking Alpha:

```bash
.\.venv\Scripts\python run_agent.py --check-sa-browser
```

If the CDP endpoint is not already running, `--check-sa-browser` starts the dedicated browser first.

## Capability Checks

Run these before the full agent when validating access one piece at a time.

Check local files and secrets without contacting external services:

```bash
.\.venv\Scripts\python run_agent.py --preflight
```

Check Gmail OAuth/API access and preview unread Seeking Alpha messages:

```bash
.\.venv\Scripts\python run_agent.py --check-gmail
```

Start and check the dedicated Seeking Alpha browser. The second command will start it automatically if needed:

```bash
.\.venv\Scripts\python run_agent.py --start-browser
.\.venv\Scripts\python run_agent.py --check-sa-browser
```

Open and analyze one article against the portfolio without sending Telegram messages or marking Gmail read:

```bash
.\.venv\Scripts\python run_agent.py --analyze-url "https://seekingalpha.com/article/example"
```

## Run

```bash
.\.venv\Scripts\python run_agent.py --once
```

## Known V1 Limits

- Manual one-shot run only.
- No scheduler.
- No local LLM fallback.
- No cloud deployment.
- No watchlist support.
- No trading recommendations.
- Full article text is used for analysis but is not stored in SQLite.
