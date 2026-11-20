import asyncio
import logging
import os
import time

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes
from google.genai import types

from core.fmt import split_message, to_html
from core.gemini import AllUnavailable, status_code
from core.router import MODES, resolve_mode, model_for, options_for

log = logging.getLogger(__name__)

PROMPT_EN = (
    "You are a personal assistant. Be accurate, concise, and useful. "
    "Avoid filler, repeated conclusions, and unnecessary introductions. "
    "Reply in English internally; the user-facing answer will be translated to Persian."
)
PROMPT_FA = (
    "You are a personal assistant. Be accurate, concise, useful, and reply in Persian. Avoid filler."
)
BAD_FINISH = {
    "SAFETY": "پاسخ توسط فیلتر ایمنی Gemini مسدود شد؛ سؤال را بازنویسی کن.",
    "PROHIBITED_CONTENT": "پاسخ توسط فیلتر ایمنی Gemini مسدود شد؛ سؤال را بازنویسی کن.",
    "BLOCKLIST": "پاسخ توسط فیلتر ایمنی Gemini مسدود شد؛ سؤال را بازنویسی کن.",
    "RECITATION": "Gemini به‌دلیل شباهت زیاد به متن منتشرشده پاسخ نداد؛ سؤال را بازنویسی کن.",
}


def _build_contents(rows, new_user_text):
    """Merge same-role neighbours and make sure the first turn is a user turn."""
    turns = []
    for row in rows:
        role = "user" if row["role"] == "user" else "model"
        if turns and turns[-1][0] == role:
            turns[-1][1] += "\n" + row["content"]
        else:
            turns.append([role, row["content"]])
    while turns and turns[0][0] == "model":
        turns.pop(0)
    if turns and turns[-1][0] == "user":
        turns[-1][1] += "\n" + new_user_text
    else:
        turns.append(["user", new_user_text])
    return [types.Content(role=r, parts=[types.Part(text=t)]) for r, t in turns]


class BotHandlers:
    def __init__(self, settings, db, translator, gemini, memory):
        self.s = settings
        self.db = db
        self.translator = translator
        self.gemini = gemini
        self.memory = memory
        self._locks = {}
        self._worker_task = None
        legacy_prompt = os.path.join(settings.data_dir, "prompt.txt")
        if os.path.isfile(legacy_prompt) and not self.db.get_setting(settings.owner_id, "prompt", ""):
            try:
                with open(legacy_prompt, encoding="utf-8") as handle:
                    old_prompt = handle.read().strip()
                if old_prompt:
                    self.db.set_setting(settings.owner_id, "prompt", old_prompt[:settings.prompt_max_chars])
            except OSError:
                pass

    # ------------------------------------------------------------ helpers
    def allowed(self, update):
        return bool(update.effective_user and update.effective_user.id == self.s.owner_id)

    def _lock(self, user_id):
        lock = self._locks.get(user_id)
        if lock is None:
            lock = self._locks[user_id] = asyncio.Lock()
        return lock

    def _mode(self, user_id):
        mode = self.db.get_setting(user_id, "mode", "chat")
        return mode if mode in MODES else "chat"

    def _translation_pref(self, user_id):
        return self.db.get_setting(
            user_id, "translation", str(self.s.translate_enabled)).lower() == "true"

    async def _send(self, bot, chat_id, text):
        """Send with HTML formatting; fall back to plain text; retry transient errors."""
        for chunk in split_message(text or "پاسخی دریافت نشد.", self.s.telegram_chunk_chars):
            html_text = to_html(chunk)
            for attempt in range(3):
                try:
                    if len(html_text) <= 4096:
                        await bot.send_message(chat_id, html_text, parse_mode=ParseMode.HTML)
                    else:
                        await bot.send_message(chat_id, chunk[:4096])
                    break
                except Exception as exc:
                    retry_after = getattr(exc, "retry_after", None)
                    if retry_after:
                        await asyncio.sleep(float(getattr(retry_after, "total_seconds", lambda: retry_after)()))
                        continue
                    try:  # formatting rejected -> plain text
                        await bot.send_message(chat_id, chunk[:4096])
                        break
                    except Exception:
                        if attempt == 2:
                            log.exception("Could not deliver message")
                        await asyncio.sleep(1 + attempt)

    async def _typing_loop(self, bot, chat_id):
        try:
            while True:
                try:
                    await bot.send_chat_action(chat_id, ChatAction.TYPING)
                except Exception:
                    pass
                await asyncio.sleep(self.s.typing_interval)
        except asyncio.CancelledError:
            pass

    # --------------------------------------------------------- main engine
    async def process(self, bot, chat_id, user_id, text, mode, from_queue=False):
        """Returns 'ok', 'queued' or 'error'. Never loses the request: if every
        key/model is exhausted the request is stored and answered automatically later."""
        async with self._lock(user_id):
            typing = asyncio.create_task(self._typing_loop(bot, chat_id))
            try:
                return await self._process_locked(bot, chat_id, user_id, text, mode, from_queue)
            finally:
                typing.cancel()

    async def _process_locked(self, bot, chat_id, user_id, text, mode, from_queue):
        try:
            use_translation = self._translation_pref(user_id) and mode not in self.s.no_translate_modes
            internal_user = (
                await self.translator.translate(text, "auto", "en", enabled=True)
                if use_translation else text
            )
            rows = self.db.history(user_id, self.s.history_turns * 2)
            contents = _build_contents(rows, internal_user)

            prompt = PROMPT_EN if use_translation else PROMPT_FA
            custom = self.db.get_setting(user_id, "prompt", "")
            if custom:
                prompt += "\nUser custom instructions:\n" + custom[:self.s.prompt_max_chars]
            memory_text = self.memory.context(user_id)
            if memory_text:
                prompt += "\nSaved user memory:\n" + memory_text
            prompt += "\nTask mode: " + MODES[mode]["system"]

            model = model_for(mode, self.s)
            opts = options_for(mode, self.s)

            async def on_wait(seconds):
                await bot.send_message(
                    chat_id,
                    f"⏳ همه‌ی کلیدها/مدل‌ها موقتاً سقف خوردن؛ حدود {int(seconds)} ثانیه صبر می‌کنم "
                    "و همین کار رو خودکار ادامه می‌دم.")

            try:
                answer, project, used_model, finish = await self.gemini.generate_full(
                    user_id, model, contents, prompt,
                    max_tokens=opts.max_tokens, thinking=opts.thinking,
                    on_wait=None if from_queue else on_wait)
            except AllUnavailable as exc:
                if not from_queue:
                    self.db.enqueue_pending(user_id, chat_id, text, mode)
                    reason = f"\n(آخرین خطا: {exc.reason})" if exc.reason else ""
                    await bot.send_message(
                        chat_id,
                        "⏳ فعلاً همه‌ی کلیدها/مدل‌ها در انتظار آزادسازی سهمیه‌ان. درخواستت در صف ماند و "
                        "به‌محض آماده شدن خودکار جواب داده می‌شه؛ لازم نیست دوباره بفرستی. "
                        f"(/queue برای دیدن صف، /clearqueue برای لغو){reason}")
                return "queued"

            if not answer.strip():
                msg = BAD_FINISH.get(finish, "Gemini پاسخی تولید نکرد؛ دوباره امتحان کن یا سؤال را تغییر بده.")
                await bot.send_message(chat_id, msg)
                return "error"

            self.db.save_exchange(user_id, internal_user, answer, mode, keep=self.s.keep_messages)
            self.db.record_usage(user_id, used_model, len(internal_user), len(answer))

            shown = (
                await self.translator.translate(answer, "auto", "fa", enabled=True)
                if use_translation else answer
            )
            if finish == "MAX_TOKENS":
                shown += "\n\n… (پاسخ به سقف طول رسید؛ برای ادامه بنویس «ادامه بده»)"
            if from_queue:
                shown = "✅ پاسخ درخواست صف‌شده‌ات:\n\n" + shown
            await self._send(bot, chat_id, shown)
            log.info("Completed mode=%s model=%s project=%s queued=%s", mode, used_model, project, from_queue)
            return "ok"
        except Exception as exc:
            log.exception("Request failed")
            status = status_code(exc)
            if status == 429:
                msg = "محدودیت سهمیه یا نرخ Gemini فعال شده؛ بعداً دوباره تلاش کن."
            elif status in (401, 403):
                msg = "دسترسی یکی از کلیدهای Gemini مشکل دارد؛ /keys را بررسی کن."
            elif status and status >= 500:
                msg = "سرور Gemini فعلاً شلوغ یا در دسترس نیست؛ چند لحظه بعد دوباره پیام بده."
            else:
                msg = f"در اجرای درخواست خطایی رخ داد ({status or type(exc).__name__}). لاگ برنامه را بررسی کن."
            try:
                await bot.send_message(chat_id, msg)
            except Exception:
                pass
            return "error"

    # ---------------------------------------------------------- retry queue
    def start_worker(self, bot):
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._queue_worker(bot))

    def stop_worker(self):
        if self._worker_task:
            self._worker_task.cancel()
            self._worker_task = None

    async def _queue_worker(self, bot):
        while True:
            try:
                await asyncio.sleep(self.s.queue_poll_seconds)
                await self.drain_queue(bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Queue worker error")

    async def drain_queue(self, bot):
        for row in self.db.purge_expired_pending(self.s.pending_ttl_seconds):
            try:
                await bot.send_message(row["chat_id"], "⌛ یکی از درخواست‌های صف‌شده منقضی شد و حذف شد.")
            except Exception:
                pass
        while True:
            job = self.db.next_pending()
            if not job:
                return
            if not self.gemini.any_ready(model_for(job["mode"], self.s)):
                return
            result = await self.process(
                bot, job["chat_id"], job["user_id"], job["text"], job["mode"], from_queue=True)
            if result == "queued":
                return
            self.db.delete_pending(job["id"])

    # -------------------------------------------------------------- commands
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.allowed(update): return
        await update.message.reply_text(
            "ربات شخصی آماده است.\n"
            "دستورات: /chat /ask /code /creative /translate /summarize "
            "/explain /brainstorm\n"
            "حافظه: /remember /memories /forget\n"
            "مدیریت: /model /settings /translation /keys /stats /usage /queue\n"
            "راهنما: /help"
        )

    async def help(self, update, context):
        if not self.allowed(update): return
        await update.message.reply_text(
            "/chat گفت‌وگوی عادی\n/ask پرسش دقیق\n/code برنامه‌نویسی\n"
            "/creative نویسندگی و خلاقیت\n/translate ترجمه\n/summarize خلاصه‌سازی\n"
            "/explain آموزش و توضیح\n/brainstorm ایده‌پردازی\n"
            "نکته: دستور همراه متن (مثلاً /code تابع بنویس) فقط برای همان پیام است؛ "
            "دستور تنهایی حالت را ثابت می‌کند و /chat برگشت به تشخیص خودکار است.\n"
            "/reset پاک‌کردن تاریخچه چت\n/history نمایش تعداد پیام‌ها\n"
            "/remember متن برای حافظه بلندمدت\n/memories دیدن حافظه\n/forget ID حذف یک مورد\n"
            "/model مدل پیشنهادی برای حالت فعلی\n/prompt دیدن پرامپت\n"
            "/setprompt متن تنظیم پرامپت\n/translation on|off\n/keys وضعیت پروژه‌ها\n"
            "/queue صف درخواست‌های منتظر سهمیه\n/clearqueue لغو صف"
        )

    async def mode_command(self, update, context):
        if not self.allowed(update): return
        mode = update.message.text.split()[0].split("@")[0][1:].lower()
        if mode not in MODES:
            return
        payload = update.message.text.partition(" ")[2].strip()
        user_id = update.effective_user.id
        if payload:  # one-shot: does not change the saved mode
            await self.process(context.bot, update.effective_chat.id, user_id, payload, mode)
        else:
            self.db.set_setting(user_id, "mode", mode)
            await update.message.reply_text(f"حالت «{mode}» فعال شد. پیام بعدی را بفرست.")

    async def message(self, update, context):
        if not self.allowed(update) or not update.message or not update.message.text:
            return
        user_id = update.effective_user.id
        text = update.message.text.strip()
        mode = resolve_mode(text, self._mode(user_id))
        await self.process(context.bot, update.effective_chat.id, user_id, text, mode)

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
        await self._send(context.bot, update.effective_chat.id, "\n".join(lines))

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
        self.db.set_setting(update.effective_user.id, "prompt", value[:self.s.prompt_max_chars])
        await update.message.reply_text("پرامپت ذخیره شد.")

    async def model(self, update, context):
        if not self.allowed(update): return
        mode = self._mode(update.effective_user.id)
        await update.message.reply_text(f"حالت: {mode}\nمدل: {model_for(mode, self.s)}")

    async def settings_command(self, update, context):
        if not self.allowed(update): return
        uid = update.effective_user.id
        trans = self.db.get_setting(uid, "translation", str(self.s.translate_enabled))
        await update.message.reply_text(
            f"حالت فعلی: {self._mode(uid)}\nترجمه: {trans}\n"
            f"تعداد turnهای تاریخچه: {self.s.history_turns}\n"
            f"failover روی سهمیه: {self.s.failover_on_429}\n"
            f"حداکثر انتظار هر درخواست: {self.s.max_wait_seconds}s")

    async def translation_toggle(self, update, context):
        if not self.allowed(update): return
        if not context.args or context.args[0].lower() not in ("on", "off"):
            await update.message.reply_text(
                "استفاده: /translation on یا /translation off\n"
                "بدون کلید هم از مترجم وب گوگل استفاده می‌شود؛ این روش غیررسمی است و ممکن است گاهی محدود شود.")
            return
        enabled = context.args[0].lower() == "on"
        self.db.set_setting(update.effective_user.id, "translation", enabled)
        await update.message.reply_text(
            "ترجمه فعال شد (ممکن است کمی کندتر شود؛ حالت‌های code و translate ترجمه نمی‌شن)."
            if enabled else "ترجمه غیرفعال شد؛ پاسخ مستقیم Gemini سریع‌تر است.")

    async def keys(self, update, context):
        if not self.allowed(update): return
        await update.message.reply_text(self.gemini.status())

    async def stats(self, update, context):
        if not self.allowed(update): return
        uid = update.effective_user.id
        u = self.db.usage_summary(uid)
        await update.message.reply_text(
            f"درخواست‌های ثبت‌شده: {u['n']}\nکاراکتر ورودی به Gemini: {u['i']}\n"
            f"کاراکتر خروجی: {u['o']}\nدر صف انتظار: {self.db.pending_count(uid)}")

    async def usage(self, update, context):
        await self.stats(update, context)

    async def queue(self, update, context):
        if not self.allowed(update): return
        rows = self.db.list_pending(update.effective_user.id)
        if not rows:
            await update.message.reply_text("صف خالی است.")
            return
        now = int(time.time())
        lines = [f"{r['id']}. [{r['mode']}] {r['text'][:60]} — {max(0, now - r['created_at']) // 60} دقیقه پیش"
                 for r in rows]
        await update.message.reply_text("درخواست‌های منتظر سهمیه:\n" + "\n".join(lines))

    async def clearqueue(self, update, context):
        if not self.allowed(update): return
        n = self.db.clear_pending(update.effective_user.id)
        await update.message.reply_text(f"{n} درخواست از صف حذف شد.")
