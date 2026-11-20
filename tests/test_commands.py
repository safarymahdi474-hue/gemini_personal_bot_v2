import dataclasses
from telegram import BotCommandScopeChat, BotCommandScopeDefault

import bot as botmod
from config import settings
from core.commands import BOT_COMMANDS, validate_commands, register_commands
from tests.helpers import cfg


def registered_command_names(tmp_path):
    app = botmod.build_app(cfg(data_dir=str(tmp_path)))
    return {c for g in app.handlers.values() for h in g for c in getattr(h, "commands", [])}


def test_menu_matches_registered_handlers_exactly(tmp_path):
    menu = {n for n, _ in BOT_COMMANDS}
    assert menu == registered_command_names(tmp_path)   # no missing, no phantom commands


def test_menu_is_valid_for_telegram():
    validate_commands()


class FakeBot:
    def __init__(self, fail_chat_scope=False):
        self.calls, self.fail = [], fail_chat_scope
    async def set_my_commands(self, commands, scope=None):
        if self.fail and isinstance(scope, BotCommandScopeChat):
            raise RuntimeError("Chat not found")
        self.calls.append((len(commands), type(scope).__name__))
    async def set_chat_menu_button(self, chat_id=None, menu_button=None):
        self.calls.append(("menu_button", chat_id))


async def test_register_for_owner():
    b = FakeBot()
    assert await register_commands(b, 42) is True
    assert b.calls[0] == (len(BOT_COMMANDS), "BotCommandScopeChat") and ("menu_button", 42) in b.calls


async def test_register_falls_back_to_default_scope():
    b = FakeBot(fail_chat_scope=True)
    assert await register_commands(b, 42) is True
    assert b.calls[0] == (len(BOT_COMMANDS), "BotCommandScopeDefault")


async def test_register_never_raises():
    class Broken:
        async def set_my_commands(self, *a, **k): raise RuntimeError("down")
    assert await register_commands(Broken(), 1) is False
