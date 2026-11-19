import hashlib
import html
import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


class GoogleTranslator:
    """Translator with a no-key Google web endpoint fallback.

    If GOOGLE_TRANSLATE_API_KEY is configured, use the official Cloud Translation
    v2 API. Otherwise use Google's public web translation endpoint (unofficial,
    best-effort and subject to throttling/changes).
    """
    CLOUD_ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
    WEB_ENDPOINT = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, api_key, enabled, cache):
        self.api_key = (api_key or "").strip()
        self.enabled = enabled
        self.cache = cache

    def _cache_key(self, text, source, target):
        raw = f"{source}|{target}|{text}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def translate(self, text, source, target):
        if not self.enabled or source == target or not text.strip():
            return text
        import asyncio
        return await asyncio.to_thread(self._translate_sync, text, source, target)

    def _translate_sync(self, text, source, target):
        key = self._cache_key(text, source, target)
        cached = self.cache.get_cached_translation(key)
        if cached is not None:
            return cached
        try:
            if self.api_key:
                translated = self._translate_cloud(text, source, target)
            else:
                translated = self._translate_web(text, source, target)
            if translated:
                self.cache.cache_translation(key, translated)
                return translated
        except Exception:
            log.exception("Translation failed; continuing with original text")
        # Translation is an enhancement, not a reason to stop the bot.
        return text

    def _translate_cloud(self, text, source, target):
        payload = urllib.parse.urlencode({
            "q": text, "source": source, "target": target,
            "format": "text", "key": self.api_key,
        }).encode("utf-8")
        req = urllib.request.Request(self.CLOUD_ENDPOINT, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return html.unescape(data["data"]["translations"][0]["translatedText"])

    def _translate_web(self, text, source, target):
        # Unofficial endpoint used by Google's web translator; no API key required.
        params = urllib.parse.urlencode({
            "client": "gtx", "sl": source, "tl": target,
            "dt": "t", "q": text,
        })
        req = urllib.request.Request(
            f"{self.WEB_ENDPOINT}?{params}",
            headers={"User-Agent": "Mozilla/5.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        chunks = data[0] if isinstance(data, list) and data else []
        translated = "".join(
            chunk[0] for chunk in chunks
            if isinstance(chunk, list) and chunk and isinstance(chunk[0], str)
        )
        if not translated:
            raise ValueError("Google web translator returned an empty response")
        return translated
