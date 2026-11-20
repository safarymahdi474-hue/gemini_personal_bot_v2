import os

try:  # optional: load a local .env file when python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from dataclasses import dataclass


def _int(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}.")


def _float(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a number, got {raw!r}.")


def _bool(name, default):
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _list(name):
    raw = os.getenv(name, "").strip()
    return tuple(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))


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
    # ---- original variables (unchanged names and defaults) ----
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    owner_id: int = _int("OWNER_ID", 0)
    gemini_keys: tuple = tuple(_keys())
    data_dir: str = os.getenv("DATA_DIR", "/data")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    translate_enabled: bool = _bool("TRANSLATE_ENABLED", False)
    google_translate_api_key: str = os.getenv("GOOGLE_TRANSLATE_API_KEY", "")
    history_turns: int = _int("HISTORY_TURNS", 8)
    max_output_tokens: int = _int("MAX_OUTPUT_TOKENS", 900)
    chat_model: str = os.getenv("GEMINI_MODEL_CHAT", "gemini-3.1-flash-lite")
    strong_model: str = os.getenv("GEMINI_MODEL_STRONG", "gemini-3.8-flash")
    light_model: str = os.getenv("GEMINI_MODEL_LIGHT", "gemini-3.1-flash-lite")
    code_model: str = os.getenv("GEMINI_MODEL_CODE", "gemini-3.8-flash")

    # ---- new in v3: values decided in code, env override is optional ----
    fallback_models: tuple = _list("GEMINI_MODEL_FALLBACKS")
    failover_on_429: bool = _bool("FAILOVER_ON_429", True)
    max_wait_seconds: int = _int("MAX_WAIT_SECONDS", 90)
    max_attempts: int = _int("MAX_ATTEMPTS", 16)
    max_continuations: int = _int("MAX_CONTINUATIONS", 3)
    temperature: float = _float("TEMPERATURE", 0.7)
    request_timeout_ms: int = _int("REQUEST_TIMEOUT_MS", 25000)
    sdk_retry_attempts: int = _int("SDK_RETRY_ATTEMPTS", 1)
    cooldown_429: int = _int("COOLDOWN_429", 60)
    cooldown_daily: int = _int("COOLDOWN_DAILY", 1800)
    cooldown_5xx: int = _int("COOLDOWN_5XX", 10)
    cooldown_network: int = _int("COOLDOWN_NETWORK", 10)
    cooldown_auth: int = _int("COOLDOWN_AUTH", 600)
    pending_ttl_seconds: int = _int("PENDING_TTL_SECONDS", 6 * 3600)
    queue_poll_seconds: int = _int("QUEUE_POLL_SECONDS", 20)
    memory_max_chars: int = _int("MEMORY_MAX_CHARS", 1800)
    memory_limit: int = _int("MEMORY_LIMIT", 30)
    prompt_max_chars: int = _int("PROMPT_MAX_CHARS", 3000)
    keep_messages: int = _int("KEEP_MESSAGES", 500)
    translate_chunk_chars: int = _int("TRANSLATE_CHUNK_CHARS", 1000)
    translate_timeout: float = _float("TRANSLATE_TIMEOUT", 5.0)
    translate_workers: int = _int("TRANSLATE_WORKERS", 4)
    translate_cooldown: int = _int("TRANSLATE_COOLDOWN", 30)
    translate_cooldown_max: int = _int("TRANSLATE_COOLDOWN_MAX", 300)
    telegram_chunk_chars: int = _int("TELEGRAM_CHUNK_CHARS", 3500)
    typing_interval: float = _float("TYPING_INTERVAL", 4.0)
    set_bot_commands: bool = _bool("SET_BOT_COMMANDS", True)
    # Modes whose text must never go through the translation layer
    # (code would be mangled; /translate would be translated twice).
    no_translate_modes: tuple = ("code", "translate")


def validate(s):
    if not s.telegram_token:
        raise RuntimeError("TELEGRAM_TOKEN is required.")
    if s.owner_id <= 0:
        raise RuntimeError("Set OWNER_ID to your numeric Telegram user ID.")
    if not s.gemini_keys:
        raise RuntimeError("Set GEMINI_API_KEY or GEMINI_API_KEYS.")


settings = Settings()
