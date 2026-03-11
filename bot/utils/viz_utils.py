"""
viz_utils.py — Matplotlib/Pillow visualizations for weekly heatmaps.
"""

import io
import logging
from datetime import date, timedelta
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from bot.database import get_conn, get_active_members
from bot.utils.time_utils import week_start_for, challenge_dates, today_local

logger = logging.getLogger(__name__)

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _get_week_activity_matrix(week_start: date) -> tuple[np.ndarray, list[str]]:
    """
    Returns (matrix, member_names) where matrix[member][day] = 0 or 1.
    """
    from datetime import timedelta
    week_end = week_start + timedelta(days=6)

    with get_conn() as conn:
        members = conn.execute(
            "SELECT user_id, username FROM members WHERE is_active=1 ORDER BY username"
        ).fetchall()

        matrix = []
        names = []
        for m in members:
            row = [0] * 7
            logs = conn.execute(
                "SELECT activity_date FROM activity_logs "
                "WHERE user_id=? AND activity_date BETWEEN ? AND ? AND verified>0",
                (m["user_id"], week_start.isoformat(), week_end.isoformat()),
            ).fetchall()
            for log in logs:
                d = date.fromisoformat(log["activity_date"])
                day_idx = d.weekday()  # 0=Mon
                row[day_idx] = 1
            matrix.append(row)
            names.append(m["username"])

    return np.array(matrix, dtype=float) if matrix else np.zeros((1, 7)), names


def generate_weekly_heatmap(week_start: date) -> io.BytesIO:
    """Generate a heatmap image for the given week. Returns a BytesIO PNG buffer."""
    matrix, names = _get_week_activity_matrix(week_start)

    week_end = week_start + timedelta(days=6)
    title = f"Activity Heatmap — Week of {week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')}"

    n_members = len(names) or 1
    fig_height = max(3, n_members * 0.55 + 1.5)

    fig, ax = plt.subplots(figsize=(9, fig_height))
    fig.patch.set_facecolor("#1e1e2e")
    ax.set_facecolor("#1e1e2e")

    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "fitness", ["#2d2d44", "#5865F2"]
    )

    im = ax.imshow(matrix, aspect="auto", cmap=cmap, vmin=0, vmax=1, interpolation="nearest")

    ax.set_xticks(range(7))
    ax.set_xticklabels(DAY_LABELS, color="white", fontsize=11)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, color="white", fontsize=10)
    ax.tick_params(colors="white", length=0)

    for spine in ax.spines.values():
        spine.set_visible(False)

    # Add checkmarks on active cells
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            if matrix[i, j] == 1:
                ax.text(j, i, "✓", ha="center", va="center", color="white",
                        fontsize=13, fontweight="bold")

    ax.set_title(title, color="white", fontsize=13, pad=12)

    # Column totals
    col_totals = matrix.sum(axis=0)
    for j, total in enumerate(col_totals):
        ax.text(j, len(names) - 0.5 + 0.65, f"{int(total)}", ha="center",
                va="bottom", color="#aaaacc", fontsize=9)

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

    week_starts = all_week_starts(since)
    labels = [ws.strftime("%b %d") for ws in week_starts]
    averages = [compute_group_weekly_average(ws)[0] for ws in week_starts]

    goal = 4.0
    try:
        from bot.database import get_setting
        goal = float(get_setting("goal_days_per_week") or 4)
    except Exception:
        pass

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
    ax.yaxis.label.set_color("white")
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
