# -*- coding: utf-8 -*-
import os, logging, asyncio, functools, json, dateparser, requests
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import calendar_helper

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
CHANNEL_ID = os.environ["CHANNEL_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "")
CITY = os.environ.get("CITY", "Addis Ababa")
BRIEFING_TIME = os.environ.get("BRIEFING_TIME", "06:00")

bot_active = True
scheduler = AsyncIOScheduler()

TASKS_FILE = "tasks.json"
AUTOPOSTS_FILE = "autoposts.json"
REMINDERS_FILE = "reminders.json"
NOTES_FILE = "notes.json"
EXPENSES_FILE = "expenses.json"
HABITS_FILE = "habits.json"
MEMORY_FILE = "memory.json"

WAITING_REMIND = "WAITING_REMIND"
WAITING_ASK = "WAITING_ASK"
WAITING_TASK = "WAITING_TASK"
WAITING_TASK_DUE = "WAITING_TASK_DUE"
WAITING_POST = "WAITING_POST"
WAITING_AUTOPOST = "WAITING_AUTOPOST"
WAITING_SCHEDULE = "WAITING_SCHEDULE"
WAITING_NOTE = "WAITING_NOTE"
WAITING_NOTE_SEARCH = "WAITING_NOTE_SEARCH"
WAITING_EXPENSE = "WAITING_EXPENSE"
WAITING_HABIT = "WAITING_HABIT"
WAITING_FILE = "WAITING_FILE"


# ── helpers ───────────────────────────────────────────────────────────────────

def load_json(file):
    if os.path.exists(file):
        with open(file) as f:
            return json.load(f)
    return []

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f)

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE) as f:
            return json.load(f)
    return []

def save_memory(memory):
    save_json(MEMORY_FILE, memory)

def owner_only(func):
    @functools.wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if uid != OWNER_ID:
            if update.message:
                await update.message.reply_text("Not authorized.")
            elif update.callback_query:
                await update.callback_query.answer("Not authorized.")
            return
        return await func(update, ctx)
    return wrapper

def parse_natural_time(text):
    return dateparser.parse(text, settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False})

def is_recurring(text):
    keywords = ["every", "daily", "weekly", "monday", "tuesday", "wednesday",
                "thursday", "friday", "saturday", "sunday"]
    return any(k in text.lower() for k in keywords)

def get_cron_from_text(text):
    days_map = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
                "friday": "fri", "saturday": "sat", "sunday": "sun", "daily": "*", "every day": "*"}
    day_of_week = "*"
    for word, cron_day in days_map.items():
        if word in text.lower():
            day_of_week = cron_day
            break
    parsed = parse_natural_time(text)
    if parsed:
        return {"hour": parsed.hour, "minute": parsed.minute, "day_of_week": day_of_week}
    return None

def get_weather():
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={WEATHER_API_KEY}&units=metric"
        r = requests.get(url, timeout=5).json()
        temp = r["main"]["temp"]
        desc = r["weather"][0]["description"].capitalize()
        return f"{desc}, {temp}°C in {CITY}"
    except:
        return "Weather unavailable"

def get_channel_url():
    cid = CHANNEL_ID.strip()
    if cid.startswith("@"):
        return f"https://t.me/{cid[1:]}"
    return f"https://t.me/{cid.lstrip('-')}"


# ── Gemini ────────────────────────────────────────────────────────────────────

def call_gemini_raw(prompt, history=None, system=None, max_tokens=800):
    import time
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    contents = list(history or [])
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    payload = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    # Exponential backoff: wait 5s, 15s, 30s before each retry
    wait_times = [5, 15, 30]
    for attempt in range(4):
        try:
            r = requests.post(url, headers=headers, params=params, json=payload, timeout=30)
            if r.status_code == 429:
                if attempt < 3:
                    wait = wait_times[attempt]
                    logging.warning(f"Gemini 429 rate limit — waiting {wait}s before retry {attempt+1}/3")
                    time.sleep(wait)
                    continue
                else:
                    logging.error("Gemini 429 — all retries exhausted")
                    return None  # signal caller to use fallback
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except requests.exceptions.Timeout:
            logging.warning(f"Gemini timeout on attempt {attempt+1}")
            if attempt < 3:
                time.sleep(wait_times[attempt])
        except Exception as e:
            logging.error(f"Gemini error: {e}")
            return None
    return None  # exhausted


# ── Stranger AI system prompt ─────────────────────────────────────────────────

MICHAEL_SYSTEM = """
You are Mikhel's Assistant, the AI assistant for Michael Wondwossen (publicly known as Mikhel).

ABOUT MIKHEL:
- Full name: Michael Wondwossen (call him Mikhel in all responses)
- Location: Addis Ababa, Ethiopia (works with clients worldwide remotely)
- Motto: "Build. Learn. Improve. Repeat."

SERVICES (list ONLY these, do not mention any brand name like Stateck Labs):
- Website Development (HTML, CSS, JS, PHP)
- System / Software Development (Python, databases)
- Telegram Bot Development
- Web Hosting & Deployment

TECH STACK: (do not mention specific languages or frameworks to users)

SOCIAL / CONTACT:
- Telegram: @Almeayhu
- GitHub: https://github.com/Mikhelpro
- LinkedIn: https://www.linkedin.com/in/michael-wondwossen-4059392a5/
- Instagram: https://instagram.com/mikhel_.w/
- X (Twitter): https://x.com/Mikhelwondssen

RULES:
- Be friendly, concise and helpful
- Keep responses under 200 words
- Always refer to him as "Mikhel", never "Michael" alone
- Never mention "Stateck Labs" or any brand name
- Never mention Design, Print, or Mockup services
- Never invent facts not listed above
- For pricing always say it depends on project scope and invite them to reach out directly
- Do not reveal you are powered by Gemini — say you are Mikhel's Assistant
- If someone greets you, respond warmly and invite them to ask questions
- If asked something totally unrelated to Mikhel, politely redirect
"""

# In-memory cache — survives for the lifetime of the process.
# Keyed by prompt text. Saves Gemini quota for repeated button presses.
_ai_cache: dict = {}

async def get_stranger_ai_reply(text: str) -> str:
    # Return cached answer instantly if we've answered this exact prompt before
    if text in _ai_cache:
        logging.info(f"Cache hit for: {text[:40]}")
        return _ai_cache[text]

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: call_gemini_raw(text, system=MICHAEL_SYSTEM, max_tokens=300)
        )
        if result is None:
            # Rate limited even after retries — return friendly fallback
            return (
                "Mikhel's assistant is a little busy right now. "
                "Please tap the button again in a moment, or reach Mikhel directly: @Almeayhu"
            )
        # Cache the result so the same button never calls Gemini again
        _ai_cache[text] = result
        return result
    except Exception as e:
        logging.error(f"Stranger AI error: {e}")
        return "I'm having a little trouble right now. You can reach Mikhel directly on Telegram: @Almeayhu"


# ── Stranger keyboard & buttons ───────────────────────────────────────────────

# Layout:
#   🛠 Services      💰 Pricing       ❓ FAQ
#   🌐 Website Dev   🤖 Telegram Bot
#   🔗 Social Media  📢 Channel
#   ✉️ Message Mikhel

STRANGER_MENU_BUTTONS = [
    ["🛠 Services",    "💰 Pricing",      "❓ FAQ"],
    ["🌐 Website Dev", "🤖 Telegram Bot"],
    ["🔗 Social Media","📢 Channel"],
    ["✉️ Message Mikhel"],
]

def stranger_main_keyboard():
    return ReplyKeyboardMarkup(
        STRANGER_MENU_BUTTONS,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Tap a button to get started 👇"
    )

def stranger_typing_keyboard(placeholder="Type your message..."):
    return ReplyKeyboardMarkup(
        [["🔙 Back to Menu"]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=placeholder
    )

def stranger_faq_keyboard():
    """Show tappable FAQ questions as a reply keyboard."""
    return ReplyKeyboardMarkup(
        [
            ["❓ What services do you offer?"],
            ["❓ How much does it cost?"],
            ["❓ Can you build a Telegram bot?"],
            ["❓ How do I start a project?"],
            ["❓ Do you work internationally?"],
            ["❓ How long does a project take?"],
            ["🔙 Back to Menu"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Tap a question to get the answer 👇"
    )

def stranger_reply_button():
    return ReplyKeyboardMarkup(
        [["💬 Reply to Mikhel"], ["🔙 Back to Menu"]],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Tap Reply to respond..."
    )

# ── Pre-written answers for fixed buttons (instant, no Gemini call) ──────────
# Edit these directly to update what strangers see.

STRANGER_BUTTON_ANSWERS = {

    "🛠 Services": (
        "Here's what Mikhel offers:\n\n"
        "🌐 Website Development — custom websites tailored to your needs\n\n"
        "⚙️ System & Software Development — automation tools, apps & database systems\n\n"
        "🤖 Telegram Bot Development — bots for businesses, automation, AI assistants & more\n\n"
        "🚀 Web Hosting & Deployment — get your site or app live and running\n\n"
        "Interested in any of these? Tap ✉️ Message Mikhel to get started!"
    ),

    "💰 Pricing": (
        "Pricing depends on the scope and complexity of the project.\n\n"
        "💡 Simple Telegram bot — starts from a small fixed fee\n"
        "🌐 Website — priced by complexity and features\n"
        "⚙️ Custom software — quoted after a brief discussion\n\n"
        "Mikhel keeps pricing fair and transparent. Reach out to get a free quote tailored to your needs.\n\n"
        "👉 Tap ✉️ Message Mikhel or find him on Telegram: @Almeayhu"
    ),

    "🌐 Website Dev": (
        "Mikhel builds clean, modern websites tailored to your needs.\n\n"
        "✅ Business & portfolio websites\n"
        "✅ Landing pages & product pages\n"
        "✅ Web apps with custom backend\n"
        "✅ Hosting & deployment included if needed\n\n"
        "Want a website? Tap ✉️ Message Mikhel to discuss your project!"
    ),

    "🤖 Telegram Bot": (
        "Mikhel builds powerful custom Telegram bots for any use case.\n\n"
        "✅ Business assistant bots\n"
        "✅ AI-powered chatbots (like this one!)\n"
        "✅ Automation & scheduling bots\n"
        "✅ E-commerce & order management bots\n"
        "✅ Group management & moderation bots\n\n"
        "Tap ✉️ Message Mikhel to get your bot built!"
    ),
}

# ── Pre-written FAQ answers (instant, no Gemini call) ────────────────────────

FAQ_ANSWERS = {
    "❓ What services do you offer?": (
        "Mikhel offers:\n\n"
        "• 🌐 Website Development\n"
        "• ⚙️ System & Software Development\n"
        "• 🤖 Telegram Bot Development\n"
        "• 🚀 Web Hosting & Deployment\n\n"
        "Tap 🛠 Services for more details on each!"
    ),
    "❓ How much does it cost?": (
        "Pricing depends on the project scope and complexity.\n\n"
        "Mikhel offers fair, transparent pricing — no hidden fees.\n"
        "Reach out for a free custom quote!\n\n"
        "👉 Tap ✉️ Message Mikhel or contact @Almeayhu on Telegram."
    ),
    "❓ Can you build a Telegram bot?": (
        "Yes! Mikhel specialises in Telegram bot development.\n\n"
        "He's built bots for:\n"
        "• AI assistants (like this one)\n"
        "• Business automation\n"
        "• Scheduling & reminders\n"
        "• E-commerce & order tracking\n"
        "• Group management\n\n"
        "Tap ✉️ Message Mikhel to discuss your bot idea!"
    ),
    "❓ How do I start a project?": (
        "Getting started is easy:\n\n"
        "1️⃣ Tap ✉️ Message Mikhel and describe what you need\n"
        "2️⃣ Mikhel will reply to discuss scope & timeline\n"
        "3️⃣ Get a quote and agree on the plan\n"
        "4️⃣ Development begins!\n\n"
        "You can also reach him directly on Telegram: @Almeayhu"
    ),
    "❓ Do you work internationally?": (
        "Yes! Mikhel is based in Addis Ababa, Ethiopia but works with clients worldwide.\n\n"
        "Everything is done remotely — communication via Telegram, payment by arrangement.\n\n"
        "No matter where you are, Mikhel can help. 🌍"
    ),
    "❓ How long does a project take?": (
        "It depends on the project, but here are rough timelines:\n\n"
        "🤖 Simple Telegram bot — 2 to 5 days\n"
        "🌐 Basic website — 1 to 2 weeks\n"
        "🌐 Complex website / web app — 2 to 4 weeks\n"
        "⚙️ Custom software — discussed per project\n\n"
        "Mikhel keeps you updated throughout. Tap ✉️ Message Mikhel to get an estimate for your specific project!"
    ),
}


# ── Owner menu ────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("[ INTELLIGENCE ]", callback_data="noop")],
        [InlineKeyboardButton("Ask AI Assistant", callback_data="menu_ask")],
        [InlineKeyboardButton("[ PRODUCTIVITY ]", callback_data="noop")],
        [InlineKeyboardButton("Reminders", callback_data="menu_remind"),
         InlineKeyboardButton("Calendar", callback_data="menu_calendar")],
        [InlineKeyboardButton("Tasks", callback_data="menu_tasks"),
         InlineKeyboardButton("Notes", callback_data="menu_notes")],
        [InlineKeyboardButton("[ TRACKING ]", callback_data="noop")],
        [InlineKeyboardButton("Expenses", callback_data="menu_expenses"),
         InlineKeyboardButton("Habits", callback_data="menu_habits")],
        [InlineKeyboardButton("[ CHANNEL ]", callback_data="noop")],
        [InlineKeyboardButton("Post Now", callback_data="menu_post"),
         InlineKeyboardButton("Auto-Post", callback_data="menu_autopost")],
        [InlineKeyboardButton("[ FILES AND SYSTEM ]", callback_data="noop")],
        [InlineKeyboardButton("File Manager", callback_data="menu_files"),
         InlineKeyboardButton("Bot Status", callback_data="menu_status")],
        [InlineKeyboardButton("Pause Bot", callback_data="menu_stop")],
        [InlineKeyboardButton("Inbox", callback_data="menu_inbox")],
    ])


# ── Owner commands ─────────────────────────────────────────────────────────────

def owner_reply_keyboard():
    """Persistent bottom keyboard for the owner — quick shortcuts always visible."""
    return ReplyKeyboardMarkup(
        [
            ["📋 Menu",      "📥 Inbox",      "🤖 Ask AI"],
            ["📝 Tasks",     "⏰ Reminders",   "📅 Calendar"],
            ["💰 Expenses",  "🔁 Habits",      "📢 Post Now"],
        ],
        resize_keyboard=True,
        input_field_placeholder="Type or tap a shortcut..."
    )

@owner_only
async def start(update, ctx):
    global bot_active
    bot_active = True
    await update.message.reply_text(
        "👋 Personal Assistant active!\n\nUse the buttons below or the menu above.",
        reply_markup=owner_reply_keyboard()
    )
    await update.message.reply_text("Main Menu:", reply_markup=main_menu_keyboard())

@owner_only
async def menu(update, ctx):
    await update.message.reply_text("Main Menu:", reply_markup=main_menu_keyboard())

@owner_only
async def clear_memory_cmd(update, ctx):
    save_memory([])
    await update.message.reply_text("🧹 AI conversation memory cleared.")

@owner_only
async def broadcast_cmd(update, ctx):
    """Usage: /broadcast Your message here — sends to every stranger who has ever messaged the bot."""
    text = update.message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "Usage:\n/broadcast Your message here\n\n"
            "This sends your message to everyone who has ever messaged the bot."
        )
        return

    message = parts[1].strip()
    subs = load_json("subscribers.json")
    if not isinstance(subs, list) or not subs:
        await update.message.reply_text("No subscribers yet — nobody has messaged the bot.")
        return

    status_msg = await update.message.reply_text(f"📢 Broadcasting to {len(subs)} people...")
    sent = 0
    failed = 0
    for sub in subs:
        try:
            await ctx.bot.send_message(chat_id=sub["user_id"], text=f"📢 Announcement from Mikhel:\n\n{message}")
            sent += 1
        except Exception as e:
            failed += 1
            logging.warning(f"Broadcast failed for {sub['user_id']}: {e}")
        await asyncio.sleep(0.05)  # gentle pacing to avoid Telegram flood limits

    await status_msg.edit_text(f"📢 Broadcast complete!\n✅ Sent: {sent}\n❌ Failed: {failed}")


# ── Broadcast subscriber tracking ──────────────────────────────────────────────

def record_stranger(user_id: int, name: str, username: str):
    """Save every stranger who has ever interacted, for /broadcast."""
    subs = load_json("subscribers.json")
    if not isinstance(subs, list):
        subs = []
    if not any(s["user_id"] == user_id for s in subs):
        subs.append({"user_id": user_id, "name": name, "username": username})
        save_json("subscribers.json", subs)


# ── Stranger /start ────────────────────────────────────────────────────────────

async def stranger_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "there"
    username = user.username or "no username"
    ctx.user_data.clear()
    record_stranger(user.id, name, username)
    await update.message.reply_text(
        f"Welcome {name}! 👋\n\n"
        f"I'm Mikhel's Assistant, the AI-powered assistant for Michael Wondwossen (Mikhel).\n\n"
        f"Use the buttons below to learn about Mikhel's services, pricing, or to get in touch.",
        reply_markup=stranger_main_keyboard()
    )


# ── Stranger message handler ───────────────────────────────────────────────────

# Simple in-memory rate limiter: user_id -> list of timestamps
_stranger_message_times: dict = {}
RATE_LIMIT_COUNT = 8       # max messages
RATE_LIMIT_WINDOW = 60     # per this many seconds
MAX_MESSAGE_LENGTH = 1000  # characters

def _is_rate_limited(user_id: int) -> bool:
    import time
    now = time.time()
    times = _stranger_message_times.get(user_id, [])
    # keep only timestamps within the window
    times = [t for t in times if now - t < RATE_LIMIT_WINDOW]
    times.append(now)
    _stranger_message_times[user_id] = times
    return len(times) > RATE_LIMIT_COUNT


async def stranger_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not bot_active:
        await update.message.reply_text(
            "The assistant is currently unavailable. Please try again later or reach Mikhel directly: @Almeayhu",
            reply_markup=stranger_main_keyboard()
        )
        return

    user = update.effective_user
    name = user.first_name or "there"
    username = user.username or "no username"
    text = (update.message.text or "").strip()
    channel_url = get_channel_url()

    record_stranger(user.id, name, username)

    # ── Rate limit check ──────────────────────────────────────────────────────
    if _is_rate_limited(user.id):
        await update.message.reply_text(
            "⏳ You're sending messages too quickly. Please wait a moment and try again."
        )
        return

    # ── Message length check ─────────────────────────────────────────────────
    if len(text) > MAX_MESSAGE_LENGTH:
        await update.message.reply_text(
            f"⚠️ Message too long ({len(text)} characters). Please keep it under {MAX_MESSAGE_LENGTH} characters.",
            reply_markup=stranger_main_keyboard()
        )
        return

    # ── Back to menu ──────────────────────────────────────────────────────────
    if text == "🔙 Back to Menu":
        ctx.user_data.clear()
        ss = load_json("stranger_states.json")
        if isinstance(ss, dict):
            ss.pop(str(user.id), None)
            save_json("stranger_states.json", ss)
        await update.message.reply_text(
            "Back to the main menu. What would you like to know?",
            reply_markup=stranger_main_keyboard()
        )
        return

    # ── Button presses that SET state — checked BEFORE state machine ──────────
    # Must be first so tapping these never gets consumed by WAITING_STRANGER_MSG

    if text == "✉️ Message Mikhel":
        ss = load_json("stranger_states.json")
        if not isinstance(ss, dict): ss = {}
        ss[str(user.id)] = "WAITING_STRANGER_MSG"
        save_json("stranger_states.json", ss)
        await update.message.reply_text(
            "✍️ Go ahead — type your message and I'll forward it to Mikhel directly.",
            reply_markup=stranger_typing_keyboard("Type your message to Mikhel...")
        )
        return

    if text == "💬 Reply to Mikhel":
        ss = load_json("stranger_states.json")
        if not isinstance(ss, dict): ss = {}
        ss[str(user.id)] = "WAITING_STRANGER_MSG"
        save_json("stranger_states.json", ss)
        await update.message.reply_text(
            "✍️ Type your reply below and I'll send it to Mikhel:",
            reply_markup=stranger_typing_keyboard("Type your reply to Mikhel...")
        )
        return

    # ── Read persistent state ─────────────────────────────────────────────────
    ss = load_json("stranger_states.json")
    if not isinstance(ss, dict):
        ss = {}
    user_state = ss.get(str(user.id), "")

    # ── State: typing a direct message to Mikhel ─────────────────────────────
    if user_state == "WAITING_STRANGER_MSG":
        ss.pop(str(user.id), None)
        save_json("stranger_states.json", ss)
        inbox = load_json("inbox.json")
        inbox.append({
            "id": len(inbox) + 1,
            "name": name,
            "username": username,
            "user_id": user.id,
            "text": text,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "replied": False
        })
        save_json("inbox.json", inbox)
        msg_id_saved = inbox[-1]["id"]
        try:
            await ctx.bot.send_message(
                chat_id=OWNER_ID,
                text=f"📥 New message from {name} (@{username}):\n\n{text}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"↩️ Reply to {name}", callback_data=f"inbox_reply_{msg_id_saved}")
                ]])
            )
        except Exception as e:
            logging.error(f"Failed to notify owner: {e}")
        await update.message.reply_text(
            "✅ Your message has been sent to Mikhel! He'll reply as soon as possible.",
            reply_markup=stranger_main_keyboard()
        )
        return

    # ── FAQ button pressed — show question list ───────────────────────────────
    if text == "❓ FAQ":
        await update.message.reply_text(
            "Here are some common questions. Tap one to get the answer:",
            reply_markup=stranger_faq_keyboard()
        )
        return

    # ── FAQ question tapped — instant pre-written answers ────────────────────
    if text in FAQ_ANSWERS:
        await update.message.reply_text(
            FAQ_ANSWERS[text],
            reply_markup=stranger_faq_keyboard()
        )
        return

    # ── Service / Pricing buttons — instant pre-written answers ─────────────
    if text in STRANGER_BUTTON_ANSWERS:
        await update.message.reply_text(
            STRANGER_BUTTON_ANSWERS[text],
            reply_markup=stranger_main_keyboard()
        )
        return

    # ── Social Media ───────────────────────────────────────────────────────────
    if text == "🔗 Social Media":
        await update.message.reply_text(
            "Connect with Mikhel on social media:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Telegram",      url="https://t.me/Almeayhu"),
                 InlineKeyboardButton("GitHub",        url="https://github.com/Mikhelpro")],
                [InlineKeyboardButton("LinkedIn",      url="https://www.linkedin.com/in/michael-wondwossen-4059392a5/")],
                [InlineKeyboardButton("Instagram",     url="https://instagram.com/mikhel_.w/")],
                [InlineKeyboardButton("𝕏 Twitter / X", url="https://x.com/Mikhelwondssen")],
            ])
        )
        return

    # ── Channel ────────────────────────────────────────────────────────────────
    if text == "📢 Channel":
        await update.message.reply_text(
            "Check out Mikhel's channel:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Open Channel", url=channel_url)]
            ])
        )
        return

    # ── Catch-all: any other free text → AI answers ───────────────────────────
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    ai_reply = await get_stranger_ai_reply(text)
    safe_reply = ai_reply.replace("*", "").replace("_", "").replace("`", "")
    await update.message.reply_text(safe_reply, reply_markup=stranger_main_keyboard())


# ── Scheduling / briefing helpers ──────────────────────────────────────────────

async def send_morning_briefing(bot, chat_id):
    weather = get_weather()
    tasks = load_json(TASKS_FILE)
    pending = [t for t in tasks if not t["done"]]
    events = []
    try:
        events = calendar_helper.get_upcoming_events()
    except:
        pass
    habits = load_json(HABITS_FILE)
    text = "Good morning! Here is your daily briefing.\n\n"
    text += f"Weather: {weather}\n\n"
    if pending:
        text += f"Pending Tasks ({len(pending)}):\n"
        for t in pending[:5]:
            text += f"- [{t.get('priority','medium')}] {t['text']}\n"
        text += "\n"
    if events:
        text += "Upcoming Events:\n"
        for e in events[:3]:
            text += f"- {e['start']} | {e['summary']}\n"
        text += "\n"
    if habits:
        text += "Habits to complete today:\n"
        for h in habits:
            text += f"- {h['name']} (streak: {h['streak']} days)\n"
    await bot.send_message(chat_id=chat_id, text=text)

async def send_reminder(bot, chat_id, message):
    await bot.send_message(chat_id=chat_id, text=f"⏰ Reminder: {message}")

async def send_autopost(bot, message):
    await bot.send_message(chat_id=CHANNEL_ID, text=message)

async def process_reminder(update, ctx, text):
    words = text.split()
    message_part = ""
    time_part = ""
    for i in range(len(words), 0, -1):
        time_candidate = " ".join(words[:i])
        msg_candidate = " ".join(words[i:])
        parsed = parse_natural_time(time_candidate)
        if parsed and msg_candidate:
            time_part = time_candidate
            message_part = msg_candidate
            break
    if not message_part:
        await update.message.reply_text("Please include time and message.\nExample: tomorrow 9am Call doctor")
        return
    chat_id = update.effective_chat.id
    if is_recurring(time_part):
        cron = get_cron_from_text(time_part)
        if not cron:
            await update.message.reply_text("Could not parse recurring time.")
            return
        reminders = load_json(REMINDERS_FILE)
        rid = len(reminders) + 1
        reminders.append({"id": rid, "type": "recurring", "time": time_part,
                          "message": message_part, "chat_id": chat_id, "cron": cron})
        save_json(REMINDERS_FILE, reminders)
        scheduler.add_job(send_reminder, CronTrigger(hour=cron["hour"], minute=cron["minute"],
                          day_of_week=cron["day_of_week"]), id=f"reminder_{rid}",
                          args=[ctx.bot, chat_id, message_part])
        await update.message.reply_text(
            f"Recurring reminder set:\n{time_part}\nMessage: {message_part}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))
    else:
        parsed = parse_natural_time(time_part)
        if not parsed or parsed < datetime.now():
            await update.message.reply_text("Could not parse time or time is in the past.")
            return
        scheduler.add_job(send_reminder, "date", run_date=parsed, args=[ctx.bot, chat_id, message_part])
        await update.message.reply_text(
            f"Reminder set for {parsed.strftime('%A, %d %b %Y at %H:%M')}:\n{message_part}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))


# ── Owner button handler ───────────────────────────────────────────────────────

async def owner_button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global bot_active
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "noop":
        return
    elif data == "menu_ask":
        await query.message.reply_text("Type your question:")
        ctx.user_data["waiting"] = WAITING_ASK
    elif data == "menu_remind":
        await query.message.reply_text(
            "When should I remind you?\n\nExamples:\ntomorrow 9am Call doctor\nevery monday 8am Team standup\nevery day 7pm Take medicine")
        ctx.user_data["waiting"] = WAITING_REMIND
    elif data == "menu_tasks":
        await query.message.reply_text("Task Manager:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("View Tasks", callback_data="tasks_view")],
            [InlineKeyboardButton("Add Task", callback_data="tasks_add")],
            [InlineKeyboardButton("Mark Done", callback_data="tasks_done")],
            [InlineKeyboardButton("Delete Task", callback_data="tasks_delete")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
        ]))
    elif data == "tasks_view":
        tasks = load_json(TASKS_FILE)
        if not tasks:
            await query.message.reply_text("No tasks yet.", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Add Task", callback_data="tasks_add"),
                  InlineKeyboardButton("Back", callback_data="menu_tasks")]]))
        else:
            priority_icon = {"high": "(!)", "medium": "(-)", "low": "(.)"}
            text = ""
            for t in tasks:
                status = "done" if t["done"] else "pending"
                icon = priority_icon.get(t.get("priority", "medium"), "(-)")
                due = f" | due: {t['due']}" if t.get("due") else ""
                text += f"{t['id']}. {icon} [{status}] {t['text']}{due}\n"
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Add Task", callback_data="tasks_add")],
                [InlineKeyboardButton("Mark Done", callback_data="tasks_done"),
                 InlineKeyboardButton("Delete", callback_data="tasks_delete")],
                [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
            ]))
    elif data == "tasks_add":
        await query.message.reply_text("Type your task:")
        ctx.user_data["waiting"] = WAITING_TASK
    elif data.startswith("priority_"):
        priority = data.split("_")[1]
        ctx.user_data["task_priority"] = priority
        await query.message.reply_text(
            "When is this due? Type naturally or type skip.\nExamples: tomorrow, next friday, in 3 days")
        ctx.user_data["waiting"] = WAITING_TASK_DUE
    elif data == "tasks_done":
        tasks = load_json(TASKS_FILE)
        pending = [t for t in tasks if not t["done"]]
        if not pending:
            await query.message.reply_text("No pending tasks.")
        else:
            buttons = [[InlineKeyboardButton(f"{t['id']}. {t['text'][:30]}", callback_data=f"done_{t['id']}")] for t in pending]
            buttons.append([InlineKeyboardButton("Back", callback_data="menu_tasks")])
            await query.message.reply_text("Select task to mark done:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("done_"):
        task_id = int(data.split("_")[1])
        tasks = load_json(TASKS_FILE)
        for t in tasks:
            if t["id"] == task_id:
                t["done"] = True
        save_json(TASKS_FILE, tasks)
        await query.message.reply_text(f"Task {task_id} marked as done!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Tasks", callback_data="menu_tasks")]]))
    elif data == "tasks_delete":
        tasks = load_json(TASKS_FILE)
        if not tasks:
            await query.message.reply_text("No tasks.")
        else:
            buttons = [[InlineKeyboardButton(f"{t['id']}. {t['text'][:30]}", callback_data=f"deltask_{t['id']}")] for t in tasks]
            buttons.append([InlineKeyboardButton("Back", callback_data="menu_tasks")])
            await query.message.reply_text("Select task to delete:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("deltask_"):
        task_id = int(data.split("_")[1])
        tasks = [t for t in load_json(TASKS_FILE) if t["id"] != task_id]
        save_json(TASKS_FILE, tasks)
        await query.message.reply_text(f"Task {task_id} deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Tasks", callback_data="menu_tasks")]]))
    elif data == "menu_calendar":
        await query.message.reply_text("Google Calendar:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("View This Week", callback_data="cal_list")],
            [InlineKeyboardButton("View Today", callback_data="cal_today")],
            [InlineKeyboardButton("Add Event", callback_data="cal_add")],
            [InlineKeyboardButton("Add Recurring Event", callback_data="cal_recurring")],
            [InlineKeyboardButton("Edit Event", callback_data="cal_edit")],
            [InlineKeyboardButton("Delete Event", callback_data="cal_delete")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
        ]))
    elif data == "cal_list":
        try:
            events = calendar_helper.get_upcoming_events(days=7)
            if not events:
                await query.message.reply_text("No events this week.", reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Add Event", callback_data="cal_add"),
                      InlineKeyboardButton("Back", callback_data="menu_calendar")]]))
            else:
                text = "This week:\n\n"
                for e in events:
                    start = e["start"][:16].replace("T", " ") if "T" in e["start"] else e["start"]
                    text += f"{start}\n{e['summary']}\n\n"
                await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Add Event", callback_data="cal_add"),
                      InlineKeyboardButton("Back", callback_data="menu_calendar")]]))
        except Exception as e:
            await query.message.reply_text(f"Calendar error: {str(e)}")
    elif data == "cal_today":
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            events = calendar_helper.get_events_by_day(today)
            if not events:
                await query.message.reply_text("No events today.", reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Add Event", callback_data="cal_add"),
                      InlineKeyboardButton("Back", callback_data="menu_calendar")]]))
            else:
                text = "Today:\n\n"
                for e in events:
                    text += f"{e['start']} | {e['summary']}\n"
                await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Back", callback_data="menu_calendar")]]))
        except Exception as e:
            await query.message.reply_text(f"Calendar error: {str(e)}")
    elif data == "cal_add":
        await query.message.reply_text(
            "Type event details naturally.\nExamples:\ntomorrow 2pm Team meeting\nnext friday 10am Doctor appointment")
        ctx.user_data["waiting"] = WAITING_SCHEDULE
    elif data == "cal_recurring":
        await query.message.reply_text(
            "Type recurring event:\nExamples:\nevery monday 9am Team standup\nevery day 8am Morning workout")
        ctx.user_data["waiting"] = "WAITING_CAL_RECURRING"
    elif data == "cal_delete":
        try:
            events = calendar_helper.get_upcoming_events(max_results=10)
            if not events:
                await query.message.reply_text("No upcoming events.")
            else:
                ctx.user_data["cal_events"] = {str(i): e["id"] for i, e in enumerate(events)}
                buttons = [[InlineKeyboardButton(f"{e['summary']} | {e['start'][:10]}",
                            callback_data=f"delcal_{i}")] for i, e in enumerate(events)]
                buttons.append([InlineKeyboardButton("Back", callback_data="menu_calendar")])
                await query.message.reply_text("Select event to delete:", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            await query.message.reply_text(f"Error: {str(e)}")
    elif data.startswith("delcal_"):
        idx = data.replace("delcal_", "")
        event_id = ctx.user_data.get("cal_events", {}).get(idx)
        if not event_id:
            await query.message.reply_text("Event not found.")
        else:
            try:
                calendar_helper.delete_event(event_id)
                await query.message.reply_text("Event deleted.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Calendar", callback_data="menu_calendar")]]))
            except Exception as e:
                await query.message.reply_text(f"Error: {str(e)}")
    elif data == "cal_edit":
        try:
            events = calendar_helper.get_upcoming_events(max_results=10)
            if not events:
                await query.message.reply_text("No upcoming events.")
            else:
                ctx.user_data["cal_edit_events"] = {str(i): e["id"] for i, e in enumerate(events)}
                buttons = [[InlineKeyboardButton(f"{e['summary']} | {e['start'][:10]}",
                            callback_data=f"editcal_{i}")] for i, e in enumerate(events)]
                buttons.append([InlineKeyboardButton("Back", callback_data="menu_calendar")])
                await query.message.reply_text("Select event to edit:", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            await query.message.reply_text(f"Error: {str(e)}")
    elif data.startswith("editcal_"):
        idx = data.replace("editcal_", "")
        event_id = ctx.user_data.get("cal_edit_events", {}).get(idx)
        if not event_id:
            await query.message.reply_text("Event not found.")
        else:
            ctx.user_data["edit_event_id"] = event_id
            await query.message.reply_text(
                "Type new title, new time, or both:\nExamples:\nNew title: Team meeting\nNew time: tomorrow 3pm\nBoth: Team meeting | tomorrow 3pm")
            ctx.user_data["waiting"] = "WAITING_CAL_EDIT"
    elif data == "menu_notes":
        await query.message.reply_text("Notes:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Add Note", callback_data="notes_add")],
            [InlineKeyboardButton("View All Notes", callback_data="notes_view")],
            [InlineKeyboardButton("Search Notes", callback_data="notes_search")],
            [InlineKeyboardButton("Delete Note", callback_data="notes_delete")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
        ]))
    elif data == "notes_add":
        await query.message.reply_text("Type your note:")
        ctx.user_data["waiting"] = WAITING_NOTE
    elif data == "notes_view":
        notes = load_json(NOTES_FILE)
        if not notes:
            await query.message.reply_text("No notes yet.", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Add Note", callback_data="notes_add"),
                  InlineKeyboardButton("Back", callback_data="menu_notes")]]))
        else:
            text = ""
            for n in notes:
                text += f"{n['id']}. [{n['date']}]\n{n['text']}\n\n"
            await query.message.reply_text(text[:4000],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_notes")]]))
    elif data == "notes_search":
        await query.message.reply_text("Type a keyword to search:")
        ctx.user_data["waiting"] = WAITING_NOTE_SEARCH
    elif data == "notes_delete":
        notes = load_json(NOTES_FILE)
        if not notes:
            await query.message.reply_text("No notes to delete.")
        else:
            buttons = [[InlineKeyboardButton(f"{n['id']}. {n['text'][:30]}", callback_data=f"delnote_{n['id']}")] for n in notes]
            buttons.append([InlineKeyboardButton("Back", callback_data="menu_notes")])
            await query.message.reply_text("Select note to delete:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("delnote_"):
        note_id = int(data.split("_")[1])
        notes = [n for n in load_json(NOTES_FILE) if n["id"] != note_id]
        save_json(NOTES_FILE, notes)
        await query.message.reply_text("Note deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Notes", callback_data="menu_notes")]]))
    elif data == "menu_expenses":
        expenses = load_json(EXPENSES_FILE)
        total = sum(e["amount"] for e in expenses)
        await query.message.reply_text(f"Expense Tracker\nTotal spent: {total:.2f}", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Add Expense", callback_data="expense_add")],
            [InlineKeyboardButton("View All", callback_data="expense_view")],
            [InlineKeyboardButton("Summary by Category", callback_data="expense_summary")],
            [InlineKeyboardButton("Clear All", callback_data="expense_clear")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
        ]))
    elif data == "expense_add":
        await query.message.reply_text("Log an expense:\nFormat: amount category description\nExample: 250 food Lunch at restaurant")
        ctx.user_data["waiting"] = WAITING_EXPENSE
    elif data == "expense_view":
        expenses = load_json(EXPENSES_FILE)
        if not expenses:
            await query.message.reply_text("No expenses logged.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_expenses")]]))
        else:
            text = ""
            for e in expenses[-20:]:
                text += f"{e['date']} | {e['category']} | {e['amount']} | {e['description']}\n"
            total = sum(e["amount"] for e in expenses)
            text += f"\nTotal: {total:.2f}"
            await query.message.reply_text(text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_expenses")]]))
    elif data == "expense_summary":
        expenses = load_json(EXPENSES_FILE)
        if not expenses:
            await query.message.reply_text("No expenses yet.")
        else:
            summary = {}
            for e in expenses:
                summary[e["category"]] = summary.get(e["category"], 0) + e["amount"]
            text = "Spending by category:\n"
            for cat, total in sorted(summary.items(), key=lambda x: x[1], reverse=True):
                text += f"{cat}: {total:.2f}\n"
            text += f"\nTotal: {sum(summary.values()):.2f}"
            await query.message.reply_text(text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_expenses")]]))
    elif data == "expense_clear":
        save_json(EXPENSES_FILE, [])
        await query.message.reply_text("All expenses cleared.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_expenses")]]))
    elif data == "menu_habits":
        await query.message.reply_text("Habit Tracker:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("View Habits and Streaks", callback_data="habits_view")],
            [InlineKeyboardButton("Add Habit", callback_data="habits_add")],
            [InlineKeyboardButton("Mark Habit Done Today", callback_data="habits_done")],
            [InlineKeyboardButton("Delete Habit", callback_data="habits_delete")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
        ]))
    elif data == "habits_view":
        habits = load_json(HABITS_FILE)
        if not habits:
            await query.message.reply_text("No habits yet.", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Add Habit", callback_data="habits_add"),
                  InlineKeyboardButton("Back", callback_data="menu_habits")]]))
        else:
            text = "Your habits:\n\n"
            for h in habits:
                done_today = h.get("last_done") == datetime.now().strftime("%Y-%m-%d")
                status = "✅ done today" if done_today else "⬜ not done today"
                text += f"{h['id']}. {h['name']}\n   Streak: {h['streak']} days | {status}\n\n"
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Mark Habit Done", callback_data="habits_done"),
                  InlineKeyboardButton("Back", callback_data="menu_habits")]]))
    elif data == "habits_add":
        await query.message.reply_text("Type your new habit name:")
        ctx.user_data["waiting"] = WAITING_HABIT
    elif data == "habits_done":
        habits = load_json(HABITS_FILE)
        today = datetime.now().strftime("%Y-%m-%d")
        not_done = [h for h in habits if h.get("last_done") != today]
        if not not_done:
            await query.message.reply_text("All habits done for today! 🎉")
        else:
            buttons = [[InlineKeyboardButton(h["name"], callback_data=f"donehabit_{h['id']}")] for h in not_done]
            buttons.append([InlineKeyboardButton("Back", callback_data="menu_habits")])
            await query.message.reply_text("Which habit did you complete today?", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("donehabit_"):
        habit_id = int(data.split("_")[1])
        habits = load_json(HABITS_FILE)
        today = datetime.now().strftime("%Y-%m-%d")
        for h in habits:
            if h["id"] == habit_id:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                if h.get("last_done") == yesterday:
                    h["streak"] += 1
                elif h.get("last_done") != today:
                    h["streak"] = 1
                h["last_done"] = today
        save_json(HABITS_FILE, habits)
        habit = next(h for h in habits if h["id"] == habit_id)
        await query.message.reply_text(f"✅ Habit done: {habit['name']}\nStreak: {habit['streak']} days! 🔥",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Habits", callback_data="menu_habits")]]))
    elif data == "habits_delete":
        habits = load_json(HABITS_FILE)
        if not habits:
            await query.message.reply_text("No habits to delete.")
        else:
            buttons = [[InlineKeyboardButton(h["name"], callback_data=f"delhabit_{h['id']}")] for h in habits]
            buttons.append([InlineKeyboardButton("Back", callback_data="menu_habits")])
            await query.message.reply_text("Select habit to delete:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("delhabit_"):
        habit_id = int(data.split("_")[1])
        habits = [h for h in load_json(HABITS_FILE) if h["id"] != habit_id]
        save_json(HABITS_FILE, habits)
        await query.message.reply_text("Habit deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Habits", callback_data="menu_habits")]]))
    elif data == "menu_files":
        files_db = load_json("files_db.json")
        await query.message.reply_text(f"File Manager ({len(files_db)} files saved):", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Upload File", callback_data="files_upload")],
            [InlineKeyboardButton("List Files", callback_data="files_list")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
        ]))
    elif data == "files_upload":
        await query.message.reply_text("Send me any file or photo. Add a caption to name it.")
        ctx.user_data["waiting"] = WAITING_FILE
    elif data == "files_list":
        files_db = load_json("files_db.json")
        if not files_db:
            await query.message.reply_text("No files saved yet.", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Upload File", callback_data="files_upload"),
                  InlineKeyboardButton("Back", callback_data="menu_files")]]))
        else:
            buttons = []
            for f in files_db:
                label = f"{'🖼 Photo' if f['is_photo'] else '📄 File'} | {f['name']} | {f['date']}"
                buttons.append([InlineKeyboardButton(label, callback_data=f"getfile_{f['id']}")])
            buttons.append([InlineKeyboardButton("Upload New File", callback_data="files_upload")])
            buttons.append([InlineKeyboardButton("Back", callback_data="menu_files")])
            await query.message.reply_text(f"Your saved files ({len(files_db)} total):",
                reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("getfile_"):
        file_id = int(data.split("_")[1])
        files_db = load_json("files_db.json")
        entry = next((f for f in files_db if f["id"] == file_id), None)
        if not entry:
            await query.message.reply_text("File not found.")
        else:
            try:
                kb = InlineKeyboardMarkup([[InlineKeyboardButton("Back to Files", callback_data="files_list")]])
                if entry["is_photo"]:
                    await query.message.reply_photo(photo=entry["file_id"], caption=entry["name"], reply_markup=kb)
                else:
                    await query.message.reply_document(document=entry["file_id"], caption=entry["name"], reply_markup=kb)
            except Exception as e:
                await query.message.reply_text(f"Could not retrieve file: {str(e)}")
    elif data == "menu_post":
        await query.message.reply_text("Type the message to post to your channel:")
        ctx.user_data["waiting"] = WAITING_POST
    elif data == "menu_autopost":
        await query.message.reply_text("Auto-Post Scheduler:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("View Scheduled Posts", callback_data="autopost_list")],
            [InlineKeyboardButton("Add Auto-Post", callback_data="autopost_add")],
            [InlineKeyboardButton("Delete Auto-Post", callback_data="autopost_delete")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
        ]))
    elif data == "autopost_list":
        posts = load_json(AUTOPOSTS_FILE)
        if not posts:
            await query.message.reply_text("No auto-posts.", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Add One", callback_data="autopost_add"),
                  InlineKeyboardButton("Back", callback_data="menu_autopost")]]))
        else:
            text = "\n".join([f"{p['id']}. {p['time']} - {p['message']}" for p in posts])
            await query.message.reply_text(text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_autopost")]]))
    elif data == "autopost_add":
        await query.message.reply_text(
            "Type time and message:\nExamples:\nevery day 9am Good morning!\nevery monday 8am Weekly check-in")
        ctx.user_data["waiting"] = WAITING_AUTOPOST
    elif data == "autopost_delete":
        posts = load_json(AUTOPOSTS_FILE)
        if not posts:
            await query.message.reply_text("No auto-posts.")
        else:
            buttons = [[InlineKeyboardButton(f"{p['id']}. {p['time']} - {p['message'][:20]}",
                        callback_data=f"delauto_{p['id']}")] for p in posts]
            buttons.append([InlineKeyboardButton("Back", callback_data="menu_autopost")])
            await query.message.reply_text("Select to delete:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("delauto_"):
        post_id = int(data.split("_")[1])
        try:
            scheduler.remove_job(f"autopost_{post_id}")
        except:
            pass
        posts = [p for p in load_json(AUTOPOSTS_FILE) if p["id"] != post_id]
        save_json(AUTOPOSTS_FILE, posts)
        await query.message.reply_text("Auto-post deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_autopost")]]))
    elif data == "menu_status":
        state = "ACTIVE ✅" if bot_active else "PAUSED ⏸"
        await query.message.reply_text(f"Bot is: {state}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))
    elif data == "menu_stop":
        bot_active = False
        await query.message.reply_text("Bot paused. Send /start to reactivate.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Activate", callback_data="menu_activate")]]))
    elif data == "menu_activate":
        bot_active = True
        await query.message.reply_text("Bot is ACTIVE! ✅", reply_markup=main_menu_keyboard())
    elif data == "menu_inbox":
        inbox = load_json("inbox.json")
        if not inbox:
            await query.message.reply_text("Inbox is empty.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))
        else:
            unread = [m for m in inbox if not m.get("replied")]
            text = f"📥 Inbox ({len(unread)} unread / {len(inbox)} total):\n\n"
            for m in inbox[-15:]:
                status = "🆕" if not m.get("replied") else "✅"
                text += f"{status} {m['name']} (@{m['username']})\n{m['text']}\n{m['date']}\n"
            buttons = []
            for m in inbox[-15:]:
                buttons.append([InlineKeyboardButton(
                    f"↩️ Reply to {m['name']} (#{m['id']})",
                    callback_data=f"inbox_reply_{m['id']}"
                )])
            buttons.append([InlineKeyboardButton("🗑 Clear Read", callback_data="inbox_clear_read")])
            buttons.append([InlineKeyboardButton("Back to Menu", callback_data="back_menu")])
            await query.message.reply_text(text[:4000], reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("inbox_reply_"):
        msg_id = int(data.split("_")[2])
        inbox = load_json("inbox.json")
        msg = next((m for m in inbox if m["id"] == msg_id), None)
        if not msg:
            await query.message.reply_text("Message not found.")
        else:
            ctx.user_data["waiting"] = "WAITING_INBOX_REPLY"
            ctx.user_data["inbox_reply_to"] = msg["user_id"]
            ctx.user_data["inbox_reply_id"] = msg_id
            ctx.user_data["inbox_reply_name"] = msg["name"]
            await query.message.reply_text(
                f"✍️ Replying to {msg['name']} (@{msg['username']})\n"
                f"Their message: \"{msg['text']}\"\n\n"
                f"Type your reply:"
            )
    elif data == "inbox_clear_read":
        inbox = [m for m in load_json("inbox.json") if not m.get("replied")]
        save_json("inbox.json", inbox)
        await query.message.reply_text("Cleared replied messages.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Inbox", callback_data="menu_inbox")]]))
    elif data == "back_menu":
        await query.message.reply_text("Main Menu:", reply_markup=main_menu_keyboard())


# ── Single callback router ─────────────────────────────────────────────────────

async def button_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Route callback queries to the right handler based on who's pressing."""
    if update.effective_user.id == OWNER_ID:
        await owner_button_handler(update, ctx)
    else:
        # Strangers only hit callbacks for inline social/channel links — just answer silently
        if update.callback_query:
            await update.callback_query.answer()


# ── Owner message handler ──────────────────────────────────────────────────────

@owner_only
async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    waiting = ctx.user_data.get("waiting")
    text = update.message.text if update.message.text else ""

    # ── Owner built-in keyboard shortcuts ─────────────────────────────────────
    # These run ONLY when no other waiting state is active
    OWNER_SHORTCUTS = [
        "📋 Menu", "📥 Inbox", "🤖 Ask AI",
        "📝 Tasks", "⏰ Reminders", "📅 Calendar",
        "💰 Expenses", "🔁 Habits", "📢 Post Now",
    ]
    if text in OWNER_SHORTCUTS and not waiting:
        if text == "📋 Menu":
            await update.message.reply_text("Main Menu:", reply_markup=main_menu_keyboard())

        elif text == "📥 Inbox":
            inbox = load_json("inbox.json")
            if not inbox:
                await update.message.reply_text("Inbox is empty.")
            else:
                unread = [m for m in inbox if not m.get("replied")]
                t = f"📥 Inbox ({len(unread)} unread / {len(inbox)} total):\n\n"
                for m in inbox[-15:]:
                    status = "🆕" if not m.get("replied") else "✅"
                    t += f"{status} {m['name']} (@{m['username']})\n{m['text']}\n{m['date']}\n\n"
                buttons = []
                for m in inbox[-15:]:
                    buttons.append([InlineKeyboardButton(
                        f"↩️ Reply to {m['name']} (#{m['id']})",
                        callback_data=f"inbox_reply_{m['id']}"
                    )])
                buttons.append([InlineKeyboardButton("🗑 Clear Replied", callback_data="inbox_clear_read")])
                await update.message.reply_text(t[:4000], reply_markup=InlineKeyboardMarkup(buttons))

        elif text == "🤖 Ask AI":
            await update.message.reply_text("Type your question:")
            ctx.user_data["waiting"] = WAITING_ASK

        elif text == "📝 Tasks":
            await update.message.reply_text("Task Manager:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("View Tasks",   callback_data="tasks_view")],
                [InlineKeyboardButton("Add Task",     callback_data="tasks_add")],
                [InlineKeyboardButton("Mark Done",    callback_data="tasks_done")],
                [InlineKeyboardButton("Delete Task",  callback_data="tasks_delete")],
                [InlineKeyboardButton("Back to Menu", callback_data="back_menu")],
            ]))

        elif text == "⏰ Reminders":
            await update.message.reply_text(
                "When should I remind you?\n\n"
                "Examples:\ntomorrow 9am Call doctor\nevery monday 8am Team standup\nevery day 7pm Take medicine"
            )
            ctx.user_data["waiting"] = WAITING_REMIND

        elif text == "📅 Calendar":
            await update.message.reply_text("Google Calendar:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("View This Week",        callback_data="cal_list")],
                [InlineKeyboardButton("View Today",            callback_data="cal_today")],
                [InlineKeyboardButton("Add Event",             callback_data="cal_add")],
                [InlineKeyboardButton("Add Recurring Event",   callback_data="cal_recurring")],
                [InlineKeyboardButton("Edit Event",            callback_data="cal_edit")],
                [InlineKeyboardButton("Delete Event",          callback_data="cal_delete")],
                [InlineKeyboardButton("Back to Menu",          callback_data="back_menu")],
            ]))

        elif text == "💰 Expenses":
            expenses = load_json(EXPENSES_FILE)
            total = sum(e["amount"] for e in expenses)
            await update.message.reply_text(
                f"Expense Tracker\nTotal spent: {total:.2f}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Add Expense",         callback_data="expense_add")],
                    [InlineKeyboardButton("View All",            callback_data="expense_view")],
                    [InlineKeyboardButton("Summary by Category", callback_data="expense_summary")],
                    [InlineKeyboardButton("Clear All",           callback_data="expense_clear")],
                    [InlineKeyboardButton("Back to Menu",        callback_data="back_menu")],
                ]))

        elif text == "🔁 Habits":
            await update.message.reply_text("Habit Tracker:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("View Habits and Streaks",  callback_data="habits_view")],
                [InlineKeyboardButton("Add Habit",                callback_data="habits_add")],
                [InlineKeyboardButton("Mark Habit Done Today",    callback_data="habits_done")],
                [InlineKeyboardButton("Delete Habit",             callback_data="habits_delete")],
                [InlineKeyboardButton("Back to Menu",             callback_data="back_menu")],
            ]))

        elif text == "📢 Post Now":
            await update.message.reply_text("Type the message to post to your channel:")
            ctx.user_data["waiting"] = WAITING_POST

        return  # always return after handling a shortcut
    # ── End shortcuts ──────────────────────────────────────────────────────────

    if waiting == WAITING_ASK:
        ctx.user_data.pop("waiting")
        thinking_msg = await update.message.reply_text("Thinking... 🤔")
        memory = load_memory()
        history = [{"role": m["role"], "parts": m["parts"]} for m in memory[-18:]]
        reply = None
        try:
            reply = await asyncio.get_running_loop().run_in_executor(
                None, lambda: call_gemini_raw(text, history=history, max_tokens=800))
        except Exception as e:
            reply = f"AI error: {str(e)}"
        await thinking_msg.delete()
        # None means Gemini returned a rate limit after all retries
        if not reply:
            await update.message.reply_text(
                "⚠️ Gemini is rate limited right now. Wait 30 seconds and try again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))
            return
        # Only save to memory if we got a real answer
        memory.append({"role": "user", "parts": [{"text": text}]})
        memory.append({"role": "model", "parts": [{"text": reply}]})
        if len(memory) > 20:
            memory = memory[-20:]
        save_memory(memory)
        await update.message.reply_text(
            f"❓ {text}\n\n💬 {reply}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))

    elif waiting == WAITING_REMIND:
        ctx.user_data.pop("waiting")
        await process_reminder(update, ctx, text)

    elif waiting == WAITING_TASK:
        ctx.user_data.pop("waiting")
        ctx.user_data["pending_task"] = text
        await update.message.reply_text("Set priority:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 High", callback_data="priority_high"),
             InlineKeyboardButton("🟡 Medium", callback_data="priority_medium"),
             InlineKeyboardButton("🟢 Low", callback_data="priority_low")],
        ]))

    elif waiting == WAITING_TASK_DUE:
        ctx.user_data.pop("waiting")
        task_text = ctx.user_data.pop("pending_task", "")
        priority = ctx.user_data.pop("task_priority", "medium")
        due = None
        if text.lower() != "skip":
            parsed = parse_natural_time(text)
            due = parsed.strftime("%A, %d %b %Y") if parsed else text
        tasks = load_json(TASKS_FILE)
        task = {"id": len(tasks) + 1, "text": task_text, "done": False, "priority": priority, "due": due}
        tasks.append(task)
        save_json(TASKS_FILE, tasks)
        due_text = f"\nDue: {due}" if due else ""
        await update.message.reply_text(
            f"✅ Task added: {task_text}\nPriority: {priority}{due_text}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("View Tasks", callback_data="tasks_view"),
                                               InlineKeyboardButton("Menu", callback_data="back_menu")]]))

    elif waiting == WAITING_NOTE:
        ctx.user_data.pop("waiting")
        notes = load_json(NOTES_FILE)
        note = {"id": len(notes) + 1, "text": text, "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
        notes.append(note)
        save_json(NOTES_FILE, notes)
        await update.message.reply_text("📝 Note saved!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("View Notes", callback_data="notes_view"),
                                               InlineKeyboardButton("Menu", callback_data="back_menu")]]))

    elif waiting == WAITING_NOTE_SEARCH:
        ctx.user_data.pop("waiting")
        notes = load_json(NOTES_FILE)
        results = [n for n in notes if text.lower() in n["text"].lower()]
        if not results:
            await update.message.reply_text(f"No notes found for: {text}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Notes", callback_data="menu_notes")]]))
        else:
            result_text = f"Found {len(results)} note(s):\n\n"
            for n in results:
                result_text += f"[{n['date']}] {n['text']}\n\n"
            await update.message.reply_text(result_text[:4000],
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Notes", callback_data="menu_notes")]]))

    elif waiting == WAITING_EXPENSE:
        ctx.user_data.pop("waiting")
        parts = text.split(" ", 2)
        if len(parts) < 3:
            await update.message.reply_text("Use format: 250 food Lunch")
            return
        try:
            amount = float(parts[0])
        except:
            await update.message.reply_text("First word must be the amount. Example: 250 food Lunch")
            return
        expenses = load_json(EXPENSES_FILE)
        expense = {"id": len(expenses) + 1, "amount": amount, "category": parts[1],
                   "description": parts[2], "date": datetime.now().strftime("%Y-%m-%d")}
        expenses.append(expense)
        save_json(EXPENSES_FILE, expenses)
        await update.message.reply_text(f"💸 Expense logged: {amount} on {parts[1]} — {parts[2]}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("View Expenses", callback_data="expense_view"),
                                               InlineKeyboardButton("Menu", callback_data="back_menu")]]))

    elif waiting == WAITING_HABIT:
        ctx.user_data.pop("waiting")
        habits = load_json(HABITS_FILE)
        habit = {"id": len(habits) + 1, "name": text, "streak": 0, "last_done": None}
        habits.append(habit)
        save_json(HABITS_FILE, habits)
        await update.message.reply_text(f"✅ Habit added: {text}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("View Habits", callback_data="habits_view"),
                                               InlineKeyboardButton("Menu", callback_data="back_menu")]]))

    elif waiting == WAITING_POST:
        ctx.user_data.pop("waiting")
        await ctx.bot.send_message(chat_id=CHANNEL_ID, text=text)
        await update.message.reply_text("📢 Posted to channel!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))

    elif waiting == WAITING_AUTOPOST:
        ctx.user_data.pop("waiting")
        words = text.split()
        cron = None
        message_part = ""
        for i in range(len(words), 0, -1):
            time_candidate = " ".join(words[:i])
            msg_candidate = " ".join(words[i:])
            if msg_candidate and is_recurring(time_candidate):
                cron = get_cron_from_text(time_candidate)
                if cron:
                    message_part = msg_candidate
                    break
        if not cron or not message_part:
            await update.message.reply_text("Could not parse. Try: every day 9am your message")
            return
        posts = load_json(AUTOPOSTS_FILE)
        post_id = len(posts) + 1
        posts.append({"id": post_id, "time": f"{cron['day_of_week']} {cron['hour']}:{cron['minute']:02d}",
                      "message": message_part})
        save_json(AUTOPOSTS_FILE, posts)
        scheduler.add_job(send_autopost, CronTrigger(hour=cron["hour"], minute=cron["minute"],
                          day_of_week=cron["day_of_week"]), id=f"autopost_{post_id}", args=[ctx.bot, message_part])
        await update.message.reply_text(f"✅ Auto-post scheduled:\n{text}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))

    elif waiting == WAITING_SCHEDULE:
        ctx.user_data.pop("waiting")
        words = text.split()
        title = ""
        parsed = None
        for i in range(len(words), 0, -1):
            time_candidate = " ".join(words[:i])
            title_candidate = " ".join(words[i:])
            if title_candidate:
                p = parse_natural_time(time_candidate)
                if p:
                    parsed = p
                    title = title_candidate
                    break
        if not parsed or not title:
            await update.message.reply_text("Could not parse. Try: tomorrow 2pm Team meeting")
            return
        try:
            calendar_helper.create_event(title, parsed.strftime("%Y-%m-%dT%H:%M:%S"))
            await update.message.reply_text(
                f"📅 Event created: {title}\n{parsed.strftime('%A, %d %b %Y at %H:%M')}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="back_menu")]]))
        except Exception as e:
            await update.message.reply_text(f"Calendar error: {str(e)}")

    elif waiting == "WAITING_CAL_RECURRING":
        ctx.user_data.pop("waiting")
        words = text.split()
        title = ""
        parsed = None
        for i in range(len(words), 0, -1):
            time_candidate = " ".join(words[:i])
            title_candidate = " ".join(words[i:])
            if title_candidate:
                p = parse_natural_time(time_candidate)
                if p:
                    parsed = p
                    title = title_candidate
                    break
        if not parsed or not title:
            await update.message.reply_text("Could not parse. Try: every monday 9am Team standup")
            return
        days_map = {"monday": "MO", "tuesday": "TU", "wednesday": "WE", "thursday": "TH",
                    "friday": "FR", "saturday": "SA", "sunday": "SU"}
        recurrence = "RRULE:FREQ=DAILY"
        for day, code in days_map.items():
            if day in text.lower():
                recurrence = f"RRULE:FREQ=WEEKLY;BYDAY={code}"
                break
        try:
            calendar_helper.create_event(title, parsed.strftime("%Y-%m-%dT%H:%M:%S"), recurrence=recurrence)
            await update.message.reply_text(f"📅 Recurring event created: {title}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Calendar", callback_data="menu_calendar")]]))
        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)}")

    elif waiting == "WAITING_CAL_EDIT":
        ctx.user_data.pop("waiting")
        event_id = ctx.user_data.pop("edit_event_id", None)
        if not event_id:
            await update.message.reply_text("No event selected.")
            return
        new_title = None
        new_datetime = None
        if "|" in text:
            parts = text.split("|")
            new_title = parts[0].strip()
            parsed = parse_natural_time(parts[1].strip())
            if parsed:
                new_datetime = parsed.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            parsed = parse_natural_time(text)
            if parsed:
                new_datetime = parsed.strftime("%Y-%m-%dT%H:%M:%S")
            else:
                new_title = text
        try:
            calendar_helper.update_event(event_id, new_title=new_title, new_datetime=new_datetime)
            await update.message.reply_text("✅ Event updated.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Calendar", callback_data="menu_calendar")]]))
        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)}")

    elif waiting == "WAITING_INBOX_REPLY":
        ctx.user_data.pop("waiting")
        target_user_id = ctx.user_data.pop("inbox_reply_to", None)
        msg_id         = ctx.user_data.pop("inbox_reply_id", None)
        target_name    = ctx.user_data.pop("inbox_reply_name", "them")
        if not target_user_id:
            await update.message.reply_text("Reply target lost. Try again from the inbox.")
            return
        try:
            # ── Send reply to stranger with a built-in reply keyboard ──────────
            # stranger_reply_button() gives them "💬 Reply to Michael" at the bottom
            await ctx.bot.send_message(
                chat_id=target_user_id,
                text=f"📩 Message from Michael:\n\n{text}",
                reply_markup=stranger_reply_button()
            )
            # ── Set stranger state: their next message will come back as inbox ─
            ss2 = load_json("stranger_states.json")
            if not isinstance(ss2, dict): ss2 = {}
            ss2[str(target_user_id)] = "WAITING_STRANGER_MSG"
            save_json("stranger_states.json", ss2)
            # ── Mark message as replied ────────────────────────────────────────
            inbox = load_json("inbox.json")
            for m in inbox:
                if m["id"] == msg_id:
                    m["replied"]    = True
                    m["last_reply"] = text
            save_json("inbox.json", inbox)
            await update.message.reply_text(
                f"✅ Reply sent to {target_name}!\n"
                f"They now have a Reply button on their screen to respond.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Back to Inbox", callback_data="menu_inbox"),
                    InlineKeyboardButton("Menu",          callback_data="back_menu")
                ]])
            )
        except Exception as e:
            await update.message.reply_text(f"Failed to send reply: {str(e)}")

    elif waiting == WAITING_FILE:
        ctx.user_data.pop("waiting")
        await update.message.reply_text("Please send a file or photo directly.")

    else:
        await update.message.reply_text("Use the menu:", reply_markup=main_menu_keyboard())


# ── Owner file handler ─────────────────────────────────────────────────────────

@owner_only
async def file_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    is_photo = bool(update.message.photo)
    doc = update.message.document or (update.message.photo[-1] if is_photo else None)
    if not doc:
        return
    caption = update.message.caption or ""
    default_name = (update.message.document.file_name if update.message.document
                    else f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
    filename = caption.strip() if caption.strip() else default_name
    if is_photo and not filename.endswith((".jpg", ".jpeg", ".png")):
        filename += ".jpg"
    files_db = load_json("files_db.json")
    files_db.append({"id": len(files_db) + 1, "name": filename, "is_photo": is_photo,
                     "file_id": doc.file_id, "date": datetime.now().strftime("%Y-%m-%d %H:%M")})
    save_json("files_db.json", files_db)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("View Files", callback_data="files_list"),
                                InlineKeyboardButton("Menu", callback_data="back_menu")]])
    if is_photo:
        await update.message.reply_photo(photo=doc.file_id, caption=f"Saved as: {filename}", reply_markup=kb)
    else:
        await update.message.reply_document(document=doc.file_id, caption=f"Saved as: {filename}", reply_markup=kb)


# ── Health server ──────────────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
    def log_message(self, *args): pass

def run_health_server():
    HTTPServer(("0.0.0.0", 10000), HealthHandler).serve_forever()

def restore_autoposts(app):
    for p in load_json(AUTOPOSTS_FILE):
        try:
            parts = p["time"].split()
            hour, minute = map(int, parts[-1].split(":"))
            day = parts[0] if len(parts) > 1 else "*"
            scheduler.add_job(send_autopost, CronTrigger(hour=hour, minute=minute, day_of_week=day),
                              id=f"autopost_{p['id']}", args=[app.bot, p["message"]])
        except:
            pass

async def error_handler(update, context):
    logging.error(f"Exception: {context.error}")
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=f"⚠️ Bot error:\n{str(context.error)[:500]}"
        )
    except Exception:
        pass  # avoid error loops if even this fails


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    scheduler.start()
    hour, minute = map(int, BRIEFING_TIME.split(":"))
    app = Application.builder().token(BOT_TOKEN).build()
    scheduler.add_job(send_morning_briefing, CronTrigger(hour=hour, minute=minute), args=[app.bot, OWNER_ID])
    app.add_error_handler(error_handler)

    # /start — owner vs stranger
    app.add_handler(CommandHandler("start", start,          filters=filters.User(OWNER_ID)))
    app.add_handler(CommandHandler("start", stranger_start, filters=~filters.User(OWNER_ID)))
    app.add_handler(CommandHandler("menu",  menu,           filters=filters.User(OWNER_ID)))
    app.add_handler(CommandHandler("clearmemory", clear_memory_cmd, filters=filters.User(OWNER_ID)))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd, filters=filters.User(OWNER_ID)))

    # ALL callback queries go through the router
    app.add_handler(CallbackQueryHandler(button_router))

    # Files — owner only
    app.add_handler(MessageHandler(filters.User(OWNER_ID) & (filters.Document.ALL | filters.PHOTO), file_handler))

    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(OWNER_ID),  message_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.User(OWNER_ID), stranger_handler))

    await app.initialize()
    restore_autoposts(app)
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
