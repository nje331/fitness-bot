"""
streak_utils.py — Streak calculations derived from the database.
Always recalculates from stored dates so manual additions/deletions are reflected correctly.
"""

from datetime import date, timedelta
from bot.database import get_all_activity_dates, get_weekly_counts_since, get_setting
from bot.utils.time_utils import challenge_dates, all_week_starts, today_local, week_start_for


def compute_daily_streak(user_id: int) -> tuple[int, int]:
    """
    Returns (current_streak, best_streak) counting consecutive active days.
    Grace days: if setting is 1, a single-day gap does not break the streak.
    """
    grace = int(get_setting("grace_days") or 0)
    dates = [date.fromisoformat(d) for d in get_all_activity_dates(user_id)]
    if not dates:
        return 0, 0

    dates = sorted(set(dates))
    best = 1
    current = 1
    i = 1
    while i < len(dates):
        gap = (dates[i] - dates[i - 1]).days
        if gap == 1 or (grace >= 1 and gap <= 2):
            current += 1
        else:
            best = max(best, current)
            current = 1
        i += 1

    best = max(best, current)

    # Is the streak still alive? (gap from last date to today)
    last = dates[-1]
    today = today_local()
    gap_to_today = (today - last).days
    alive = gap_to_today == 0 or (grace >= 1 and gap_to_today <= 2)
    if not alive:
        current = 0

    return current, best


def compute_weekly_streak(user_id: int) -> tuple[int, int]:
    """
    Returns (current_weekly_streak, best_weekly_streak).
    A week counts if the user hit the goal days/week threshold.
    """
    start, _ = challenge_dates()
    if not start:
        return 0, 0

    goal = float(get_setting("goal_days_per_week") or 4)
    weekly_counts = get_weekly_counts_since(user_id, start)
    week_starts = all_week_starts(start)

    best = 0
    current = 0
    for ws in week_starts:
        count = weekly_counts.get(ws.isoformat(), 0)
        if count >= goal:
            current += 1
            best = max(best, current)
        else:
            current = 0

    # Check if current week should still be considered "in progress"
    this_week = week_start_for(today_local())
    if week_starts and week_starts[-1] == this_week:
        # Don't penalize the in-progress week
        pass

    return current, best


def compute_weekly_average(user_id: int) -> float:
    """Average days/week since challenge start."""
    start, _ = challenge_dates()
    if not start:
        return 0.0

    weekly_counts = get_weekly_counts_since(user_id, start)
    weeks = all_week_starts(start)
    if not weeks:
        return 0.0
    total = sum(weekly_counts.get(ws.isoformat(), 0) for ws in weeks)
    return round(total / len(weeks), 2)


def get_user_tier(user_id: int) -> str:
    """Returns 'Elite', 'Baseline', or 'Keep Pushing'."""
    avg = compute_weekly_average(user_id)
    elite = float(get_setting("elite_days_per_week") or 5.5)
    goal = float(get_setting("goal_days_per_week") or 4)
    if avg >= elite:
        return "Elite"
    elif avg >= goal:
        return "Baseline"
    return "Keep Pushing"


def compute_group_weekly_average(week_start: date) -> tuple[float, int]:
    """
    Returns (average_days, active_member_count) for all active members that week.
    """
    from datetime import timedelta
    from bot.database import get_conn, get_active_members

    week_end = week_start + timedelta(days=6)
    with get_conn() as conn:
        members = conn.execute("SELECT user_id FROM members WHERE is_active=1").fetchall()
        total = 0
        count = len(members)
        for m in members:
            days = conn.execute(
                "SELECT COUNT(*) as c FROM activity_logs WHERE user_id=? "
                "AND activity_date BETWEEN ? AND ? AND verified>0",
                (m["user_id"], week_start.isoformat(), week_end.isoformat()),
            ).fetchone()["c"]
            total += days

    if count == 0:
        return 0.0, 0
    return round(total / count, 2), count