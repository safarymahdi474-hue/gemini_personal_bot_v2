import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from config import settings
from core.db import Database
from core.router import resolve_mode, MODES
from core.gemini import GeminiService
from core.translator import GoogleTranslator
from core.memory import MemoryManager
from core.handlers import BotHandlers

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("personal-gemini-bot")


def main():
    db = Database(settings.data_dir)
    translator = GoogleTranslator(
        api_key=settings.google_translate_api_key,
        enabled=settings.translate_enabled,
        cache=db,
    )
    gemini = GeminiService(settings, db)
    memory = MemoryManager(db)
    handlers = BotHandlers(settings, db, translator, gemini, memory)

    app = ApplicationBuilder().token(settings.telegram_token).concurrent_updates(4).build()

    # Main / mode commands. Commands accept optional inline text.
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help))
    for command in MODES:
        app.add_handler(CommandHandler(command, handlers.mode_command))
    app.add_handler(CommandHandler("reset", handlers.reset))
    app.add_handler(CommandHandler("history", handlers.history))
    app.add_handler(CommandHandler("prompt", handlers.prompt))
    app.add_handler(CommandHandler("setprompt", handlers.setprompt))
    app.add_handler(CommandHandler("model", handlers.model))
    app.add_handler(CommandHandler("settings", handlers.settings_command))
    app.add_handler(CommandHandler("keys", handlers.keys))
    app.add_handler(CommandHandler("remember", handlers.remember))
    app.add_handler(CommandHandler("memories", handlers.memories))
    app.add_handler(CommandHandler("forget", handlers.forget))
    app.add_handler(CommandHandler("stats", handlers.stats))
    app.add_handler(CommandHandler("usage", handlers.usage))
    app.add_handler(CommandHandler("translation", handlers.translation_toggle))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.message))

    log.info("Personal bot starting. Translation=%s", settings.translate_enabled)
    app.run_polling()


if __name__ == "__main__":
    main()
