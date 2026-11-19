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
        # Short bounded retries prevent Telegram from staying in "typing" for minutes.
        http_options = types.HttpOptions(
            timeout=25000,
            retry_options=types.HttpRetryOptions(
                attempts=2,
                initial_delay=0.5,
                max_delay=2.0,
                http_status_codes=[408, 500, 502, 503, 504],
            ),
        )
        self.states = [
            KeyState(f"Project-{i+1}", genai.Client(api_key=k, http_options=http_options))
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

        # Try one healthy project first; only move to another project for auth/network/server errors.
        primary = candidates[0]
        fallback = self.settings.light_model if self.settings.light_model != model else None
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=self.settings.max_output_tokens,
            temperature=0.7,
        )
        last_exc = None
        for candidate_model in ([model, fallback] if fallback else [model]):
            try:
                response = await primary.client.aio.models.generate_content(
                    model=candidate_model, contents=contents, config=config
                )
                answer = response.text or ""
                if candidate_model != model:
                    log.warning("Fallback model succeeded: %s", candidate_model)
                return answer, primary.label
            except Exception as exc:
                last_exc = exc
                status = status_code(exc)
                if status == 429:
                    primary.cooldown_until = time.monotonic() + 60
                    raise
                if status in (401, 403):
                    primary.disabled = True
                    # Try a different configured project only for invalid credentials/access.
                    for state in candidates[1:]:
                        if state.disabled or state.cooldown_until > time.monotonic():
                            continue
                        try:
                            response = await state.client.aio.models.generate_content(
                                model=candidate_model, contents=contents, config=config
                            )
                            return response.text or "", state.label
                        except Exception as next_exc:
                            last_exc = next_exc
                            if status_code(next_exc) == 429:
                                state.cooldown_until = time.monotonic() + 60
                                raise
                            if status_code(next_exc) in (401, 403):
                                state.disabled = True
                            else:
                                state.cooldown_until = time.monotonic() + 10
                    break
                if status is not None and 500 <= status <= 599:
                    primary.cooldown_until = time.monotonic() + 10
                    if candidate_model == model and fallback:
                        log.warning("Primary model returned %s; trying one fast fallback", status)
                        continue
                elif status is None and any(x in str(exc).lower() for x in ("timeout", "connection", "network", "transport")):
                    primary.cooldown_until = time.monotonic() + 10
                raise
        raise last_exc or RuntimeError("Gemini request failed.")

    def status(self):
        now = time.monotonic()
        return "\n".join(
            f"{s.label}: " + ("disabled" if s.disabled else
            f"cooldown {int(s.cooldown_until-now)}s" if s.cooldown_until > now else "ready")
            for s in self.states
        )
