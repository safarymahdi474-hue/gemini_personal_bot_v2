"""Telegram command menu: the list that pops up when you type "/" (and behind the
blue menu button next to the message box), each command with a short description."""
import logging
import re

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, MenuButtonCommands

log = logging.getLogger(__name__)

# (command, description) in the order they should appear. Telegram limits:
# command = 1-32 chars [a-z0-9_]; description = 3-256 chars; max 100 commands.
BOT_COMMANDS = [
    ("start", "شروع و نمایش دستورات"),
    ("chat", "گفت‌وگوی عادی و برگشت به تشخیص خودکار"),
    ("ask", "پرسش دقیق (مدل قوی)"),
    ("code", "برنامه‌نویسی و دیباگ"),
    ("creative", "نویسندگی و خلاقیت"),
    ("translate", "ترجمه متن"),
    ("summarize", "خلاصه‌سازی متن"),
    ("explain", "آموزش و توضیح مرحله‌به‌مرحله"),
    ("brainstorm", "ایده‌پردازی"),
    ("remember", "ذخیره در حافظه بلندمدت"),
    ("memories", "دیدن حافظه ذخیره‌شده"),
    ("forget", "حذف یک مورد از حافظه (با شناسه)"),
    ("reset", "پاک‌کردن تاریخچه گفت‌وگو"),
    ("history", "تعداد پیام‌های ذخیره‌شده"),
    ("prompt", "دیدن پرامپت اختصاصی"),
    ("setprompt", "تنظیم پرامپت اختصاصی"),
    ("model", "مدل مربوط به حالت فعلی"),
    ("settings", "نمایش تنظیمات فعلی"),
    ("translation", "روشن/خاموش کردن ترجمه (on یا off)"),
    ("queue", "درخواست‌های منتظر سهمیه"),
    ("clearqueue", "لغو درخواست‌های صف‌شده"),
    ("keys", "وضعیت کلیدها و مدل‌ها"),
    ("stats", "آمار مصرف"),
    ("usage", "آمار مصرف (همان stats)"),
    ("help", "راهنمای کامل"),
]

_NAME = re.compile(r"^[a-z0-9_]{1,32}$")


def command_objects():
    return [BotCommand(name, desc) for name, desc in BOT_COMMANDS]


def validate_commands(commands=BOT_COMMANDS):
    """Raises ValueError if Telegram would reject the list."""
    if not 1 <= len(commands) <= 100:
        raise ValueError("Telegram allows 1-100 commands")
    seen = set()
    for name, desc in commands:
        if not _NAME.match(name):
            raise ValueError(f"invalid command name: {name!r}")
        if not 3 <= len(desc) <= 256:
            raise ValueError(f"description length for /{name} must be 3-256")
        if name in seen:
            raise ValueError(f"duplicate command: {name}")
        seen.add(name)


async def register_commands(bot, owner_id):
    """Publish the menu for the owner (falls back to the default scope if Telegram
    does not know the owner chat yet). Never raises: a menu problem must not stop the bot."""
    try:
        validate_commands()
        commands = command_objects()
        try:
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(owner_id))
            scope = f"owner chat {owner_id}"
        except Exception as exc:  # e.g. owner never pressed Start -> "chat not found"
            log.warning("Owner-scoped menu failed (%s); using default scope", exc)
            await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
            scope = "default scope"
        try:
            await bot.set_chat_menu_button(chat_id=owner_id, menu_button=MenuButtonCommands())
        except Exception as exc:
            log.debug("Menu button not set: %s", exc)
        log.info("Registered %d bot commands (%s)", len(commands), scope)
        return True
    except Exception:
        log.exception("Could not register the command menu")
        return False
