import asyncio
import hashlib
import html
import json
import logging
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import httpx

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"(```.*?```)", re.S)
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_LATIN = re.compile(r"[A-Za-z]")


def persian_share(text):
    """Share of Arabic-script letters among all Arabic+Latin letters (0..1)."""
    ar = len(_ARABIC.findall(text))
    la = len(_LATIN.findall(text))
    total = ar + la
    return ar / total if total else None


class GoogleTranslator:
    """Translator with a no-key Google web endpoint fallback.

    If GOOGLE_TRANSLATE_API_KEY is configured, use the official Cloud Translation
    v2 API (all chunks of a text go in ONE request). Otherwise use Google's public
    web endpoint (unofficial, best-effort).

    Speed design (v3.2):
      * persistent keep-alive HTTP connection (no new TCP/TLS handshake per chunk),
      * chunks are translated in parallel,
      * short timeouts + a circuit breaker: if Google stops answering, translation is
        skipped for a while instead of stalling every message,
      * text that is already in the target language is not sent at all,
      * fenced code blocks are never translated.
    """
    CLOUD_ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
    WEB_ENDPOINT = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, api_key, enabled, cache, chunk_chars=1000, timeout=8.0,
                 workers=4, cooldown=30, cooldown_max=300):
        self.api_key = (api_key or "").strip()
        self.enabled = enabled
        self.cache = cache
        self.chunk_chars = max(200, int(chunk_chars))
        self.cooldown = max(1, cooldown)
        self.cooldown_max = max(self.cooldown, cooldown_max)
        self._fail_until = 0.0
        self._fail_streak = 0
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="translate")
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(3.0, timeout)),
            limits=httpx.Limits(max_connections=max(2, workers), max_keepalive_connections=max(2, workers)),
            transport=httpx.HTTPTransport(retries=1),   # transparently redo stale keep-alive connections
            headers={"User-Agent": "Mozilla/5.0"},
        )

    def close(self):
        self._pool.shutdown(wait=False)
        self._http.close()

    # ------------------------------------------------------------ public API
    @staticmethod
    def already_in_target(text, source, target):
        """True when sending the text would be wasted time."""
        if source != "auto":
            return False
        share = persian_share(text)
        if share is None:                      # digits / emoji only
            return True
        return share < 0.10 if target == "en" else share > 0.60

    async def translate(self, text, source, target, enabled=None):
        active = self.enabled if enabled is None else enabled
        if not active or source == target or not (text or "").strip():
            return text
        if self.already_in_target(text, source, target):
            return text
        return await asyncio.to_thread(self._translate_text, text, source, target)

    # ------------------------------------------------------------- chunking
    def _split_prose(self, text):
        """-> list of (chunk, separator_that_followed_it). Chunks are <= chunk_chars."""
        limit = self.chunk_chars
        pieces = []  # (text, sep_after): "\n" real line break, " " split at a space, "" hard cut
        lines = text.split("\n")
        for li, line in enumerate(lines):
            rest = line
            while len(rest) > limit:
                found = max(rest.rfind(". ", 0, limit), rest.rfind("؟ ", 0, limit),
                            rest.rfind("! ", 0, limit), rest.rfind("؛ ", 0, limit),
                            rest.rfind(" ", 0, limit))
                if found > limit // 3:
                    pieces.append((rest[:found + 1].rstrip(" "), " "))
                    rest = rest[found + 1:].lstrip(" ")
                else:
                    pieces.append((rest[:limit], ""))
                    rest = rest[limit:]
            pieces.append((rest, "\n" if li < len(lines) - 1 else ""))
        chunks, cur, cur_sep = [], "", ""
        for part, sep in pieces:
            if cur and len(cur) + len(cur_sep) + len(part) > limit:
                chunks.append((cur, cur_sep))
                cur, cur_sep = part, sep
            else:
                cur = part if not cur else cur + cur_sep + part
                cur_sep = sep
        if cur or not chunks:
            chunks.append((cur, cur_sep))
        return chunks

    def _translate_text(self, text, source, target):
        segments = _FENCE_RE.split(text)
        plan, all_chunks = [], []
        for index, segment in enumerate(segments):
            if index % 2 == 1 or not segment.strip():       # code fence / blank: keep as is
                plan.append(None)
                continue
            lead = segment[: len(segment) - len(segment.lstrip())]
            trail = segment[len(segment.rstrip()):]
            chunks = self._split_prose(segment.strip())
            plan.append((lead, trail, chunks))
            all_chunks.extend(c for c, _ in chunks)
        done = self._translate_many(list(dict.fromkeys(all_chunks)), source, target)
        out = []
        for segment, item in zip(segments, plan):
            if item is None:
                out.append(segment)
                continue
            lead, trail, chunks = item
            body = "".join(done.get(c, c) + sep for c, sep in chunks[:-1]) + done.get(chunks[-1][0], chunks[-1][0])
            out.append(lead + body + trail)
        return "".join(out)

    # ----------------------------------------------------- breaker + batching
    def _breaker_open(self):
        return time.monotonic() < self._fail_until

    def _record(self, ok):
        with self._lock:
            if ok:
                self._fail_streak = 0
                return
            now = time.monotonic()
            if now >= self._fail_until:                    # first failure since the breaker last closed
                self._fail_streak += 1
                wait = min(self.cooldown * 2 ** (self._fail_streak - 1), self.cooldown_max)
                self._fail_until = now + wait
                log.warning("Translation service failing; skipping translation for %ds", wait)

    def _cache_key(self, text, source, target):
        return hashlib.sha256(f"{source}|{target}|{text}".encode("utf-8")).hexdigest()

    def _translate_many(self, chunks, source, target):
        results, missing = {}, []
        for chunk in chunks:
            cached = self.cache.get_cached_translation(self._cache_key(chunk, source, target))
            if cached is not None:
                results[chunk] = cached
            else:
                missing.append(chunk)
        if not missing or self._breaker_open():
            return results                                  # callers fall back to the original text
        if self.api_key:
            translated = self._translate_cloud_many(missing, source, target)
        else:
            translated = list(self._pool.map(lambda c: self._web_one(c, source, target), missing))
        for chunk, value in zip(missing, translated):
            if value:
                results[chunk] = value
                self.cache.cache_translation(self._cache_key(chunk, source, target), value)
        return results

    def _web_one(self, chunk, source, target):
        if self._breaker_open():
            return None
        try:
            value = self._translate_web(chunk, source, target)
            self._record(True)
            return value
        except Exception as exc:
            self._record(False)
            log.warning("Translation request failed (%s: %s)", type(exc).__name__, str(exc)[:120])
            return None

    # ---------------------------------------------------------------- calls
    def _translate_cloud_many(self, chunks, source, target):
        """One POST per batch (many `q` values) instead of one request per chunk."""
        batches, cur, size = [], [], 0
        for chunk in chunks:
            if cur and (len(cur) >= 100 or size + len(chunk) > 20000):
                batches.append(cur)
                cur, size = [], 0
            cur.append(chunk)
            size += len(chunk)
        if cur:
            batches.append(cur)

        def run(batch):
            try:
                result = self._translate_cloud(batch, source, target)
                self._record(True)
                return result
            except Exception as exc:
                self._record(False)
                log.warning("Cloud translation failed (%s: %s)", type(exc).__name__, str(exc)[:120])
                return [None] * len(batch)

        out = []
        for part in self._pool.map(run, batches):
            out.extend(part)
        return out

    def _translate_cloud(self, texts, source, target):
        fields = [("q", t) for t in texts] + [("target", target), ("format", "text"), ("key", self.api_key)]
        if source and source != "auto":
            fields.append(("source", source))
        response = self._http.post(self.CLOUD_ENDPOINT, content=urllib.parse.urlencode(fields).encode("utf-8"),
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        response.raise_for_status()
        items = response.json()["data"]["translations"]
        if len(items) != len(texts):
            raise ValueError("Cloud translation returned an unexpected number of segments")
        return [html.unescape(i["translatedText"]) for i in items]

    def _translate_web(self, text, source, target):
        # Unofficial endpoint used by Google's web translator; no API key required.
        response = self._http.get(self.WEB_ENDPOINT, params={
            "client": "gtx", "sl": source or "auto", "tl": target, "dt": "t", "q": text})
        response.raise_for_status()
        data = response.json()
        pieces = data[0] if isinstance(data, list) and data else []
        translated = "".join(
            p[0] for p in pieces if isinstance(p, list) and p and isinstance(p[0], str))
        if not translated:
            raise ValueError("Google web translator returned an empty response")
        return translated
