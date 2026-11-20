"""Telegram output helpers: safe message splitting and Markdown -> Telegram HTML."""
import html
import re

_CODE_BLOCK = re.compile(r"```[A-Za-z0-9_+\-#.]*[ \t]*\n?(.*?)```", re.S)
_CODE_SPAN = re.compile(r"(`[^`\n]+`)")
_HEADER = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.M)
_BULLET = re.compile(r"^([ \t]*)[\*\-][ \t]+", re.M)
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def split_message(text, limit=3500):
    """Split on line boundaries; never leaves a ``` fence open across chunks."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    hard = max(50, limit - 20)
    lines = []
    for ln in text.split("\n"):
        while len(ln) > hard:
            lines.append(ln[:hard])
            ln = ln[hard:]
        lines.append(ln)
    chunks, cur, size, in_fence = [], [], 0, False
    for ln in lines:
        add = len(ln) + 1
        if cur and size + add + (4 if in_fence else 0) > limit:
            chunks.append("\n".join(cur) + ("\n```" if in_fence else ""))
            cur, size = (["```"], 4) if in_fence else ([], 0)
        cur.append(ln)
        size += add
        if ln.strip().startswith("```"):
            in_fence = not in_fence
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


def _inline(text):
    parts = _CODE_SPAN.split(text)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append("<code>" + html.escape(part[1:-1], quote=False) + "</code>")
            continue
        part = html.escape(part, quote=False)
        part = _HEADER.sub(lambda m: "<b>" + m.group(1).replace("**", "") + "</b>", part)
        part = _BULLET.sub(r"\1• ", part)
        part = _BOLD.sub(r"<b>\1</b>", part)
        out.append(part)
    return "".join(out)


def to_html(markdown):
    """Convert the Markdown subset Gemini produces into Telegram-safe HTML."""
    markdown = markdown or ""
    out, pos = [], 0
    for m in _CODE_BLOCK.finditer(markdown):
        out.append(_inline(markdown[pos:m.start()]))
        out.append("<pre>" + html.escape(m.group(1).strip("\n"), quote=False) + "</pre>")
        pos = m.end()
    out.append(_inline(markdown[pos:]))
    return "".join(out)
