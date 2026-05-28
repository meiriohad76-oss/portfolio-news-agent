# Portfolio News Agent - Current Product Plan

## Purpose

The Portfolio News Agent monitors unread Seeking Alpha emails, opens linked
articles through the user's logged-in Seeking Alpha browser session, analyzes
article relevance to the configured portfolio, and stores structured article
and per-ticker evidence in SQLite for the trading agency to consume.

## Current Operating Model

1. Load `config.yaml` and `.env`.
2. Import the current portfolio workbook.
3. Scan Gmail for unread messages from `account@seekingalpha.com`.
4. Extract Seeking Alpha links from each unread email.
5. Store one link row per Gmail-message/article pair.
6. Reuse a visible Chrome session launched with remote debugging.
7. Require the user to log in and confirm article access before processing.
8. Open each queued article through that same Chrome session.
9. Extract headline, metadata, and article body text.
10. Analyze the article with the configured LLM provider.
11. Save structured article/ticker summaries to SQLite.
12. Mark a Gmail message read only after its relevant links are processed safely.

## LLM Providers

The preferred local configuration is:

```yaml
llm_provider: "local_ollama"
local_llm_base_url: "http://10.100.102.18:11434"
local_llm_model: "qwen3.5:4b"
local_llm_timeout_seconds: 180
```

OpenAI remains available by setting `llm_provider: "openai"` and supplying
`OPENAI_API_KEY`, but production testing currently favors the local Pi model.

## Analysis Contract

The model must return JSON only. It classifies whether the article contains
company-specific evidence for supplied portfolio tickers. Broad macro, sector,
commodity, rate, inflation, or index context is not enough by itself. A ticker
is relevant only when the article includes direct evidence such as named company
discussion, financial metrics, guidance, earnings, margins, demand, ratings,
price targets, regulatory/legal events, management commentary, or other material
facts tied to that company.

Each relevant ticker summary includes:

- `symbol`
- `inferred_sentiment`
- `theme`
- `action_relevance`
- `short_summary`
- `confidence`
- optional ratings, price targets, and forward-looking data

## Browser And Access Boundary

The agent uses a normal visible Chrome session with a persistent profile and CDP
endpoint. The user logs in manually and completes any challenge manually. The
agent does not bypass access controls, CAPTCHA, paywalls, or bot-detection
systems. If access fails, the link records the failure and the Gmail message
remains unread for a future retry.

## Persistence

SQLite stores:

- `runs`
- imported `assets`
- Gmail messages
- article links and statuses
- article metadata
- per-asset article summaries

Full article body text is used for analysis but is not stored in SQLite.

## Acceptance Criteria

- Preflight reports config, Gmail credentials, portfolio, browser, and LLM
  readiness.
- `--check-sa-browser` reports an accessible Seeking Alpha session.
- `--check-gmail` reports unread Seeking Alpha messages and extracted links.
- `--once --max-emails N --max-articles M --login-acknowledged` processes a
  bounded batch through the same logged-in Chrome session.
- Relevant ticker summaries appear in `article_asset_summaries`.
- Failed article links remain retryable.
- Runtime code has no unused alert-sender integration.
