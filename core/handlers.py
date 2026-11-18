import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from google.genai import types
from core.router import MODES, resolve_mode, model_for

log = logging.getLogger(__name__)

BASE_PROMPT = (
    "You are a personal assistant. Be accurate, concise, and useful. "
    "Avoid filler, repeated conclusions, and unnecessary introductions. "
    "Reply in English internally; the user-facing answer will be translated to Persian."
)


class BotHandlers:
    def __init__(self, settings, db, translator, gemini, memory):
        self.s = settings
        self.db = db
        self.translator = translator
        self.gemini = gemini
        self.memory = memory
        legacy_prompt = os.path.join(settings.data_dir, "prompt.txt")
        if os.path.isfile(legacy_prompt) and not self.db.get_setting(settings.owner_id, "prompt", ""):
            try:
                with open(legacy_prompt, encoding="utf-8") as handle:
                    old_prompt = handle.read().strip()
                if old_prompt:
                    self.db.set_setting(settings.owner_id, "prompt", old_prompt[:3000])
            except OSError:
                pass

    def allowed(self, update):
        return bool(update.effective_user and update.effective_user.id == self.s.owner_id)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.allowed(update): return
        await update.message.reply_text(
            "ربات شخصی آماده است.\n"
            "دستورات: /chat /ask /code /creative /translate /summarize "
            "/explain /brainstorm\n"
            "حافظه: /remember /memories /forget\n"
            "مدیریت: /model /settings /translation /keys /stats /usage\n"
            "راهنما: /help"
        )

    async def help(self, update, context):
        if not self.allowed(update): return
        await update.message.reply_text(
            "/chat گفت‌وگوی عادی\n/ask پرسش دقیق\n/code برنامه‌نویسی\n"
            "/creative نویسندگی و خلاقیت\n/translate ترجمه\n/summarize خلاصه‌سازی\n"
            "/explain آموزش و توضیح\n/brainstorm ایده‌پردازی\n"
            "/reset پاک‌کردن تاریخچه چت\n/history نمایش تعداد پیام‌ها\n"
            "/remember متن برای حافظه بلندمدت\n/memories دیدن حافظه\n/forget ID حذف یک مورد\n"
            "/model مدل پیشنهادی برای حالت فعلی\n/prompt دیدن پرامپت\n"
            "/setprompt متن تنظیم پرامپت\n/translation on|off\n/keys وضعیت پروژه‌ها"
        )

    async def mode_command(self, update, context):
        if not self.allowed(update): return
        mode = update.message.text.split()[0].split("@")[0][1:]
        context.user_data["mode"] = mode
        payload = update.message.text.partition(" ")[2].strip()
        if payload:
            await self._answer(update, context, payload, mode)
        else:
            await update.message.reply_text(f"حالت «{mode}» فعال شد. پیام بعدی را بفرست.")

    async def message(self, update, context):
        if not self.allowed(update) or not update.message or not update.message.text:
            return
        selected = context.user_data.get("mode", "chat")
        text = update.message.text.strip()
        mode = resolve_mode(text, selected)
        await self._answer(update, context, text, mode)

    async def _answer(self, update, context, user_text, mode):
        user_id = update.effective_user.id
        await update.effective_chat.send_action("typing")
        try:
            # Optional Google translation: Persian -> English before Gemini.
            translation_pref = self.db.get_setting(
                user_id, "translation", str(self.s.translate_enabled)
            ).lower() == "true"
            self.translator.enabled = translation_pref
            internal_user = await self.translator.translate(user_text, "fa", "en")
            self.db.save_message(user_id, "user", internal_user, mode)

            rows = self.db.history(user_id, self.s.history_turns * 2)
            history = []
            for row in rows:
                role = "user" if row["role"] == "user" else "model"
                history.append(types.Content(role=role, parts=[types.Part(text=row["content"])]))

            memory_text = self.memory.context(user_id)
            prompt = (
                BASE_PROMPT if self.translator.enabled
                else "You are a personal assistant. Be accurate, concise, useful, and reply in Persian. Avoid filler."
            )
            custom = self.db.get_setting(user_id, "prompt", "")
            if custom:
                prompt += "\nUser custom instructions:\n" + custom[:1200]
            if memory_text:
                prompt += "\nRelevant saved user memory:\n" + memory_text

            prompt += "\nTask mode: " + MODES[mode]["system"]
            model = model_for(mode, self.s)
            answer_en, project = await self.gemini.generate(
                user_id, model, history, prompt
            )
            if not answer_en:
                answer_en = "I could not generate a response."
            self.db.save_message(user_id, "model", answer_en, mode)
            self.db.record_usage(user_id, model, len(internal_user), len(answer_en))

            # Optional English -> Persian display translation.
            answer_fa = await self.translator.translate(answer_en, "en", "fa")
            await self._send_long(update, answer_fa)
            log.info("Completed mode=%s model=%s project=%s", mode, model, project)
        except Exception as exc:
            log.exception("Request failed")
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            if status == 429:
                msg = "محدودیت سهمیه یا نرخ Gemini فعال شده؛ بعداً دوباره تلاش کن."
            elif status in (401, 403):
                msg = "دسترسی یکی از کلیدهای Gemini مشکل دارد؛ /keys را بررسی کن."
            else:
                msg = "در اجرای درخواست خطایی رخ داد. لاگ برنامه را بررسی کن."
            await update.message.reply_text(msg)

    async def _send_long(self, update, text):
        text = text or "پاسخی دریافت نشد."
        for start in range(0, len(text), 3900):
            await update.message.reply_text(text[start:start+3900])

    async def reset(self, update, context):
        if not self.allowed(update): return
        self.db.clear_history(update.effective_user.id)
        await update.message.reply_text("تاریخچه گفت‌وگو پاک شد؛ حافظه بلندمدت دست‌نخورده ماند.")

    async def history(self, update, context):
        if not self.allowed(update): return
        await update.message.reply_text(f"تعداد پیام‌های ذخیره‌شده: {self.db.message_count(update.effective_user.id)}")

    async def remember(self, update, context):
        if not self.allowed(update): return
        value = update.message.text.partition(" ")[2].strip()
        if not value:
            await update.message.reply_text("استفاده: /remember چیزی که باید به خاطر بسپارم")
            return
        mid = self.memory.add(update.effective_user.id, value)
        await update.message.reply_text(f"در حافظه ذخیره شد. شناسه: {mid}")

    async def memories(self, update, context):
        if not self.allowed(update): return
        rows = self.memory.list(update.effective_user.id)
        if not rows:
            await update.message.reply_text("حافظه ذخیره‌شده‌ای وجود ندارد.")
            return
        lines = [f"{r['id']}. {r['content']}" for r in rows]
        await self._send_long(update, "\n".join(lines))

    async def forget(self, update, context):
        if not self.allowed(update): return
        arg = context.args[0] if context.args else ""
        if not arg.isdigit():
            await update.message.reply_text("استفاده: /forget شناسه")
            return
        deleted = self.memory.forget(update.effective_user.id, int(arg))
        await update.message.reply_text("حذف شد." if deleted else "شناسه پیدا نشد.")

    async def prompt(self, update, context):
        if not self.allowed(update): return
        value = self.db.get_setting(update.effective_user.id, "prompt", "")
        await update.message.reply_text(value or "پرامپت اختصاصی تنظیم نشده است.")

    async def setprompt(self, update, context):
        if not self.allowed(update): return
        value = update.message.text.partition(" ")[2].strip()
        if not value:
            await update.message.reply_text("استفاده: /setprompt متن دستورالعمل")
            return
        self.db.set_setting(update.effective_user.id, "prompt", value[:3000])
        await update.message.reply_text("پرامپت ذخیره شد.")

    async def model(self, update, context):
        if not self.allowed(update): return
        mode = context.user_data.get("mode", "chat")
        await update.message.reply_text(f"حالت: {mode}\nمدل: {model_for(mode, self.s)}")

    async def settings_command(self, update, context):
        if not self.allowed(update): return
        mode = context.user_data.get("mode", "chat")
        trans = self.db.get_setting(update.effective_user.id, "translation", str(self.s.translate_enabled))
        await update.message.reply_text(f"حالت فعلی: {mode}\nترجمه: {trans}\nتعداد turnهای تاریخچه: {self.s.history_turns}")

    async def translation_toggle(self, update, context):
        if not self.allowed(update): return
        if not context.args or context.args[0].lower() not in ("on", "off"):
            await update.message.reply_text("استفاده: /translation on یا /translation off\nبرای فعال‌سازی، کلید Cloud Translation را در .env قرار بده.")
            return
        # Runtime preference; global translator must be configured with credentials.
        if context.args[0].lower() == "on" and not self.s.google_translate_api_key:
            await update.message.reply_text("کلید Google Cloud Translation تنظیم نشده است.")
            return
        self.translator.enabled = context.args[0].lower() == "on"
        self.db.set_setting(update.effective_user.id, "translation", self.translator.enabled)
        await update.message.reply_text("ترجمه فعال شد." if self.translator.enabled else "ترجمه غیرفعال شد.")

    async def keys(self, update, context):
        if not self.allowed(update): return
        await update.message.reply_text(self.gemini.status())

    async def stats(self, update, context):
        if not self.allowed(update): return
        u = self.db.usage_summary(update.effective_user.id)
        await update.message.reply_text(f"درخواست‌های ثبت‌شده: {u['n']}\nکاراکتر ورودی به Gemini: {u['i']}\nکاراکتر خروجی: {u['o']}")

    async def usage(self, update, context):
        await self.stats(update, context)
