import logging

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from config import settings, validate
from core.db import Database
from core.router import MODES
from core.gemini import GeminiService
from core.translator import GoogleTranslator
from core.memory import MemoryManager
from core.handlers import BotHandlers
from core.commands import register_commands

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("personal-gemini-bot")


def build_app(cfg=settings):
    db = Database(cfg.data_dir)
    translator = GoogleTranslator(
        api_key=cfg.google_translate_api_key,
        enabled=cfg.translate_enabled,
        cache=db,
        chunk_chars=cfg.translate_chunk_chars,
        timeout=cfg.translate_timeout,
        workers=cfg.translate_workers,
        cooldown=cfg.translate_cooldown,
        cooldown_max=cfg.translate_cooldown_max,
    )
    gemini = GeminiService(cfg, db)
    memory = MemoryManager(db, cfg.memory_max_chars, cfg.memory_limit)
    handlers = BotHandlers(cfg, db, translator, gemini, memory)

    async def post_init(application):
        handlers.start_worker(application.bot)  # retries queued requests automatically
        if cfg.set_bot_commands:
            await register_commands(application.bot, cfg.owner_id)  # the "/" menu

    async def post_shutdown(application):
        handlers.stop_worker()
        translator.close()

    async def on_error(update, context):
        log.error("Unhandled error while processing an update", exc_info=context.error)

    app = (
        ApplicationBuilder().token(cfg.telegram_token)
        .concurrent_updates(4).post_init(post_init).post_shutdown(post_shutdown).build()
    )

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
    app.add_handler(CommandHandler("queue", handlers.queue))
    app.add_handler(CommandHandler("clearqueue", handlers.clearqueue))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.message))
    app.add_error_handler(on_error)
    return app


def main():
    validate(settings)
    app = build_app()
    log.info("Personal bot starting. Translation=%s keys=%d",
             settings.translate_enabled, len(settings.gemini_keys))
    app.run_polling()


if __name__ == "__main__":
    main()
