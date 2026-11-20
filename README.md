# Gemini Personal Telegram Bot — v3

Private bot (only `OWNER_ID` can use it). All v2 variables and commands are kept; v3 only adds.

## What changed in v3
- **Never cancels a task when quota runs out.**
  1. A 429 / 5xx / network error moves the *same request* to the next key, then the next model (other key on the same model first, light model last).
  2. If every key/model is cooling down, the request waits (up to `MAX_WAIT_SECONDS`) and continues automatically, telling you once.
  3. If it would wait longer than that (e.g. daily quota), the request is **saved in a queue** (survives restarts) and answered automatically when a key recovers. `/queue` shows it, `/clearqueue` cancels it. Queued requests expire after `PENDING_TTL_SECONDS`.
  4. If an answer is cut by the output-token limit, the bot **continues it automatically** (up to `MAX_CONTINUATIONS` times), even on another key. An empty answer caused by "thinking" eating the budget is retried with a bigger budget.
- Cooldowns are per (key, model), use Google's `retry in Ns` hint, and treat daily quota separately.
- Router uses whole-word matching (no more "کدام" → code, "capital" → code, "به فارسی جواب بده" → translate).
- Translation: long texts are chunked, code blocks are never translated, `/code` and `/translate` skip translation, source language is auto-detected, no shared mutable state.
- A failed request no longer leaves an orphan message in the history; per-user lock keeps history ordered.
- Legacy `chat.db` is imported only once (not again after `/reset`).
- `/command text` is now one-shot; a bare `/command` sets the mode (saved in the DB, survives restarts); `/chat` returns to automatic routing. Upper-case commands work.
- Telegram output: Markdown → HTML (bold, code, code blocks), splitting never breaks code fences, plain-text fallback, typing indicator stays on during long answers.
- `/setprompt` limit and the used limit are now the same (`PROMPT_MAX_CHARS`). Old messages are pruned (`KEEP_MESSAGES`).
- **Command menu**: typing `/` shows every command with a description (and the blue menu button works). It is published automatically at startup for the owner; list lives in `core/commands.py`; disable with `SET_BOT_COMMANDS=false`.
- **Faster translation** (when `/translation on`): chunks are translated in parallel over one persistent connection (a 4,300-char text: 1.5 s → 0.65 s in a local benchmark with 0.3 s server latency); if Google stops answering, translation is skipped for a while instead of stalling every message (was 20 s per chunk, now `TRANSLATE_TIMEOUT` once, then instant); text already in the target language is not sent; with a Cloud Translation key all chunks go in one request. Translation still adds two network round trips per message — keeping it off (default) is the fastest, Gemini answers Persian directly.
- Global error handler, optional `.env` loading (python-dotenv), unwritable `DATA_DIR` falls back to `./data`.

## Important note about failover on quota (429)
Gemini quota is **per Google Cloud project and model**. Extra keys only add capacity if they belong to *different projects*. Google's terms may restrict using multiple projects to get around quotas — check them for your use. You can turn this behaviour off with `FAILOVER_ON_429=false`; server/network failover keeps working.

## Install
1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` (or set the variables in your host, e.g. Railway) and fill `TELEGRAM_TOKEN`, `OWNER_ID`, `GEMINI_API_KEYS`.
3. `python bot.py`

Keep `DATA_DIR` on persistent storage (the retry queue and history live there).

## Variables
Original (unchanged): `TELEGRAM_TOKEN`, `OWNER_ID`, `GEMINI_API_KEYS`/`GEMINI_API_KEY`, `GEMINI_MODEL_CHAT|STRONG|LIGHT|CODE`, `DATA_DIR`, `HISTORY_TURNS`, `MAX_OUTPUT_TOKENS`, `LOG_LEVEL`, `TRANSLATE_ENABLED`, `GOOGLE_TRANSLATE_API_KEY`.

New (defaults live in `config.py`; env override optional):

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_MODEL_FALLBACKS` | – | extra fallback models |
| `FAILOVER_ON_429` | true | switch key/model on quota errors |
| `MAX_WAIT_SECONDS` | 90 | max wait for a key to recover before queueing |
| `MAX_ATTEMPTS` | 16 | max calls per request |
| `MAX_CONTINUATIONS` | 3 | auto-continue rounds for cut answers |
| `TEMPERATURE` | 0.7 | sampling temperature |
| `REQUEST_TIMEOUT_MS` / `SDK_RETRY_ATTEMPTS` | 25000 / 1 | per-call timeout / SDK retries |
| `COOLDOWN_429` / `_DAILY` / `_5XX` / `_NETWORK` / `_AUTH` | 60 / 1800 / 10 / 10 / 600 | seconds |
| `PENDING_TTL_SECONDS` / `QUEUE_POLL_SECONDS` | 21600 / 20 | queue expiry / poll interval |
| `MEMORY_MAX_CHARS` / `MEMORY_LIMIT` | 1800 / 30 | long-term memory sent per request |
| `PROMPT_MAX_CHARS` | 3000 | custom prompt limit |
| `KEEP_MESSAGES` | 500 | messages kept per user |
| `TRANSLATE_CHUNK_CHARS` | 1000 | translation chunk size |
| `TRANSLATE_TIMEOUT` / `TRANSLATE_WORKERS` | 5 / 4 | seconds per request / parallel chunks |
| `TRANSLATE_COOLDOWN` / `TRANSLATE_COOLDOWN_MAX` | 30 / 300 | seconds translation is skipped after a failure (doubles up to max) |
| `TELEGRAM_CHUNK_CHARS` / `TYPING_INTERVAL` | 3500 / 4 | message split size / typing refresh |
| `SET_BOT_COMMANDS` | true | publish the "/" command menu |

Per-mode output budgets and thinking hints are in `core/router.py` (`MODES`); they multiply `MAX_OUTPUT_TOKENS`.

## Commands
`/start /help /chat /ask /code /creative /translate /summarize /explain /brainstorm /reset /history /remember /memories /forget ID /prompt /setprompt TEXT /model /settings /translation on|off /keys /stats /usage` + new `/queue /clearqueue`.

## Tests
`pip install pytest pytest-asyncio && pytest` (tests: router, formatting, translation chunking, failover, waiting, continuation, queue, migration).
