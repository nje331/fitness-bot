"""
scheduler_cog.py — Scheduled tasks using discord.ext.tasks (no APScheduler dependency).

Monday 8 AM ET — all weekly actions fire together:
  1. Heatmap + group summary embed posted to fitness channel
  2. Photo of the Week selected and announced
  3. Personal DM summaries sent to opted-in members
"""

import logging
from datetime import date, timedelta, time as dtime
from typing import Optional
import asyncio

import discord
from discord.ext import commands, tasks
import pytz

from bot.database import (
    get_setting, set_photo_of_week,
    update_group_streak, get_conn,
)
from bot.utils.time_utils import current_week_start, week_start_for, today_local
from bot.utils.streak_utils import (
    compute_group_weekly_average, compute_daily_streak,
    compute_weekly_streak, compute_weekly_average, get_user_tier,
)
from bot.utils.viz_utils import generate_weekly_heatmap
from bot.utils.embed_utils import base_embed, COLOUR_SUCCESS, COLOUR_ERROR, COLOUR_ELITE

logger = logging.getLogger(__name__)

# Monday 8 AM Eastern
_MONDAY_8AM_ET = dtime(hour=8, minute=0, tzinfo=pytz.timezone("US/Eastern"))


class SchedulerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.monday_tasks.start()
        logger.info("Scheduler started (discord.ext.tasks).")

    def cog_unload(self):
        self.monday_tasks.cancel()

    # ── Loop: fires once per day, acts only on Monday ─────────────────────────

    @tasks.loop(time=_MONDAY_8AM_ET)
    async def monday_tasks(self):
        today = today_local()
        if today.weekday() != 0:   # 0 = Monday
            return
        logger.info("Monday 8 AM task firing.")
        # Previous week = the Mon–Sun that just ended
        prev_monday = today - timedelta(days=7)
        week_start = week_start_for(prev_monday)
        await self._run_weekly(week_start)

    @monday_tasks.before_loop
    async def before_monday_tasks(self):
        await self.bot.wait_until_ready()

    # ── Orchestrator ──────────────────────────────────────────────────────────

    async def _run_weekly(self, week_start: date):
        """Run all weekly actions for the given week_start (Mon date)."""
        await self._post_heatmap_and_summary(week_start)
        await self._select_photo_of_week(week_start)
        await self._send_dm_summaries(week_start)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_fitness_channel(self) -> Optional[discord.TextChannel]:
        cid = get_setting("fitness_channel_id")
        if not cid:
            return None
        ch = self.bot.get_channel(int(cid))
        return ch if isinstance(ch, discord.TextChannel) else None

    # ── 1. Heatmap + group summary ────────────────────────────────────────────

    async def _post_heatmap_and_summary(self, week_start: date):
        fitness_ch = await self._get_fitness_channel()
        if not fitness_ch:
            logger.warning("No fitness channel set; skipping heatmap.")
            return

        week_end = week_start + timedelta(days=6)
        week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

        avg, member_count = compute_group_weekly_average(week_start)
        goal = float(get_setting("goal_days_per_week") or 4)
        success = avg >= goal
        current_streak, best_streak = update_group_streak(success, week_start)

        colour = COLOUR_SUCCESS if success else COLOUR_ERROR

        if success:
            summary_line = f"✅ Group hit the goal! Streak: **{current_streak}** week(s) 🔥"
        else:
            summary_line = f"📉 Goal not met (avg {avg} < {goal}). Streak reset — let's bounce back!"

        description = (
            f"{summary_line}\n\n"
            f"**Group avg:** {avg} days/member  •  "
            f"**Goal:** {goal} days/wk  •  "
            f"**Members:** {member_count}"
        )
        if best_streak > 1:
            description += f"\n**Best streak:** {best_streak} weeks"

        embed = discord.Embed(
            title=f"📊 Weekly Summary — {week_label}",
            description=description,
            colour=colour,
        )
        embed.set_footer(text="💪 Activity Challenge Bot")

        try:
            buf = generate_weekly_heatmap(week_start)
            file = discord.File(buf, filename="heatmap.png")
            embed.set_image(url="attachment://heatmap.png")
            await fitness_ch.send(embed=embed, file=file)
        except Exception as e:
            logger.warning("Heatmap generation failed: %s", e)
            await fitness_ch.send(embed=embed)

        logger.info("Weekly summary posted. avg=%.2f success=%s streak=%d", avg, success, current_streak)

    # ── 2. Photo of the Week ──────────────────────────────────────────────────

    async def _select_photo_of_week(self, week_start: date):
        fitness_ch = await self._get_fitness_channel()
        if not fitness_ch:
            return

        week_end = week_start + timedelta(days=6)

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM activity_logs WHERE activity_date BETWEEN ? AND ? AND verified>0",
                (week_start.isoformat(), week_end.isoformat()),
            ).fetchall()

        if not rows:
            logger.info("No verified activities this week; skipping Photo of the Week.")
            return

        best_msg_id = None
        best_user_id = None
        best_reactions = -1
        best_channel_id = None

        for row in rows:
            if row["message_id"] is None:
                continue
            try:
                ch = self.bot.get_channel(row["channel_id"])
                if ch is None:
                    continue
                msg = await ch.fetch_message(row["message_id"])
                reaction_count = sum(r.count for r in msg.reactions)
                if reaction_count > best_reactions:
                    best_reactions = reaction_count
                    best_msg_id = row["message_id"]
                    best_user_id = row["user_id"]
                    best_channel_id = row["channel_id"]
            except (discord.NotFound, discord.HTTPException) as e:
                logger.warning("Couldn't fetch message %s: %s", row["message_id"], e)

        if best_msg_id is None:
            return

        set_photo_of_week(week_start, best_user_id, best_msg_id, best_channel_id, best_reactions)

        guild = fitness_ch.guild
        winner = guild.get_member(best_user_id)
        winner_name = winner.display_name if winner else f"User {best_user_id}"

        try:
            winning_msg = await fitness_ch.fetch_message(best_msg_id)
        except discord.NotFound:
            winning_msg = None

        week_label = f"{week_start.strftime('%b %d')} – {(week_start + timedelta(days=6)).strftime('%b %d, %Y')}"
        embed = discord.Embed(
            title="📸 Photo of the Week!",
            description=(
                f"🏅 Congratulations to **{winner_name}** for this week's most-reacted photo!\n\n"
                f"**Week of:** {week_label}\n"
                f"**Reactions:** {best_reactions}"
            ),
            colour=COLOUR_ELITE,
        )
        if winning_msg:
            embed.add_field(name="Jump to post", value=winning_msg.jump_url, inline=False)
            if winning_msg.attachments:
                embed.set_image(url=winning_msg.attachments[0].url)

        await fitness_ch.send(embed=embed)
        logger.info("Photo of the Week announced: user=%s message=%s", best_user_id, best_msg_id)

    # ── 3. DM summaries ───────────────────────────────────────────────────────

    async def _send_dm_summaries(self, week_start: date):
        with get_conn() as conn:
            members = conn.execute(
                "SELECT * FROM members WHERE is_active=1 AND dm_updates=1"
            ).fetchall()

        goal = float(get_setting("goal_days_per_week") or 4)

        for m in members:
            try:
                await self._dm_user_summary(m["user_id"], week_start, goal)
            except Exception as e:
                logger.warning("Failed to DM user %s: %s", m["user_id"], e)

    async def _dm_user_summary(self, user_id: int, week_start: date, goal: float):
        from bot.database import get_activity_for_week
        user = await self.bot.fetch_user(user_id)
        if user is None:
            return

        week_days = get_activity_for_week(user_id, week_start)
        this_week_count = len(week_days)
        avg = compute_weekly_average(user_id)
        tier = get_user_tier(user_id)
        daily_streak, best_daily = compute_daily_streak(user_id)
        weekly_streak, best_weekly = compute_weekly_streak(user_id)

        hit_goal = this_week_count >= goal
        colour = COLOUR_SUCCESS if hit_goal else COLOUR_ERROR

        if this_week_count == 0:
            message = "Every step counts — next week is a fresh start. You've got this! 🚶"
        elif not hit_goal:
            message = f"You logged **{this_week_count}** day(s) this week. Keep building that momentum — consistency is everything!"
        elif tier == "Elite":
            message = f"🔥 **Elite week!** You stayed active {this_week_count} days. Incredible consistency!"
        else:
            message = f"✅ **Goal hit!** {this_week_count} active days this week — right on track. Keep it rolling!"

        embed = discord.Embed(
            title="📊 Your Weekly Summary",
            description=message,
            colour=colour,
        )
        embed.add_field(name="This Week", value=f"**{this_week_count}** active days", inline=True)
        embed.add_field(name="Weekly Avg", value=f"**{avg}** days/wk", inline=True)
        embed.add_field(name="Tier", value=f"**{tier}**", inline=True)
        embed.add_field(
            name="🔥 Daily Streak",
            value=f"Current: **{daily_streak}** | Best: **{best_daily}**",
            inline=False,
        )
        embed.add_field(
            name="📅 Weekly Streak",
            value=f"Current: **{weekly_streak}** wks | Best: **{best_weekly}** wks",
            inline=False,
        )
        embed.set_footer(text="Turn off DM updates with /updates in the server.")

        await user.send(embed=embed)
        logger.info("DM summary sent to user %s", user_id)

    # ── Public trigger methods (used by debug cog) ────────────────────────────

    async def trigger_end_week(self, use_current_week: bool = False):
        today = today_local()
        if use_current_week:
            week_start = week_start_for(today)
        else:
            week_start = week_start_for(today - timedelta(days=7))
        await self._run_weekly(week_start)

    async def trigger_sunday(self):
        """Debug alias — runs the full weekly suite for current week."""
        week_start = week_start_for(today_local())
        await self._run_weekly(week_start)

    async def trigger_show_summary(self, channel: discord.TextChannel):
        """Post current week's heatmap + summary to a given channel."""
        week_start = current_week_start()
        week_end = week_start + timedelta(days=6)
        week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

        avg, member_count = compute_group_weekly_average(week_start)
        goal = float(get_setting("goal_days_per_week") or 4)
        success = avg >= goal

        description = (
            f"**Group avg:** {avg} days/member  •  "
            f"**Goal:** {goal} days/wk  •  "
            f"**Members:** {member_count}"
        )

        embed = discord.Embed(
            title=f"📊 Current Week — {week_label}",
            description=description,
            colour=COLOUR_SUCCESS if success else COLOUR_ERROR,
        )
        embed.set_footer(text="💪 Activity Challenge Bot")

        try:
            buf = generate_weekly_heatmap(week_start)
            file = discord.File(buf, filename="heatmap.png")
            embed.set_image(url="attachment://heatmap.png")
            await channel.send(embed=embed, file=file)
        except Exception as e:
            await channel.send(f"❌ Error generating heatmap: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(SchedulerCog(bot))