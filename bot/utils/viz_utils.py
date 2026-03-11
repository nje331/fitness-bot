"""
viz_utils.py — Matplotlib visualizations.

generate_weekly_heatmap: 7×1 colored grid showing how many members
were active each day of the week. Color scale:
  - No members active → dim grey (future or zero)
  - Low activity      → green
  - Medium activity   → yellow
  - High activity     → orange
  - All members active → red

The thresholds scale with total active member count.
"""

import io
import logging
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

from bot.database import get_conn
from bot.utils.time_utils import week_start_for, today_local

logger = logging.getLogger(__name__)

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Colors for the grid cells
COLOUR_FUTURE  = "#2a2a3e"   # muted — not yet happened
COLOUR_ZERO    = "#3a3a4e"   # dark grey — nobody active
COLOUR_LOW     = "#57F287"   # green
COLOUR_MEDIUM  = "#FEE75C"   # yellow
COLOUR_HIGH    = "#F0A030"   # orange
COLOUR_MAX     = "#ED4245"   # red — 100% of members active
COLOUR_BG      = "#1e1e2e"


def _day_colour(count: int, total: int) -> str:
    """
    Map (count, total) to a grid cell color.
    Scale: 0=grey, 0<x<33%=green, 33-66%=yellow, 66-99%=orange, 100%=red.
    """
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
    """
    7×1 grid heatmap for the given week.
    Each cell = one day, colored by proportion of active members.
    No legend — color meaning is intuitive (green=low, yellow=med, orange=high, red=all).
    """
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

    # Compact figure — just the grid, no extra margins
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

        # Day label
        ax.text(
            x + cell_w / 2, y + cell_h * 0.68,
            DAY_LABELS[i],
            ha="center", va="center",
            color=text_colour,
            fontsize=10.5, fontweight="bold", zorder=3,
        )

        # Count (or dash for zero/future)
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

    # Title above cells
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