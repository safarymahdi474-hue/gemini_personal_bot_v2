import time
import pytest

from core.translator import GoogleTranslator, persian_share
from tests import mock_server as ms
from tests.test_router_fmt_translator import Cache, distinct_persian


def make(**kw):
    kw.setdefault("chunk_chars", 300)
    return GoogleTranslator("", True, Cache(), **kw)


async def test_chunks_are_translated_in_parallel():
    t = make(workers=4)
    def slow(text, s, tg):
        time.sleep(0.3); return "T"
    t._translate_web = slow
    text = distinct_persian(90)                       # >= 8 distinct chunks
    n_chunks = len(t._split_prose(text))
    assert n_chunks >= 8
    t0 = time.monotonic(); await t.translate(text, "auto", "en"); dt = time.monotonic() - t0
    assert dt < 0.3 * n_chunks * 0.6                  # sequential would be 0.3 * n_chunks


async def test_text_already_in_target_language_is_not_sent():
    t = make(); calls = []
    t._translate_web = lambda *a: calls.append(a) or "x"
    assert await t.translate("Hello, this is already English text.", "auto", "en") == "Hello, this is already English text."
    persian = "این متن از قبل فارسی است و نیازی به ترجمه ندارد."
    assert await t.translate(persian, "auto", "fa") == persian
    assert await t.translate("12345 😀", "auto", "en") == "12345 😀"
    assert calls == []
    assert persian_share("سلام hello") is not None


async def test_failure_returns_original_layout_and_opens_breaker():
    t = make(cooldown=1, workers=2); calls = []
    def boom(text, s, tg):
        calls.append(text); raise ConnectionError("down")
    t._translate_web = boom
    text = distinct_persian(30) + "\n\nخط جدید"
    assert await t.translate(text, "auto", "en") == text          # exactly the original, layout intact
    first = len(calls); assert first >= 1
    assert await t.translate(distinct_persian(31), "auto", "en")   # breaker open: no new network calls
    assert len(calls) == first
    time.sleep(1.1)
    await t.translate(distinct_persian(32), "auto", "en")          # cooldown over: tries again
    assert len(calls) > first


def test_cloud_sends_all_chunks_in_one_request():
    t = GoogleTranslator("KEY", True, Cache(), chunk_chars=300)
    calls = []
    t._translate_cloud = lambda texts, s, tg: (calls.append(len(texts)), [f"T{i}" for i, _ in enumerate(texts)])[1]
    text = distinct_persian(60)
    n = len(set(c for c, _ in t._split_prose(text)))
    out = t._translate_text(text, "auto", "en")
    assert calls == [n] and n > 5 and "T0" in out


# ---- real HTTP against a local mock of the Google endpoint -------------------
@pytest.fixture
def mock():
    srv, url = ms.start()
    ms.State.delay, ms.State.hang, ms.State.requests, ms.State.connections = 0.2, False, 0, 0
    yield url
    srv.shutdown()


async def test_real_http_keepalive_and_parallel(mock):
    t = make(workers=4); t.WEB_ENDPOINT = mock
    text = distinct_persian(60)
    n = len(set(c for c, _ in t._split_prose(text)))
    t0 = time.monotonic(); out = await t.translate(text, "auto", "en"); dt = time.monotonic() - t0
    assert "T(" in out and ms.State.requests == n
    assert dt < 0.2 * n * 0.6                                       # parallel
    before = ms.State.connections
    await t.translate(distinct_persian(61), "auto", "en")           # connections get reused
    assert ms.State.connections - before < ms.State.requests - n


async def test_real_http_hang_is_bounded_by_timeout_not_20s_per_chunk(mock):
    ms.State.hang = True
    t = make(workers=4, timeout=1.0); t.WEB_ENDPOINT = mock
    text = distinct_persian(60)
    t0 = time.monotonic(); out = await t.translate(text, "auto", "en"); dt = time.monotonic() - t0
    assert out == text and dt < 4.0                                 # v3.1 needed 20s x chunks
    t0 = time.monotonic(); await t.translate(distinct_persian(61), "auto", "en")
    assert time.monotonic() - t0 < 0.3                              # breaker open -> instant
