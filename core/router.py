# Deterministic hybrid routing: explicit slash command wins; otherwise lightweight
# keyword rules are used. No extra Gemini classifier call is made.
MODES = {
    "chat": {"model": "chat", "system": "Answer naturally, directly, and concisely."},
    "ask": {"model": "strong", "system": "Answer the question accurately. State uncertainty when needed."},
    "code": {"model": "code", "system": "Act as a senior software engineer. Give correct, runnable code and concise explanations."},
    "creative": {"model": "strong", "system": "Be creative, original, and follow the requested format."},
    "translate": {"model": "light", "system": "Translate faithfully. Preserve meaning, tone, names, and formatting. Return only the translation."},
    "summarize": {"model": "light", "system": "Summarize only the supplied content. Keep essential facts and remove repetition."},
    "explain": {"model": "strong", "system": "Teach clearly, step by step, using simple examples when useful."},
    "brainstorm": {"model": "strong", "system": "Generate distinct, practical ideas. Avoid repetitive suggestions."},
}

RULES = {
    "code": ("کد", "پایتون", "python", "javascript", "debug", "باگ", "برنامه نویسی", "برنامه‌نویسی", "sql", "api"),
    "translate": ("ترجمه کن", "ترجمه به", "به انگلیسی", "به فارسی", "translate"),
    "summarize": ("خلاصه کن", "خلاصه‌سازی", "خلاصه سازی", "summarize"),
    "explain": ("توضیح بده", "یاد بده", "مرحله به مرحله", "چیست", "چیه", "explain"),
    "brainstorm": ("ایده بده", "ایده‌پردازی", "brainstorm", "چند ایده"),
}


def resolve_mode(text, selected="chat"):
    if selected and selected != "chat":
        return selected
    lowered = text.lower()
    for mode, terms in RULES.items():
        if any(term in lowered for term in terms):
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
