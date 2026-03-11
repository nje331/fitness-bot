"""
viz_utils.py — Matplotlib visualizations.

generate_weekly_heatmap: group-level bar chart showing how many members
were active each day of the week, not a per-person grid.
"""

import io
import logging
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from bot.database import get_conn
from bot.utils.time_utils import week_start_for, challenge_dates, today_local

logger = logging.getLogger(__name__)

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def generate_weekly_heatmap(week_start: date) -> io.BytesIO:
    """
    Group-level activity chart for the given week.
    Each bar = number of members active that day.
    """
    week_end = week_start + timedelta(days=6)

    # Count how many members logged activity on each day of the week
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

    # Today's weekday index — grey out future days
    today = today_local()
    today_idx = today.weekday() if week_start_for(today) == week_start else 7

    title = (
        f"Group Activity — Week of "
        f"{week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')}"
    )

    fig, ax = plt.subplots(figsize=(9, 4))
    fig.patch.set_facecolor("#1e1e2e")
    ax.set_facecolor("#1e1e2e")

    colours = []
    for i in range(7):
        if i > today_idx:
            colours.append("#3a3a55")  # future — muted
        elif day_counts[i] == 0:
            colours.append("#44445a")  # zero — dim
        elif day_counts[i] >= total_active:
            colours.append("#57F287")  # full house — green
        else:
            colours.append("#5865F2")  # partial — blurple

    bars = ax.bar(range(7), day_counts, color=colours, width=0.6, zorder=3)

    # Label each bar with count
    for i, (bar, count) in enumerate(zip(bars, day_counts)):
        if i <= today_idx and count > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.05,
                str(count),
                ha="center", va="bottom", color="white", fontsize=11, fontweight="bold",
            )

    ax.set_xticks(range(7))
    ax.set_xticklabels(DAY_LABELS, color="white", fontsize=12)
    ax.set_ylabel("Members Active", color="white", fontsize=10)
    ax.tick_params(colors="white", length=0)
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.set_ylim(0, max(total_active + 0.5, max(day_counts) + 1))
    ax.set_title(title, color="white", fontsize=13, pad=12)
    ax.grid(axis="y", color="#333355", linewidth=0.7, zorder=0)

    for spine in ax.spines.values():
        spine.set_visible(False)

    # Goal line
    try:
        from bot.database import get_setting
        goal = float(get_setting("goal_days_per_week") or 4)
    except Exception:
        goal = 4.0

    ax.axhline(y=total_active, color="#57F287", linestyle="--", linewidth=1,
               alpha=0.5, label=f"All {total_active} members")

    # Legend patches
    legend_patches = [
        mpatches.Patch(color="#57F287", label=f"All {total_active} active"),
        mpatches.Patch(color="#5865F2", label="Partial"),
        mpatches.Patch(color="#44445a", label="Zero"),
    ]
    if today_idx < 6:
        legend_patches.append(mpatches.Patch(color="#3a3a55", label="Upcoming"))
    ax.legend(
        handles=legend_patches,
        facecolor="#2d2d44", labelcolor="white",
        edgecolor="#555", fontsize=8,
        loc="upper right",
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_group_trend_chart(since: date) -> io.BytesIO:
    """Line chart of group average days/week over all challenge weeks."""
    from bot.utils.time_utils import all_week_starts
    from bot.utils.streak_utils import compute_group_weekly_average
    from bot.database import get_setting

    week_starts = all_week_starts(since)
    labels = [ws.strftime("%b %d") for ws in week_starts]
    averages = [compute_group_weekly_average(ws)[0] for ws in week_starts]

    try:
        goal = float(get_setting("goal_days_per_week") or 4)
    except Exception:
        goal = 4.0

    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#1e1e2e")
    ax.set_facecolor("#1e1e2e")

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
    ax.set_title("Group Average — Challenge Progress", color="white", fontsize=13)
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
