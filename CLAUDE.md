# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
```

Place `credentials.json` (OAuth 2.0 desktop app credentials from Google Cloud Console) in the project root. On first run, a browser window opens for Gmail authorization; the token is saved to `token.json` automatically.

## Running the agent

```bash
# Digest for yesterday (default)
python gmail_ai_digest.py

# Specific date
python gmail_ai_digest.py --date 2026-02-18

# Limit emails and save JSON output
python gmail_ai_digest.py --max-emails 20 --save-json
```

JSON digests are saved to `digests/digest_YYYY-MM-DD.json`.

## Architecture

The entire application is a single file: [gmail_ai_digest.py](gmail_ai_digest.py).

**Data flow:**
1. `authenticate_gmail()` handles OAuth2 token management (load → refresh → browser flow)
2. `fetch_ai_emails()` queries Gmail API with keyword filters + date range, then `_parse_email()` / `_extract_body()` extract structured email content (text/plain preferred, HTML fallback)
3. A PydanticAI `Agent` (`digest_agent`) is instantiated with `claude-sonnet-4-6` and a `DailyDigest` structured output type
4. The agent's tool `recuperer_emails()` is called during inference — it invokes `fetch_ai_emails()` via `GmailDigestDeps` (dependency injection)
5. `generate_digest()` is the async entry point; `print_digest()` and `save_digest_json()` handle output

**Key types:**
- `GmailDigestDeps` (dataclass): dependencies injected into the agent — `gmail_service`, `target_date`, `max_emails`
- `ArticleSummary` (Pydantic): per-article structured output with `titre`, `source`, `categorie`, `resume`, `pertinence` (1–5), optional `url`
- `DailyDigest` (Pydantic): top-level output — list of `ArticleSummary` plus `synthese_globale` and `top_3_a_retenir`

**Filtering logic:** The Gmail query uses `AI_KEYWORDS_QUERY` (subject-line keyword filter). A second layer of filtering happens in the agent's system prompt: it excludes funding rounds, acquisitions, and personnel moves from the digest output.

## Cron automation

```bash
0 7 * * * cd /path/to/agent && ANTHROPIC_API_KEY="sk-ant-..." python gmail_ai_digest.py --save-json
```
