import pytest
from core.router import resolve_mode
from core.fmt import split_message, to_html
from core.translator import GoogleTranslator


@pytest.mark.parametrize("text,expected", [
    ("کدوم کشور بزرگتره؟", "chat"),
    ("کدام شهر بهتره", "chat"),
    ("what is the capital of France", "chat"),
    ("a therapist said hi", "chat"),
    ("لطفا به فارسی جواب بده", "chat"),
    ("سلام خوبی؟", "chat"),
    ("این کد رو دیباگ کن", "code"),
    ("یه تابع python بنویس", "code"),
    ("how do I call this API", "code"),
    ("این متن رو ترجمه کن", "translate"),
    ("لطفاً خلاصه کن", "summarize"),
    ("فتوسنتز چیه؟", "explain"),
    ("چند ایده بده", "brainstorm"),
])
def test_router_whole_words(text, expected):
    assert resolve_mode(text, "chat") == expected


def test_sticky_mode_wins():
    assert resolve_mode("هر چی", "creative") == "creative"


def test_split_keeps_fences_balanced():
    code = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(400)) + "\n```"
    chunks = split_message("intro\n" + code, 1000)
    assert len(chunks) > 1 and all(len(c) <= 1000 for c in chunks)
    assert all(c.count("```") % 2 == 0 for c in chunks)


def test_to_html_escapes_and_formats():
    out = to_html("# T\n* **bold** a<b\n`x<y`\n```py\nif a<b: pass\n```")
    assert "<b>T</b>" in out and "• <b>bold</b> a&lt;b" in out
    assert "<code>x&lt;y</code>" in out and "<pre>if a&lt;b: pass</pre>" in out


class Cache:
    def __init__(self): self.d = {}
    def get_cached_translation(self, k): return self.d.get(k)
    def cache_translation(self, k, v): self.d[k] = v


def distinct_persian(n):
    return " ".join(f"جمله شماره {i} برای آزمایش ترجمه است." for i in range(n))


async def test_translator_chunks_and_protects_code():
    t = GoogleTranslator("", True, Cache(), chunk_chars=300)
    seen = []
    t._translate_web = lambda text, s, tg: (seen.append(text), f"[{text}]")[1]
    text = distinct_persian(60) + "\n```python\nprint('hi')\n```\nپایان"
    out = await t.translate(text, "auto", "en")
    assert "```python\nprint('hi')\n```" in out          # code untouched
    assert all(len(x) <= 300 for x in seen) and len(seen) > 5   # chunked
    assert "print('hi')" not in "".join(seen)             # code never sent out
    assert await t.translate("x", "fa", "fa") == "x"
    assert await t.translate("x", "auto", "en", enabled=False) == "x"
