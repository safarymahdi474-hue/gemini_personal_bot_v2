import os
from dataclasses import dataclass


def _keys():
    raw = os.getenv("GEMINI_API_KEYS", "").strip()
    if raw:
        values = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        one = os.getenv("GEMINI_API_KEY", "").strip()
        values = [one] if one else []
    return list(dict.fromkeys(values))


@dataclass(frozen=True)
class Settings:
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    owner_id: int = int(os.getenv("OWNER_ID", "0") or 0)
    gemini_keys: tuple = tuple(_keys())
    data_dir: str = os.getenv("DATA_DIR", "/data")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    translate_enabled: bool = os.getenv("TRANSLATE_ENABLED", "false").lower() == "true"
    google_translate_api_key: str = os.getenv("GOOGLE_TRANSLATE_API_KEY", "")
    history_turns: int = int(os.getenv("HISTORY_TURNS", "8"))
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS", "900"))
    chat_model: str = os.getenv("GEMINI_MODEL_CHAT", "gemini-3.8-flash")
    strong_model: str = os.getenv("GEMINI_MODEL_STRONG", "gemini-3.8-flash")
    light_model: str = os.getenv("GEMINI_MODEL_LIGHT", "gemini-3.1-flash-lite")
    code_model: str = os.getenv("GEMINI_MODEL_CODE", "gemini-3.8-flash")


settings = Settings()
if not settings.telegram_token:
    raise RuntimeError("TELEGRAM_TOKEN is required.")
if settings.owner_id <= 0:
    raise RuntimeError("Set OWNER_ID to your numeric Telegram user ID.")
if not settings.gemini_keys:
    raise RuntimeError("Set GEMINI_API_KEY or GEMINI_API_KEYS.")
if settings.translate_enabled and not settings.google_translate_api_key:
    raise RuntimeError("TRANSLATE_ENABLED=true requires GOOGLE_TRANSLATE_API_KEY.")
