import hashlib
import json
import urllib.parse
import urllib.request


class GoogleTranslator:
    """Google Cloud Translation Basic v2. Requires a Cloud Translation API key.
    Translation is optional and cached locally to avoid paying for repeated strings.
    """
    ENDPOINT = "https://translation.googleapis.com/language/translate/v2"

    def __init__(self, api_key, enabled, cache):
        self.api_key = api_key
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
        payload = urllib.parse.urlencode({
            "q": text,
            "source": source,
            "target": target,
            "format": "text",
            "key": self.api_key,
        }).encode("utf-8")
        req = urllib.request.Request(self.ENDPOINT, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        translated = data["data"]["translations"][0]["translatedText"]
        self.cache.cache_translation(key, translated)
        return translated
