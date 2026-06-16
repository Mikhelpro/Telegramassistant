# Mikhelassist — Personal AI Telegram Bot

A dual-purpose Telegram bot built for **Michael Wondwossen (Mikhel)**:

- **Owner side** — a full personal assistant (tasks, reminders, calendar, expenses, habits, notes, AI chat, channel posting)
- **Stranger side** — a professional AI-powered assistant that represents Mikhel to anyone who messages the bot

---

## Features

### Owner (you)
- 🤖 **AI Chat** — conversational AI with memory (Gemini 2.5 Flash)
- ✅ **Tasks** — add, view, prioritize, mark done, delete
- ⏰ **Reminders** — one-time and recurring, parsed from natural language
- 📅 **Google Calendar** — view, add, edit, delete, recurring events
- 📝 **Notes** — save, search, delete
- 💰 **Expenses** — log and summarize spending by category
- 🔁 **Habits** — track daily habits with streaks
- 📢 **Channel Posting** — post now or schedule auto-posts
- 📁 **File Manager** — save and retrieve files/photos
- 📥 **Inbox** — view messages from strangers and reply directly from the bot
- 🌅 **Morning Briefing** — daily weather + tasks + calendar summary
- ⚠️ **Error Notifications** — get DM'd automatically if the bot crashes or hits an error
- 🧹 **`/clearmemory`** — reset AI conversation memory on demand
- 📢 **`/broadcast`** — send an announcement to everyone who has ever messaged the bot

### Strangers (visitors)
- Reply keyboard at the bottom (built-in, like mic/emoji row)
- Pre-written instant answers for all buttons (no API delay)
- AI (Gemini) only used for free-text questions
- Two-way conversation thread — you reply, they get a Reply button, it comes back to your inbox
- Buttons: Services, Pricing, Website Dev, Telegram Bot, FAQ, Social Media, Channel, Message Mikhel
- 🛡️ **Rate limiting** — max 8 messages per 60 seconds per user, protects your Gemini quota from spam
- 📏 **Message length cap** — messages over 1000 characters are rejected with a friendly notice

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/Mikhelpro/telegrambot
cd telegrambot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create your `.env` file

```env
BOT_TOKEN=your_telegram_bot_token
OWNER_ID=your_telegram_user_id
CHANNEL_ID=@yourchannel
GEMINI_API_KEY=your_gemini_api_key
WEATHER_API_KEY=your_openweather_api_key
CITY=Addis Ababa
BRIEFING_TIME=06:00
```

**How to get each value:**

| Variable | Where to get it |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram — create a new bot |
| `OWNER_ID` | [@userinfobot](https://t.me/userinfobot) — send it a message, it replies with your ID |
| `CHANNEL_ID` | Your channel username e.g. `@mychannel` |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `WEATHER_API_KEY` | [OpenWeatherMap](https://openweathermap.org/api) — free tier works |
| `BRIEFING_TIME` | 24h format e.g. `06:00` |

### 4. Set up Google Calendar (optional)

If you want the Calendar feature:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → enable **Google Calendar API**
3. Create OAuth 2.0 credentials → download as `credentials.json`
4. Place `credentials.json` in the project root
5. Run the bot once locally — it will open a browser to authorize
6. A `token.json` file will be created — keep it safe

If you don't need Calendar, the bot works fine without it — calendar commands will just show an error.

### 5. Run

```bash
python bot.py
```

---

## Deploying to Render

1. Push your code to GitHub (see `.gitignore` section below first)
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `python bot.py`
6. Add all environment variables from your `.env` under **Environment**
7. Deploy

The bot includes a built-in health server on port `10000` so Render keeps it alive.

### Keeping it awake (important — free tier sleeps)

Render's free tier puts your service to sleep after 15 minutes of inactivity, which means the bot won't respond until something wakes it up. Fix this for free:

1. Go to [uptimerobot.com](https://uptimerobot.com) and create a free account
2. Add a new monitor → **HTTP(s)**
3. URL: your Render service URL (e.g. `https://your-app.onrender.com`)
4. Monitoring interval: **5 minutes**
5. Save

UptimeRobot will ping your bot every 5 minutes, keeping it always awake.

---

## Security notes

- All secrets (`BOT_TOKEN`, `OWNER_ID`, `GEMINI_API_KEY`, etc.) are read from environment variables — never hardcoded. Safe to make this repo public as long as `.env` is never committed.
- Stranger messages are **rate limited** (max 8 per 60 seconds) to prevent spam from draining your Gemini API quota.
- Stranger messages are **capped at 1000 characters** to prevent abuse via huge payloads.
- The bot **DMs you automatically** if it crashes or hits an unhandled error, via the built-in error handler.
- `inbox.json`, `tasks.json`, and other data files contain personal information — they're in `.gitignore` by default and should never be committed.

---

## .gitignore

Create a `.gitignore` file in your repo root with at least:

```
.env
credentials.json
token.json
inbox.json
tasks.json
notes.json
expenses.json
habits.json
reminders.json
autoposts.json
memory.json
files_db.json
stranger_states.json
__pycache__/
*.pyc
```

**Never commit `.env`, `credentials.json`, or `token.json` — they contain private keys.**

---

## Customizing

### Edit pre-written answers (stranger buttons)

Find `STRANGER_BUTTON_ANSWERS` in `bot.py`. Each key is the button label, each value is what gets sent to the visitor. Edit the strings directly:

```python
STRANGER_BUTTON_ANSWERS = {
    "🛠 Services": (
        "Here's what Mikhel offers:\n\n"
        "🌐 Website Development...\n\n"
        # edit this text
    ),
    ...
}
```

### Edit FAQ questions and answers

Find `FAQ_ANSWERS` in `bot.py`. To **edit** an existing answer, change the value string.

To **add a new FAQ question**:
1. Add it to `FAQ_ANSWERS`:
   ```python
   "❓ Your new question?": "Your answer here.",
   ```
2. Add the button to `stranger_faq_keyboard()`:
   ```python
   ["❓ Your new question?"],
   ```

### Train the AI for free-text questions

Find `MICHAEL_SYSTEM = """` in `bot.py`. Add any information you want the AI to know about you inside the triple quotes:

```
ABOUT MIKHEL:
- Full name: Michael Wondwossen
- Location: Addis Ababa, Ethiopia
- Add anything here: portfolio links, new services, your story, etc.
```

The AI uses this as its knowledge base when a visitor types a free-text question.

### Add buttons to the stranger menu

Find `STRANGER_MENU_BUTTONS` in `bot.py`:

```python
STRANGER_MENU_BUTTONS = [
    ["🛠 Services",    "💰 Pricing",      "❓ FAQ"],
    ["🌐 Website Dev", "🤖 Telegram Bot"],
    ["🔗 Social Media","📢 Channel"],
    ["✉️ Message Mikhel"],
]
```

Add a new row or add to an existing row. Then handle the button text in `stranger_handler`.

### Adjust rate limiting

Find these constants near the top of `stranger_handler`:

```python
RATE_LIMIT_COUNT = 8       # max messages
RATE_LIMIT_WINDOW = 60     # per this many seconds
MAX_MESSAGE_LENGTH = 1000  # characters
```

Increase or decrease these based on your traffic and Gemini quota.

### Owner commands

| Command | What it does |
|---|---|
| `/start` | Activates the bot and shows the main menu |
| `/menu` | Shows the main inline menu |
| `/clearmemory` | Wipes the AI conversation memory (use if responses feel off-context) |
| `/broadcast <message>` | Sends a message to everyone who has ever messaged the bot |

### Broadcasting to all subscribers

Every stranger who messages the bot is automatically recorded in `subscribers.json`. To announce something to everyone:

```
/broadcast New service launched! Check it out 🚀
```

The bot sends the message to each subscriber one at a time with a small delay to avoid Telegram's flood limits, then reports how many succeeded and failed.

---

## Project Structure

```
telegrambot/
├── bot.py              # Main bot — all logic lives here
├── calendar_helper.py  # Google Calendar integration
├── requirements.txt    # Python dependencies
├── README.md           # This file
└── .gitignore          # Files that should not be committed
```

**Runtime data files** (auto-created, should be in `.gitignore`):
```
inbox.json          # Messages from strangers
tasks.json          # Your tasks
notes.json          # Your notes
expenses.json       # Expense log
habits.json         # Habit tracker
reminders.json      # Saved reminders
autoposts.json      # Scheduled channel posts
memory.json         # AI conversation memory
files_db.json       # Saved file references
stranger_states.json  # Tracks stranger conversation state
subscribers.json    # List of everyone who has messaged the bot (for /broadcast)
```

---

## Requirements

```
python-telegram-bot==21.5
google-auth-oauthlib
google-api-python-client
APScheduler==3.10.4
python-dotenv
dateparser
requests
```

---

## How the stranger conversation works

```
Stranger sends /start
        ↓
Bot shows reply keyboard (built-in buttons at bottom)
        ↓
Stranger taps a button (Services / FAQ / etc.)
        ↓
    Pre-written?  ──yes──→  Instant answer shown
        │
        no
        ↓
    Free text  ──────────→  Gemini AI answers
        ↓
Stranger taps ✉️ Message Mikhel
        ↓
Bot prompts them to type
        ↓
Message saved to inbox.json
        ↓
Owner gets DM with ↩️ Reply button
        ↓
Owner types reply → sent to stranger with 💬 Reply to Mikhel button
        ↓
Stranger taps Reply → types → comes back to owner inbox
        ↓
Repeat (full two-way thread)
```

---

## License

MIT — free to use, modify, and distribute.
If you use this as a base for your own bot, a credit to [@Mikhelpro](https://github.com/Mikhelpro) is appreciated but not required.

---

Built by [Michael Wondwossen (Mikhel)](https://github.com/Mikhelpro) • Telegram: [@Almeayhu](https://t.me/Almeayhu)
