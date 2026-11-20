import asyncio, os, sqlite3, tempfile
from types import SimpleNamespace

from core.db import Database
from core.gemini import GeminiService
from core.handlers import BotHandlers, _build_contents
from core.memory import MemoryManager
from core.translator import GoogleTranslator
from tests.helpers import FakeAPIError, resp, make_factory, cfg


def test_legacy_migration_runs_once_even_after_reset():
    d = tempfile.mkdtemp()
    old = sqlite3.connect(os.path.join(d, "chat.db"))
    old.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, chat_id INT, role TEXT, text TEXT)")
    old.executemany("INSERT INTO messages(chat_id,role,text) VALUES(?,?,?)", [(1, "user", "a"), (1, "model", "b")])
    old.commit(); old.close()
    db = Database(d); assert db.message_count(1) == 2
    db.clear_history(1); assert db.message_count(1) == 0
    assert Database(d).message_count(1) == 0          # was 2 in v2


def test_prune_and_unwritable_data_dir(tmp_path, monkeypatch):
    db = Database(str(tmp_path))
    for i in range(10):
        db.save_exchange(1, f"u{i}", f"m{i}", "chat", keep=6)
    assert db.message_count(1) == 6
    monkeypatch.chdir(tmp_path)
    assert Database("/proc/definitely/not/writable").path.startswith(str(tmp_path))


def test_build_contents_merges_and_starts_with_user():
    rows = [{"role": "model", "content": "orphan"}, {"role": "user", "content": "a"},
            {"role": "user", "content": "b"}, {"role": "model", "content": "c"}]
    c = _build_contents(rows, "new")
    assert [x.role for x in c] == ["user", "model", "user"] and c[0].parts[0].text == "a\nb"


class FakeBot:
    def __init__(self): self.sent = []
    async def send_message(self, chat_id, text, **kw): self.sent.append(text)
    async def send_chat_action(self, *a, **k): pass


def build(tmp_path, *scripts, **kw):
    settings = cfg(data_dir=str(tmp_path), **kw)
    db = Database(str(tmp_path))
    factory, clients = make_factory(*scripts)
    gem = GeminiService(settings, db, client_factory=factory)
    h = BotHandlers(settings, db, GoogleTranslator("", False, db), gem, MemoryManager(db))
    return h, db, gem, clients


async def test_normal_turn_saves_history_and_formats(tmp_path):
    h, db, gem, c = build(tmp_path, [resp("**hi** there")], [])
    bot = FakeBot()
    assert await h.process(bot, 1, 1, "سلام", "chat") == "ok"
    assert bot.sent == ["<b>hi</b> there"] and db.message_count(1) == 2


async def test_failed_request_leaves_no_orphan_message(tmp_path):
    h, db, gem, c = build(tmp_path, [FakeAPIError(400, "bad")], [])
    bot = FakeBot()
    assert await h.process(bot, 1, 1, "x", "chat") == "error"
    assert db.message_count(1) == 0


async def test_quota_exhausted_is_queued_then_finished_automatically(tmp_path):
    # both keys hit 429 on every model, with a long retry delay -> request must be queued, not lost
    long429 = lambda: FakeAPIError(429, "Please retry in 2s")
    h, db, gem, c = build(tmp_path, [long429(), resp("answer after wait")],
                          [long429(), resp("answer after wait 2")], max_wait_seconds=0)
    bot = FakeBot()
    assert await h.process(bot, 1, 1, "important task", "chat") == "queued"
    assert db.pending_count() == 1 and "صف" in bot.sent[-1]
    assert db.message_count(1) == 0                      # nothing half-saved
    await h.drain_queue(bot)                             # still cooling down -> stays queued
    assert db.pending_count() == 1
    await asyncio.sleep(3.3)                             # quota "resets"
    await h.drain_queue(bot)
    assert db.pending_count() == 0
    assert "answer after wait" in bot.sent[-1] and bot.sent[-1].startswith("✅")
    assert db.message_count(1) == 2


async def test_long_wait_notifies_and_continues_same_request(tmp_path):
    e = lambda: FakeAPIError(429, "Please retry in 4s")
    h, db, gem, c = build(tmp_path, [e(), resp("continued")], [e(), resp("continued")],
                          max_wait_seconds=10)
    bot = FakeBot()
    assert await h.process(bot, 1, 1, "task", "chat") == "ok"
    assert any("صبر" in m for m in bot.sent) and bot.sent[-1] == "continued"
    assert db.pending_count() == 0


async def test_translation_skipped_for_code_mode(tmp_path):
    h, db, gem, c = build(tmp_path, [resp("```py\nprint(1)\n```")], [])
    db.set_setting(1, "translation", "true")
    h.translator.translate = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not translate"))
    bot = FakeBot()
    assert await h.process(bot, 1, 1, "write code", "code") == "ok"
    assert "<pre>print(1)</pre>" in bot.sent[-1]


async def test_inline_command_is_one_shot_and_bare_command_is_sticky(tmp_path):
    h, db, gem, c = build(tmp_path, [resp("a"), resp("b")], [])
    bot = FakeBot()
    sent_texts = []
    async def reply_text(t): sent_texts.append(t)
    def upd(text):
        return SimpleNamespace(
            message=SimpleNamespace(text=text, reply_text=reply_text),
            effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(id=1))
    ctx = SimpleNamespace(bot=bot)
    await h.mode_command(upd("/Code write a function"), ctx)     # upper-case no longer KeyErrors
    assert db.get_setting(1, "mode", "chat") == "chat"           # one-shot
    await h.mode_command(upd("/translate"), ctx)
    assert db.get_setting(1, "mode", "chat") == "translate"      # sticky
    assert (await h.queue(upd("/queue"), ctx)) is None and sent_texts[-1] == "صف خالی است."
