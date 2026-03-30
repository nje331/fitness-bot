"""
viz_utils.py — Matplotlib visualizations.

generate_weekly_heatmap:      7×1 colored grid for the group fitness channel post.
generate_user_activity_chart: Bar chart of a user's daily count per week (for DMs).
                               Pass exclude_current_week=True to omit the in-progress week.
generate_group_trend_chart:   Line chart of group avg days/week over the challenge.
                               Pass until=<date> to exclude weeks at or after that date.
"""

import io
import logging
from datetime import date, timedelta
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

from bot.database import get_conn
from bot.utils.time_utils import week_start_for, today_local

logger = logging.getLogger(__name__)

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

COLOUR_FUTURE  = "#2a2a3e"
COLOUR_ZERO    = "#3a3a4e"
COLOUR_LOW     = "#57F287"
COLOUR_MEDIUM  = "#FEE75C"
COLOUR_HIGH    = "#F0A030"
COLOUR_MAX     = "#ED4245"
COLOUR_BG      = "#1e1e2e"


def _day_colour(count: int, total: int) -> str:
    if total == 0 or count == 0:
        return COLOUR_ZERO
    ratio = count / total
    if ratio >= 1.0:
        return COLOUR_MAX
    elif ratio >= 0.66:
        return COLOUR_HIGH
    elif ratio >= 0.33:
        return COLOUR_MEDIUM
    else:
        return COLOUR_LOW


def generate_weekly_heatmap(week_start: date) -> io.BytesIO:
    """7×1 grid heatmap for the given week (group fitness channel post)."""
    week_end = week_start + timedelta(days=6)

    with get_conn() as conn:
        total_active = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM members WHERE is_active=1"
        ).fetchone()[0] or 1

        day_counts = [0] * 7
        rows = conn.execute(
            "SELECT activity_date FROM activity_logs "
            "WHERE activity_date BETWEEN ? AND ? AND verified>0",
            (week_start.isoformat(), week_end.isoformat()),
        ).fetchall()

        for r in rows:
            d = date.fromisoformat(r["activity_date"])
            day_counts[d.weekday()] += 1

    today = today_local()
    today_idx = today.weekday() if week_start_for(today) == week_start else 7

    title = f"Week of {week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

    fig, ax = plt.subplots(figsize=(7.5, 1.6))
    fig.patch.set_facecolor(COLOUR_BG)
    ax.set_facecolor(COLOUR_BG)
    ax.set_aspect("equal")
    ax.axis("off")

    cell_w = 1.0
    cell_h = 1.0
    gap = 0.08
    total_width = 7 * cell_w + 6 * gap

    for i in range(7):
        x = i * (cell_w + gap)
        y = 0

        if i > today_idx:
            face_colour = COLOUR_FUTURE
            text_colour = "#555577"
        else:
            face_colour = _day_colour(day_counts[i], total_active)
            text_colour = "#1e1e2e" if face_colour not in (COLOUR_ZERO, COLOUR_FUTURE) else "#888899"

        rect = plt.Rectangle(
            (x, y), cell_w, cell_h,
            facecolor=face_colour,
            edgecolor="#11111e",
            linewidth=1.5,
            zorder=2,
        )
        ax.add_patch(rect)

        ax.text(
            x + cell_w / 2, y + cell_h * 0.68,
            DAY_LABELS[i],
            ha="center", va="center",
            color=text_colour,
            fontsize=10.5, fontweight="bold", zorder=3,
        )

        if i <= today_idx:
            count_str = str(day_counts[i]) if day_counts[i] > 0 else "–"
            ax.text(
                x + cell_w / 2, y + cell_h * 0.30,
                count_str,
                ha="center", va="center",
                color=text_colour,
                fontsize=12, fontweight="bold", zorder=3,
            )

    ax.set_xlim(-0.05, total_width + 0.05)
    ax.set_ylim(-0.15, cell_h + 0.35)

    ax.text(
        total_width / 2, cell_h + 0.22,
        title,
        ha="center", va="center",
        color="white", fontsize=10, fontweight="bold",
    )

    plt.tight_layout(pad=0.1)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_user_activity_chart(
    user_id: int,
    exclude_current_week: bool = False,
) -> Optional[io.BytesIO]:
    """
    Bar chart of the user's active days per week across the entire challenge.

    Args:
        exclude_current_week: If True, the in-progress (current) week is omitted
            so the chart only reflects completed weeks.

    Returns None if there is no challenge start date or fewer than 2 weeks of data.
    """
    from bot.utils.time_utils import all_week_starts, challenge_dates
    from bot.database import get_setting, get_weekly_counts_since

    start, _ = challenge_dates()
    if not start:
        return None

    week_starts = all_week_starts(start)

    if exclude_current_week:
        current_week = week_start_for(today_local())
        week_starts = [ws for ws in week_starts if ws < current_week]

    if len(week_starts) < 2:
        return None

    counts_map = get_weekly_counts_since(user_id, start)
    counts = [counts_map.get(ws.isoformat(), 0) for ws in week_starts]
    labels = [ws.strftime("%-m/%-d") for ws in week_starts]

    try:
        goal = float(get_setting("goal_days_per_week") or 4)
        elite = float(get_setting("elite_days_per_week") or 5.5)
    except Exception:
        goal = 4.0
        elite = 5.5

    bar_colours = []
    for c in counts:
        if c >= elite:
            bar_colours.append("#F1C40F")   # gold
        elif c >= goal:
            bar_colours.append("#57F287")   # green
        else:
            bar_colours.append("#ED4245")   # red

    fig, ax = plt.subplots(figsize=(max(6, len(week_starts) * 0.7), 3.5))
    fig.patch.set_facecolor(COLOUR_BG)
    ax.set_facecolor(COLOUR_BG)

    x = np.arange(len(week_starts))
    bars = ax.bar(x, counts, color=bar_colours, edgecolor="#11111e", linewidth=0.8, zorder=3)

    ax.axhline(y=goal, color="#57F287", linestyle="--", linewidth=1.4,
               label=f"Goal ({int(goal)}/wk)", zorder=4)
    ax.axhline(y=elite, color="#F1C40F", linestyle=":", linewidth=1.4,
               label=f"Elite ({elite}/wk)", zorder=4)

    for bar, val in zip(bars, counts):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.1,
                str(val),
                ha="center", va="bottom",
                color="white", fontsize=8, fontweight="bold",
            )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, color="#aaaacc", fontsize=8,
                       rotation=45 if len(week_starts) > 8 else 0, ha="right")
    ax.set_yticks(range(0, 8))
    ax.tick_params(colors="#aaaacc")
    ax.set_ylabel("Active Days", color="#aaaacc", fontsize=9)
    ax.set_ylim(0, 8)
    ax.set_title("Your Activity — Days per Week (Completed Weeks)", color="white", fontsize=11, fontweight="bold", pad=8)
    ax.yaxis.grid(True, color="#333355", linestyle="-", linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)

    ax.legend(
        facecolor="#2d2d44", labelcolor="white",
        edgecolor="#555", fontsize=8, loc="upper left",
    )

    for spine in ax.spines.values():
        spine.set_edgecolor("#333355")

    plt.tight_layout(pad=0.6)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_group_trend_chart(
    since: date,
    until: Optional[date] = None,
) -> io.BytesIO:
    """
    Line chart of group average days/week over all challenge weeks.

    Args:
        since: Challenge start date.
        until: Exclusive upper bound. Weeks at or after this date are omitted.
               Defaults to the current week start so in-progress data is excluded.
    """
    from bot.utils.time_utils import all_week_starts
    from bot.utils.streak_utils import compute_group_weekly_average
    from bot.database import get_setting

    # Default: exclude the current in-progress week
    if until is None:
        until = week_start_for(today_local())

    week_starts = [ws for ws in all_week_starts(since) if ws < until]

    if not week_starts:
        # Fallback: render an empty chart rather than crashing
        week_starts = all_week_starts(since)

    labels = [ws.strftime("%b %d") for ws in week_starts]
    averages = [compute_group_weekly_average(ws)[0] for ws in week_starts]

    try:
        goal = float(get_setting("goal_days_per_week") or 4)
    except Exception:
        goal = 4.0

    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor(COLOUR_BG)
    ax.set_facecolor(COLOUR_BG)

    ax.plot(labels, averages, color="#5865F2", linewidth=2.5, marker="o",
            markersize=6, label="Group Avg")
    ax.axhline(y=goal, color="#57F287", linestyle="--", linewidth=1.5,
               label=f"Goal ({goal} days/wk)")

    ax.fill_between(range(len(labels)), averages, goal,
                    where=[a >= goal for a in averages],
                    alpha=0.15, color="#57F287")

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, color="white", fontsize=9, rotation=30, ha="right")
    ax.tick_params(colors="white")
    ax.set_ylabel("Avg Days/Week", color="white")
    ax.set_title("Group Average — Challenge Progress (Completed Weeks)", color="white", fontsize=13)
    ax.legend(facecolor="#2d2d44", labelcolor="white", edgecolor="#555")

    for spine in ax.spines.values():
        spine.set_edgecolor("#444")

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf