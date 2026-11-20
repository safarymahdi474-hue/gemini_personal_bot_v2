import re
from dataclasses import dataclass

# Deterministic hybrid routing: explicit slash command wins; otherwise lightweight
# keyword rules are used. No extra Gemini classifier call is made.
#
# "tokens"  : multiplier applied to MAX_OUTPUT_TOKENS for this mode.
# "thinking": thinking level hint ("" = model default). Falls back automatically
#             if the model/SDK rejects it.
MODES = {
    "chat": {"model": "chat", "tokens": 1.0, "thinking": "low",
             "system": "Answer naturally, directly, and concisely."},
    "ask": {"model": "strong", "tokens": 1.5, "thinking": "",
            "system": "Answer the question accurately. State uncertainty when needed."},
    "code": {"model": "code", "tokens": 3.0, "thinking": "",
             "system": "Act as a senior software engineer. Give correct, runnable code and concise explanations."},
    "creative": {"model": "strong", "tokens": 2.5, "thinking": "low",
                 "system": "Be creative, original, and follow the requested format."},
    "translate": {"model": "light", "tokens": 2.0, "thinking": "low",
                  "system": "Translate faithfully. Preserve meaning, tone, names, and formatting. Return only the translation."},
    "summarize": {"model": "light", "tokens": 1.0, "thinking": "low",
                  "system": "Summarize only the supplied content. Keep essential facts and remove repetition."},
    "explain": {"model": "strong", "tokens": 2.0, "thinking": "",
                "system": "Teach clearly, step by step, using simple examples when useful."},
    "brainstorm": {"model": "strong", "tokens": 1.5, "thinking": "low",
                   "system": "Generate distinct, practical ideas. Avoid repetitive suggestions."},
}

_RULE_TERMS = {
    "code": ("کد", "کدها", "کدنویسی", "کد نویسی", "پایتون", "python", "javascript", "typescript",
             "debug", "باگ", "برنامه نویسی", "برنامه‌نویسی", "sql", "api", "script", "اسکریپت"),
    "translate": ("ترجمه کن", "ترجمه کنید", "ترجمه‌اش کن", "به انگلیسی ترجمه", "به فارسی ترجمه",
                  "translate"),
    "summarize": ("خلاصه کن", "خلاصه‌اش کن", "خلاصه‌سازی", "خلاصه سازی", "summarize"),
    "explain": ("توضیح بده", "توضیح بدید", "یاد بده", "مرحله به مرحله", "چیست", "چیه", "explain"),
    "brainstorm": ("ایده بده", "ایده‌پردازی", "brainstorm", "چند ایده"),
}


def _compile(terms):
    # (?<!\w)/(?!\w) = whole-word match, works for Persian too, so "کد" no longer
    # matches inside "کدام"/"کدوم" and "api" no longer matches inside "capital".
    body = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)", re.IGNORECASE)


RULES = {mode: _compile(terms) for mode, terms in _RULE_TERMS.items()}


def resolve_mode(text, selected="chat"):
    if selected and selected != "chat" and selected in MODES:
        return selected
    for mode, pattern in RULES.items():
        if pattern.search(text or ""):
            return mode
    return "chat"


def model_for(mode, settings):
    key = MODES.get(mode, MODES["chat"])["model"]
    return {
        "chat": settings.chat_model,
        "strong": settings.strong_model,
        "light": settings.light_model,
        "code": settings.code_model,
    }[key]


@dataclass(frozen=True)
class ModeOptions:
    max_tokens: int
    thinking: str


def options_for(mode, settings):
    cfg = MODES.get(mode, MODES["chat"])
    return ModeOptions(
        max_tokens=max(64, int(settings.max_output_tokens * cfg["tokens"])),
        thinking=cfg["thinking"],
    )
