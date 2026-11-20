import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

CONTINUE_PROMPT = (
    "Continue exactly from where you stopped. Do not repeat anything and do not add an introduction."
)
_RETRY_IN = re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.I)
_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", re.I)


class AllUnavailable(RuntimeError):
    """Every key/model pair is cooling down for longer than the request may wait."""

    def __init__(self, wait=0.0, reason=""):
        super().__init__(reason or "All configured Gemini keys/models are temporarily unavailable.")
        self.wait = wait
        self.reason = reason


def status_code(exc):
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _is_network_error(exc):
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(x in name for x in ("timeout", "connect", "network", "transport", "remoteprotocol", "readerror")) \
        or any(x in text for x in ("timed out", "connection", "temporarily unavailable"))


def parse_retry_delay(text):
    for pattern in (_RETRY_IN, _RETRY_DELAY):
        m = pattern.search(text or "")
        if m:
            return float(m.group(1))
    return None


@dataclass
class KeyState:
    label: str
    client: object
    until: dict = field(default_factory=dict)  # model -> monotonic time it is usable again
    key_until: float = 0.0                      # whole key (network / bad key)
    ok: int = 0
    fail: int = 0

    def ready_at(self, model):
        return max(self.key_until, self.until.get(model, 0.0))


def _default_client_factory(api_key, http_options):
    return genai.Client(api_key=api_key, http_options=http_options)


class GeminiService:
    """Gemini caller with per-(key, model) cooldowns, automatic failover to other
    keys/models, waiting for recovery instead of failing, and automatic
    continuation when an answer is cut by the output-token limit."""

    def __init__(self, settings, db, client_factory=None):
        self.s = settings
        self.db = db
        self.index = 0
        self.thinking_ok = True
        self.failovers = 0
        factory = client_factory or _default_client_factory
        options = self._http_options()
        self.states = [
            KeyState(label=f"project-{i}", client=factory(key, options))
            for i, key in enumerate(settings.gemini_keys, 1)
        ]
        if not self.states:
            raise RuntimeError("No Gemini API key configured.")

    # ------------------------------------------------------------- setup
    def _http_options(self):
        try:
            retry = types.HttpRetryOptions(
                attempts=max(1, self.s.sdk_retry_attempts), initial_delay=0.5, max_delay=2.0,
                http_status_codes=[408, 500, 502, 503, 504],
            )
            return types.HttpOptions(timeout=self.s.request_timeout_ms, retry_options=retry)
        except Exception:  # very old SDK without retry options
            return types.HttpOptions(timeout=self.s.request_timeout_ms)

    def model_chain(self, model):
        chain = [model, *self.s.fallback_models, self.s.light_model]
        return list(dict.fromkeys(m for m in chain if m))

    def _rotated(self):
        n = len(self.states)
        order = [self.states[(self.index + i) % n] for i in range(n)]
        self.index = (self.index + 1) % n
        return order

    def any_ready(self, model):
        now = time.monotonic()
        return any(st.ready_at(m) <= now for st in self.states for m in self.model_chain(model))

    def _next_ready(self, models):
        return min(st.ready_at(m) for st in self.states for m in models)

    def _thinking(self, level):
        if not level or not self.thinking_ok:
            return None
        try:
            return types.ThinkingConfig(thinking_level=level.upper())
        except Exception:
            self.thinking_ok = False
            return None

    # ---------------------------------------------------------- one call
    async def _call(self, state, model, contents, system, max_tokens, thinking):
        kwargs = dict(system_instruction=system, max_output_tokens=max_tokens,
                      temperature=self.s.temperature)
        think = self._thinking(thinking)
        if think is not None:
            kwargs["thinking_config"] = think
        config = types.GenerateContentConfig(**kwargs)
        return await state.client.aio.models.generate_content(
            model=model, contents=contents, config=config)

    @staticmethod
    def _extract(resp):
        try:
            text = resp.text or ""
        except Exception:
            text = ""
        finish = ""
        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            fr = getattr(candidates[0], "finish_reason", None)
            finish = getattr(fr, "name", None) or (str(fr) if fr else "")
        return text, finish.split(".")[-1].upper()

    def _classify(self, state, model, exc):
        """Update cooldowns. Returns 'retry', 'retry_now' or 'fatal'."""
        s = self.s
        now = time.monotonic()
        status = status_code(exc)
        text = str(exc)
        low = text.lower()
        state.fail += 1
        if status == 429:
            delay = parse_retry_delay(text)
            if "perday" in low.replace(" ", "") or "per day" in low:
                cd = max(delay or 0, s.cooldown_daily)
            else:
                cd = (delay + 1) if delay else s.cooldown_429
            state.until[model] = now + min(max(cd, 3), 6 * 3600)
            return "retry" if s.failover_on_429 else "fatal"
        if status == 401:
            state.key_until = now + s.cooldown_auth
            return "retry"
        if status in (403, 404):
            state.until[model] = now + s.cooldown_auth
            return "retry"
        if status in (408, 500, 502, 503, 504) or (status and 500 <= status <= 599):
            state.until[model] = now + s.cooldown_5xx
            return "retry"
        if status == 400 and "thinking" in low:
            self.thinking_ok = False  # model rejected the hint: stop sending it
            state.fail -= 1
            return "retry_now"
        if status is None and _is_network_error(exc):
            state.key_until = now + s.cooldown_network
            return "retry"
        return "fatal"

    # --------------------------------------------------- failover + wait
    async def generate(self, user_id, model, contents, system_instruction,
                       max_tokens=None, thinking=None, on_wait=None):
        """Returns (text, project_label, model_used, finish_reason).

        Tries every ready (key, model) pair; if all are cooling down, waits for the
        soonest one (up to MAX_WAIT_SECONDS) instead of failing the request."""
        models = self.model_chain(model)
        max_tokens = max_tokens or self.s.max_output_tokens
        deadline = time.monotonic() + self.s.max_wait_seconds
        notified, attempts, last_exc = False, 0, None
        while True:
            now = time.monotonic()
            states = self._rotated()
            pairs = [(st, m) for m in models for st in states if st.ready_at(m) <= now]
            if not pairs:
                wait = max(0.0, self._next_ready(models) - now)
                if now + wait > deadline:
                    reason = f"{type(last_exc).__name__}: {str(last_exc)[:160]}" if last_exc else ""
                    raise AllUnavailable(wait, reason)
                if on_wait and not notified and wait > 3:
                    notified = True
                    await on_wait(wait)
                await asyncio.sleep(wait + 0.2)
                continue
            for st, m in pairs:
                attempts += 1
                try:
                    resp = await self._call(st, m, contents, system_instruction, max_tokens, thinking)
                except Exception as exc:
                    last_exc = exc
                    verdict = self._classify(st, m, exc)
                    log.warning("Gemini %s/%s failed (%s): %s -> %s", st.label, m,
                                status_code(exc), str(exc)[:200], verdict)
                    if verdict == "fatal":
                        raise
                    self.failovers += 1
                    if attempts >= self.s.max_attempts:
                        raise
                    if verdict == "retry_now":
                        break
                    continue
                text, finish = self._extract(resp)
                st.ok += 1
                return text, st.label, m, finish
            if attempts >= self.s.max_attempts:
                raise last_exc  # type: ignore[misc]

    async def generate_full(self, user_id, model, contents, system_instruction,
                            max_tokens=None, thinking=None, on_wait=None):
        """generate() plus automatic continuation when the output-token limit cuts the
        answer (each continuation may run on a different key/model)."""
        base = max_tokens or self.s.max_output_tokens
        tokens, parts = base, []
        label, used_model, finish = "", model, ""
        current = list(contents)
        for _ in range(self.s.max_continuations + 1):
            text, label, used_model, finish = await self.generate(
                user_id, model, current, system_instruction, tokens, thinking, on_wait)
            if text:
                parts.append(text)
            if finish != "MAX_TOKENS":
                break
            if not text:  # thinking consumed the whole budget: give it more room
                tokens = min(tokens * 2, base * 4)
                continue
            current = list(contents) + [
                types.Content(role="model", parts=[types.Part(text="".join(parts))]),
                types.Content(role="user", parts=[types.Part(text=CONTINUE_PROMPT)]),
            ]
        return "".join(parts), label, used_model, finish

    # ------------------------------------------------------------ status
    def status(self):
        now = time.monotonic()
        lines = []
        for st in self.states:
            cooling = []
            if st.key_until > now:
                cooling.append(f"کل کلید: {int(st.key_until - now)}s")
            cooling += [f"{m}: {int(t - now)}s" for m, t in st.until.items() if t > now]
            state = "آماده" if not cooling else "در انتظار → " + "، ".join(cooling)
            lines.append(f"{st.label}: {state} | موفق={st.ok} خطا={st.fail}")
        lines.append(f"تعداد failover تا حالا: {self.failovers}")
        return "\n".join(lines)
