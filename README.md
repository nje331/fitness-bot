# 💪 Fitness Challenge Discord Bot

A Discord bot for running a group fitness challenge where members log workouts by posting photos. Tracks streaks, weekly averages, tiers, group progress, and selects a Photo of the Week.

---

## Features

| Feature | Details |
|---|---|
| 📸 Photo logging | Post a workout image in the fitness channel → auto-credited |
| ✅ Verification | Optional admin ✅ reaction required before credit is granted |
| 🏆 Tiers | Baseline (default 4 days/wk) and Elite (default 5.5 days/wk) |
| 🔥 Streaks | Daily & weekly streaks with grace day forgiveness |
| 📅 Group streak | Tracks consecutive weeks the group hits the goal |
| 📊 Heatmap | Auto-posted every Monday showing weekly group activity |
| 📸 Photo of the Week | Most-reacted photo crowned every Sunday night |
| 📬 DM summaries | Opt-in personal weekly DMs every Sunday |
| 🧪 Debug mode | Extra commands for testing without affecting production |

---

## Project Structure

```
fitness-bot/
├── bot.py                   # Entry point
├── bot/
│   ├── database.py          # All DB access (SQLite)
│   ├── cogs/
│   │   ├── admin_cog.py     # /admins, /settings, /members
│   │   ├── activity_cog.py  # Photo listener, /addactivity, /removeactivity
│   │   ├── scheduler_cog.py # Monday/Sunday scheduled tasks
│   │   ├── user_cog.py      # /help, /status, /updates, /photos
│   │   └── debug_cog.py     # /nextday, /endweek, /showsummary (debug only)
│   └── utils/
│       ├── checks.py        # Admin permission check decorator
│       ├── embed_utils.py   # Reusable embed builders
│       ├── streak_utils.py  # Streak/tier calculations (DB-derived)
│       ├── time_utils.py    # Timezone-aware date helpers
│       └── viz_utils.py     # Heatmap + trend chart generation
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Quick Start

### 1. Create a Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application → Bot section → copy the token
3. Enable **Message Content Intent**, **Server Members Intent**, **Presence Intent**
4. Under OAuth2 → URL Generator, select `bot` + `applications.commands`
5. Bot permissions needed: `Send Messages`, `Read Message History`, `Add Reactions`, `Embed Links`, `Attach Files`, `Use External Emojis`
6. Invite the bot to your server using the generated URL

For the debug bot, repeat with a second application.

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and fill in your tokens
```

### 3. Run with Docker (recommended)

```bash
# Production
docker compose up -d fitness-bot

# Debug mode
docker compose --profile debug up fitness-bot-debug
```

### 4. Run locally (for development)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Create data/log directories
mkdir -p data logs

# Production
python bot.py

# Debug mode
python bot.py --debug
```

---

## Initial Setup (after bot is running)

1. Use `/settings` to configure:
   - **Fitness channel** — where members post workout photos
   - **Admin channel** — where admin alerts go
   - **Challenge start/end dates**
   - **Goal** (default: 4 days/week)
   - **Elite goal** (default: 5.5 days/week)
   - **Grace days** (0 = off, 1 = streak forgiveness for 1-day gaps)
   - **Manual verification** (toggle on/off)
   - **Elite reward description**

2. Use `/admins` to add bot admins

3. Use `/members` to add participants

---

## Commands Reference

### Admin Only

| Command | Description |
|---|---|
| `/admins` | View, add, and remove bot admins |
| `/settings` | Configure all bot settings via buttons |
| `/members` | Add/deactivate challenge members |
| `/addactivity @user [date]` | Manually grant activity credit |
| `/removeactivity @user date` | Remove a credit (YYYY-MM-DD) |

### All Users

| Command | Description |
|---|---|
| `/help` | Challenge overview + commands (admin commands shown to admins) |
| `/status` | Your tier, streak, weekly average, this-week count |
| `/updates` | Toggle opt-in DM weekly summaries |
| `/photos` | Paginated gallery of Photo of the Week winners |

### Debug Only (loaded when `--debug` or `DEBUG=true`)

| Command | Description |
|---|---|
| `/nextday` | Info about simulating a day advance |
| `/endweek` | Trigger Monday weekly announcement now |
| `/showsummary` | Post current week's heatmap to this channel |
| `/triggersunday` | Trigger Sunday wrap-up (photo + DMs) now |

---

## How Activity Tracking Works

1. Member posts a photo with an image attachment in the fitness channel
2. Bot checks it's an image, the user is an active member, and they haven't already been credited today
3. If **manual verification is OFF**: credit is granted immediately, bot reacts with PogU
4. If **manual verification is ON**: credit is pending until an admin reacts with ✅
   - Verification uses the **original post date**, not the date the admin reacted
5. One credit per person per calendar day (US Eastern time, configurable)

---

## Streak Mechanics

- **Daily streak**: Consecutive days with activity. With grace days enabled (setting = 1), a single-day gap doesn't break the streak.
- **Weekly streak**: Consecutive weeks where the member hit the goal.
- Streaks are always recalculated from the database — manual additions and deletions are automatically reflected.

---

## Scheduled Tasks

| Time | Task |
|---|---|
| Sunday 9 PM ET | Select Photo of the Week, send DM summaries to opted-in members |
| Monday 9 AM ET | Post weekly group announcement with heatmap, update group streak |

Timezone is configurable in `/settings`.

---

## Database

SQLite at `/data/fitness.db` (inside the container). The Docker volume `fitness-data` persists across container restarts and image rebuilds.

**Tables:** `settings`, `members`, `admins`, `activity_logs`, `photo_of_week`, `group_streak`, `schema_version`

---

## Persistent Data

| Volume | Path | Contents |
|---|---|---|
| `fitness-data` | `/data/` | `fitness.db` SQLite database |
| `fitness-logs` | `/logs/` | `bot.log` rotating log file |

---

## The PogU Emoji

The bot reacts to workout photos with a PogU emoji. Default setting: `<:PogU:958805527717097473>`. If the bot can't find this emoji in the server, it falls back to 💪. Update in `/settings → pog_emoji`.

---

## Updating the Bot

```bash
# Pull latest code
git pull

# Rebuild and restart (data volume is preserved)
docker compose up -d --build fitness-bot
```

---

## Grace Days

When `grace_days = 1` in settings:
- A 1-day gap in your activity doesn't break your daily streak
- Example: active Mon, Wed → streak = 2 (Tuesday is forgiven)
- This does **not** allow retroactive posting for missed days — it's streak forgiveness only

---

## Members & Group Average

- Only **active** members count toward the group average
- When a member is made inactive, historical weeks are unaffected — the change applies to future group averages only
- Inactive members' photo posts are ignored (no credit)
- Members can be reactivated at any time via `/members`
