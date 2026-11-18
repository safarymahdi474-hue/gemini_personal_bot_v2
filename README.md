# Gemini Personal Telegram Bot — v2 / Stage 1

## Included in this stage
- Private bot: only `OWNER_ID` can use it.
- Hybrid deterministic routing: explicit slash command wins; otherwise keyword rules select a mode. No extra model call is used for classification.
- Commands: `/chat`, `/ask`, `/code`, `/creative`, `/translate`, `/summarize`, `/explain`, `/brainstorm`.
- Separate model settings for chat / strong / light / code.
- SQLite conversation history and persistent per-user memory commands.
- `/remember`, `/memories`, `/forget ID`.
- `/reset` clears conversation history but keeps saved long-term memories.
- Optional Google Cloud Translation Basic v2 layer, with SQLite cache.
- Token controls: bounded history, bounded saved-memory context, concise system prompts, max output token setting.
- Multi-project availability failover for transient server/network failures. HTTP 429 never triggers project switching.

## Important translation note
The translator uses the official Google Cloud Translation Basic v2 API, not the consumer Google Translate website.
It requires a Cloud Translation API key and a project with the API enabled. Translation is disabled by default.
When enabled, each uncached message is translated Persian -> English and each uncached response English -> Persian.
This adds two translation requests and latency; it does not guarantee lower Gemini token use.
Google Cloud Translation bills by characters. The pricing page currently lists the first 500,000 NMT text characters per month as free, then paid usage. Check current pricing and quotas before enabling.
Translation results are cached to avoid repeated charges for identical text.

## Install
1. Create a virtual environment.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set `TELEGRAM_TOKEN`, `OWNER_ID`, and Gemini key(s).
4. Export variables from `.env` using your host's environment-variable settings.
5. Run `python bot.py`.

Keep `/data` on persistent storage. Existing `chat.db` from the earlier bot is not automatically migrated because the new schema and memory model differ. Back up old data before switching.

## Translation setup
- Enable Cloud Translation API in Google Cloud Console.
- Create an API key restricted to the Cloud Translation API.
- Set `TRANSLATE_ENABLED=true` and `GOOGLE_TRANSLATE_API_KEY=...`.
- The `/translation on|off` command toggles translation at runtime. A key must be configured first.

## Commands
`/start`, `/help`, `/chat`, `/ask`, `/code`, `/creative`, `/translate`, `/summarize`, `/explain`, `/brainstorm`, `/reset`, `/history`, `/remember`, `/memories`, `/forget ID`, `/prompt`, `/setprompt TEXT`, `/model`, `/settings`, `/translation on|off`, `/keys`, `/stats`, `/usage`.

## Notes / next stages
This is Stage 1, not the full feature roadmap. Next: automatic memory candidate review, media/file handling, web search integration, voice transcription/speech, scheduled tasks, agent workflows, backup/restore, and more robust token accounting.
Do not configure multiple keys from one Google Cloud project expecting more quota: Gemini quota is project-scoped. 429 responses are respected and are not bypassed by switching projects.
