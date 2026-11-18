import asyncio
import logging
import time
from dataclasses import dataclass
from google import genai
from google.genai import types

log = logging.getLogger(__name__)


def status_code(exc):
    for name in ("status_code", "code"):
        value = getattr(exc, name, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class KeyState:
    label: str
    client: object
    cooldown_until: float = 0.0
    disabled: bool = False


class GeminiService:
    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self.states = [
            KeyState(f"Project-{i+1}", genai.Client(api_key=k))
            for i, k in enumerate(settings.gemini_keys)
        ]
        self.index = 0
        self.lock = asyncio.Lock()

    async def _candidates(self):
        async with self.lock:
            now = time.monotonic()
            n = len(self.states)
            ordered = [self.states[(self.index+i) % n] for i in range(n)]
            self.index = (self.index + 1) % n
            return [s for s in ordered if not s.disabled and s.cooldown_until <= now]

    async def generate(self, user_id, model, contents, system_instruction):
        candidates = await self._candidates()
        if not candidates:
            raise RuntimeError("All configured Gemini projects are unavailable.")
        last_exc = None
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=self.settings.max_output_tokens,
        )
        for state in candidates:
            try:
                response = await state.client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
                answer = response.text or ""
                return answer, state.label
            except Exception as exc:
                last_exc = exc
                status = status_code(exc)
                if status == 429:
                    # Respect rate/quota limits: do not switch projects after 429.
                    state.cooldown_until = time.monotonic() + 60
                    raise
                if status in (401, 403):
                    state.disabled = True
                    continue
                if status is not None and 500 <= status <= 599:
                    state.cooldown_until = time.monotonic() + 15
                    continue
                msg = str(exc).lower()
                if status is None and any(x in msg for x in ("timeout", "connection", "network", "transport")):
                    state.cooldown_until = time.monotonic() + 15
                    continue
                raise
        raise last_exc or RuntimeError("No Gemini project available.")

    def status(self):
        now = time.monotonic()
        return "\n".join(
            f"{s.label}: " + ("disabled" if s.disabled else
            f"cooldown {int(s.cooldown_until-now)}s" if s.cooldown_until > now else "ready")
            for s in self.states
        )
