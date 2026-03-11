"""
embed_utils.py — Reusable Discord embed builders.
"""

import discord
from datetime import date


COLOUR_PRIMARY   = 0x5865F2   # Blurple
COLOUR_SUCCESS   = 0x57F287   # Green
COLOUR_WARNING   = 0xFEE75C   # Yellow
COLOUR_ERROR     = 0xED4245   # Red
COLOUR_ELITE     = 0xF1C40F   # Gold


def base_embed(title: str, description: str = "", colour: int = COLOUR_PRIMARY) -> discord.Embed:
    e = discord.Embed(title=title, description=description, colour=colour)
    e.set_footer(text="💪 Fitness Challenge Bot")
    return e


def success_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(f"✅ {title}", description, COLOUR_SUCCESS)


def error_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(f"❌ {title}", description, COLOUR_ERROR)


def warning_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(f"⚠️ {title}", description, COLOUR_WARNING)


def tier_colour(tier: str) -> int:
    if tier == "Elite":
        return COLOUR_ELITE
    elif tier == "Baseline":
        return COLOUR_SUCCESS
    return COLOUR_ERROR


def build_status_embed(
    member: discord.Member,
    tier: str,
    daily_streak: int,
    best_daily: int,
    weekly_streak: int,
    best_weekly: int,
    avg: float,
    this_week_count: int,
    goal: float,
    elite_goal: float,
) -> discord.Embed:
    e = discord.Embed(
        title=f"📊 Status — {member.display_name}",
        colour=tier_colour(tier),
    )
    e.set_thumbnail(url=member.display_avatar.url)

    tier_emoji = "🥇" if tier == "Elite" else ("🎯" if tier == "Baseline" else "📈")
    e.add_field(name="Tier", value=f"{tier_emoji} **{tier}**", inline=True)
    e.add_field(name="Weekly Avg", value=f"**{avg}** days/wk", inline=True)
    e.add_field(name="This Week", value=f"**{this_week_count}** / {int(goal)} days", inline=True)
    e.add_field(
        name="🔥 Daily Streak",
        value=f"Current: **{daily_streak}** | Best: **{best_daily}**",
        inline=False,
    )
    e.add_field(
        name="📅 Weekly Streak",
        value=f"Current: **{weekly_streak}** wks | Best: **{best_weekly}** wks",
        inline=False,
    )
    needed_for_goal = max(0, goal - this_week_count)
    needed_for_elite = max(0, elite_goal - this_week_count)
    if needed_for_goal > 0:
        e.add_field(
            name="This Week",
            value=f"Need **{needed_for_goal:.0f}** more day(s) to hit goal",
            inline=False,
        )
    elif needed_for_elite > 0:
        e.add_field(
            name="This Week",
            value=f"Goal met! Need **{needed_for_elite:.1f}** more for Elite 🏆",
            inline=False,
        )
    else:
        e.add_field(name="This Week", value="🏆 Elite goal reached this week!", inline=False)
    return e
