import time
import pytest
from google.genai import types

from core.gemini import GeminiService, AllUnavailable, parse_retry_delay
from tests.helpers import FakeAPIError, resp, make_factory, cfg

USER = [types.Content(role="user", parts=[types.Part(text="hi")])]


def svc(*scripts, **kw):
    factory, clients = make_factory(*scripts)
    return GeminiService(cfg(**kw), None, client_factory=factory), clients


async def test_429_on_one_key_continues_on_other_key():
    s, c = svc([FakeAPIError(429, "quota")], [resp("done")])
    text, label, model, finish = await s.generate(1, "strong-m", USER, "sys")
    assert text == "done" and label == "project-2" and model == "strong-m"
    assert s.failovers == 1


async def test_same_model_on_other_key_before_downgrading_model():
    s, c = svc([FakeAPIError(503)], [resp("ok")])
    _, _, model, _ = await s.generate(1, "strong-m", USER, "sys")
    assert model == "strong-m"  # other key tried before the light model


async def test_all_keys_429_waits_then_resumes_same_task():
    s, c = svc([FakeAPIError(429, "x"), resp("late1")], [FakeAPIError(429, "x"), resp("late2")])
    waited = []
    async def on_wait(sec): waited.append(sec)
    t0 = time.monotonic()
    text, *_ = await s.generate(1, "m", USER, "sys", on_wait=on_wait)
    assert text.startswith("late")
    # strong model and light model both exist -> falls to light model on a ready key first
    assert time.monotonic() - t0 < 3.5


async def test_everything_exhausted_raises_all_unavailable_with_wait():
    errs = lambda: [FakeAPIError(429, "x")] * 3
    s, c = svc(errs(), errs(), max_wait_seconds=0)
    with pytest.raises(AllUnavailable) as e:
        await s.generate(1, "m", USER, "sys")
    assert e.value.wait > 0 and "429" in e.value.reason


async def test_daily_quota_uses_long_cooldown():
    s, c = svc([FakeAPIError(429, "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel")], [resp("k2")])
    await s.generate(1, "m", USER, "sys")
    left = s.states[0].until["m"] - time.monotonic()
    assert left > 20  # cooldown_daily=30 in test cfg


async def test_failover_can_be_disabled():
    s, c = svc([FakeAPIError(429, "x")], [resp("never")], failover_on_429=False)
    with pytest.raises(FakeAPIError):
        await s.generate(1, "m", USER, "sys")


async def test_400_is_fatal_not_retried():
    s, c = svc([FakeAPIError(400, "bad request")], [resp("x")])
    with pytest.raises(FakeAPIError):
        await s.generate(1, "m", USER, "sys")
    assert not c[1].aio.models.calls


async def test_thinking_rejected_is_dropped_and_retried():
    s, c = svc([FakeAPIError(400, "thinking level not supported")], [resp("ok")])
    text, *_ = await s.generate(1, "m", USER, "sys", thinking="low")
    assert text == "ok" and s.thinking_ok is False
    assert c[1].aio.models.calls[0][2].thinking_config is None


async def test_truncated_answer_is_continued_possibly_on_another_key():
    s, c = svc([resp("part one ", "MAX_TOKENS")], [resp("part two", "STOP")])
    text, *_, finish = await s.generate_full(1, "m", USER, "sys", max_tokens=50)
    assert text == "part one part two" and finish == "STOP"
    sent = c[1].aio.models.calls[0][1]
    assert sent[-2].role == "model" and sent[-2].parts[0].text == "part one "
    assert sent[-1].role == "user"


async def test_empty_answer_from_thinking_gets_bigger_budget():
    s, c = svc([resp("", "MAX_TOKENS")], [resp("final", "STOP")])
    text, *_ = await s.generate_full(1, "m", USER, "sys", max_tokens=100)
    assert text == "final"
    budgets = [call[2].max_output_tokens for call in c[0].aio.models.calls + c[1].aio.models.calls]
    assert 200 in budgets


def test_parse_retry_delay():
    assert parse_retry_delay("Please retry in 34.5s.") == 34.5
    assert parse_retry_delay("{'retryDelay': '12s'}") == 12
    assert parse_retry_delay("nothing") is None
