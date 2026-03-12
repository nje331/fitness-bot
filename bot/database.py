"""
database.py — SQLite database layer for the Fitness Challenge Bot.
All schema creation, migrations, and low-level queries live here.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("/data/fitness.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create all tables if they don't exist and run any pending migrations."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS members (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT    NOT NULL,
            joined_at   TEXT    NOT NULL DEFAULT (date('now')),
            is_active   INTEGER NOT NULL DEFAULT 1,
            dm_updates  INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS admins (
            user_id  INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS activity_logs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            activity_date  TEXT    NOT NULL,   -- YYYY-MM-DD
            message_id     INTEGER,
            channel_id     INTEGER,
            verified       INTEGER NOT NULL DEFAULT 0,  -- 0=pending,1=verified,2=manual
            added_by       INTEGER,                     -- NULL = self-posted
            created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES members(user_id),
            UNIQUE(user_id, activity_date)
        );

        CREATE TABLE IF NOT EXISTS photo_of_week (
            week_start   TEXT    PRIMARY KEY,  -- Monday YYYY-MM-DD
            user_id      INTEGER NOT NULL,
            message_id   INTEGER,
            channel_id   INTEGER,
            reaction_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS group_streak (
            id            INTEGER PRIMARY KEY CHECK (id = 1),
            current       INTEGER NOT NULL DEFAULT 0,
            best          INTEGER NOT NULL DEFAULT 0,
            last_success  TEXT
        );

        INSERT OR IGNORE INTO group_streak (id, current, best) VALUES (1, 0, 0);

        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        );

        INSERT OR IGNORE INTO schema_version (version) VALUES (1);
        """)

    # _run_migrations()
    logger.info("Database initialized at %s", DB_PATH)


def _run_migrations() -> None:
    """Run incremental schema migrations based on schema_version."""
    with get_conn() as conn:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        version = row["version"] if row else 1

        if version < 2:
            # Migration 2: flip DMs to opt-out — existing members who never actively
            # opted out (old default was 0 = off) get switched to 1 = on.
            conn.execute("UPDATE members SET dm_updates=1 WHERE dm_updates=0")
            conn.execute("UPDATE schema_version SET version=2")
            logger.info("Migration 2 applied: dm_updates set to 1 for all existing members.")


# ── Settings helpers ──────────────────────────────────────────────────────────

DEFAULTS: dict[str, str] = {
    "fitness_channel_id":   "",
    "admin_channel_id":     "",
    "goal_days_per_week":   "4",
    "elite_days_per_week":  "5.5",
    "elite_reward_text":    "TBD by admins",
    "grace_days":           "0",           # 0 = off, 1 = 1 grace day
    "manual_verification":  "0",           # 0 = auto, 1 = requires ✅
    "timezone":             "US/Eastern",
    "challenge_start":      "",
    "challenge_end":        "",
    "pog_emoji":            "<:PogU:1481438595133866175>",
    "debug":                "0",
    "last_weekly_sent":     "",            # ISO date (Monday) of last completed weekly send
}


def get_setting(key: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            return row["value"]
        return DEFAULTS.get(key, "")


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_all_settings() -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result = dict(DEFAULTS)
        result.update({r["key"]: r["value"] for r in rows})
        return result


# ── Member helpers ────────────────────────────────────────────────────────────

def upsert_member(user_id: int, username: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO members (user_id, username, dm_updates) VALUES (?,?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
            (user_id, username),
        )


def set_member_active(user_id: int, active: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE members SET is_active=? WHERE user_id=?", (int(active), user_id))


def get_active_members(conn: Optional[sqlite3.Connection] = None) -> list[sqlite3.Row]:
    c = conn or get_conn()
    rows = c.execute("SELECT * FROM members WHERE is_active=1").fetchall()
    if conn is None:
        c.close()
    return rows


def get_member(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM members WHERE user_id=?", (user_id,)).fetchone()


def set_dm_updates(user_id: int, enabled: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE members SET dm_updates=? WHERE user_id=?", (int(enabled), user_id))


# ── Admin helpers ─────────────────────────────────────────────────────────────

def add_admin(user_id: int, added_by: Optional[int] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?,?)",
            (user_id, added_by),
        )


def remove_admin(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))


def get_admins() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admins").fetchall()


def is_admin(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
        return row is not None


# ── Activity helpers ──────────────────────────────────────────────────────────

def log_activity(
    user_id: int,
    activity_date: date,
    message_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    verified: int = 1,
    added_by: Optional[int] = None,
) -> bool:
    """Returns True if inserted, False if already existed for that day."""
    date_str = activity_date.isoformat()
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO activity_logs "
                "(user_id, activity_date, message_id, channel_id, verified, added_by) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, date_str, message_id, channel_id, verified, added_by),
            )
        return True
    except sqlite3.IntegrityError:
        return False  # duplicate


def remove_activity(user_id: int, activity_date: date) -> bool:
    date_str = activity_date.isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM activity_logs WHERE user_id=? AND activity_date=?",
            (user_id, date_str),
        )
        return cur.rowcount > 0


def get_activity_for_week(user_id: int, week_start: date) -> list[sqlite3.Row]:
    """Returns activity rows for a Mon–Sun week."""
    from datetime import timedelta
    week_end = week_start + timedelta(days=6)
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM activity_logs WHERE user_id=? AND activity_date BETWEEN ? AND ? AND verified>0",
            (user_id, week_start.isoformat(), week_end.isoformat()),
        ).fetchall()


def get_all_activity_dates(user_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT activity_date FROM activity_logs WHERE user_id=? AND verified>0 ORDER BY activity_date",
            (user_id,),
        ).fetchall()
        return [r["activity_date"] for r in rows]


def get_weekly_counts_since(user_id: int, since: date) -> dict[str, int]:
    """Returns {week_start_str: count} for all weeks since challenge start."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT activity_date FROM activity_logs WHERE user_id=? AND activity_date>=? AND verified>0",
            (user_id, since.isoformat()),
        ).fetchall()
    from bot.utils.time_utils import week_start_for
    counts: dict[str, int] = {}
    for r in rows:
        d = date.fromisoformat(r["activity_date"])
        ws = week_start_for(d).isoformat()
        counts[ws] = counts.get(ws, 0) + 1
    return counts


def get_total_activity_count(user_id: int) -> int:
    """Total verified activity days logged across the entire challenge."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM activity_logs WHERE user_id=? AND verified>0",
            (user_id,),
        ).fetchone()
        return row["c"] if row else 0


def get_most_active_day_of_week(user_id: int) -> Optional[str]:
    """Returns the day name the user has logged the most activity on, or None."""
    DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT activity_date FROM activity_logs WHERE user_id=? AND verified>0",
            (user_id,),
        ).fetchall()
    if not rows:
        return None
    counts = [0] * 7
    for r in rows:
        d = date.fromisoformat(r["activity_date"])
        counts[d.weekday()] += 1
    best_idx = counts.index(max(counts))
    return DAY_NAMES[best_idx] if counts[best_idx] > 0 else None


def get_pending_verifications(channel_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM activity_logs WHERE channel_id=? AND verified=0",
            (channel_id,),
        ).fetchall()


def verify_activity(message_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE activity_logs SET verified=1 WHERE message_id=? AND verified=0",
            (message_id,),
        )
        return cur.rowcount > 0


# ── Photo of week helpers ─────────────────────────────────────────────────────

def set_photo_of_week(
    week_start: date,
    user_id: int,
    message_id: Optional[int],
    channel_id: Optional[int],
    reaction_count: int,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO photo_of_week (week_start, user_id, message_id, channel_id, reaction_count) "
            "VALUES (?,?,?,?,?) ON CONFLICT(week_start) DO UPDATE SET "
            "user_id=excluded.user_id, message_id=excluded.message_id, "
            "channel_id=excluded.channel_id, reaction_count=excluded.reaction_count",
            (week_start.isoformat(), user_id, message_id, channel_id, reaction_count),
        )


def get_photo_of_week(week_start: date) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM photo_of_week WHERE week_start=?", (week_start.isoformat(),)
        ).fetchone()


def get_all_photos_of_week() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM photo_of_week ORDER BY week_start DESC"
        ).fetchall()


# ── Group streak helpers ──────────────────────────────────────────────────────

def get_group_streak() -> sqlite3.Row:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM group_streak WHERE id=1").fetchone()


def update_group_streak(success: bool, week_start: date) -> tuple[int, int, bool]:
    """
    Increment or reset streak. Returns (current, best, new_record).
    new_record is True only when the streak just broke and the ended run exceeded
    the previous stored best (i.e. a true new all-time record was just set and lost).
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM group_streak WHERE id=1").fetchone()
        current = row["current"]
        best = row["best"]
        new_record = False

        if success:
            current += 1
            if current > best:
                best = current
            conn.execute(
                "UPDATE group_streak SET current=?, best=?, last_success=? WHERE id=1",
                (current, best, week_start.isoformat()),
            )
        else:
            # The streak is breaking — was it a record?
            if current > best:
                new_record = True
                best = current
                conn.execute(
                    "UPDATE group_streak SET current=0, best=? WHERE id=1", (best,)
                )
            else:
                conn.execute("UPDATE group_streak SET current=0 WHERE id=1")
            current = 0

        return current, best, new_record