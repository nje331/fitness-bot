"""
scheduler_cog.py — APScheduler-based scheduled tasks.

Tasks:
- Monday 9 AM ET: Group weekly announcement + streak update
- Sunday 9 PM ET: Photo of the Week selection + DM summaries
- Weekly heatmap post
"""

import logging
from datetime import date, timedelta
from typing import Optional

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from bot.database import (
    get_setting, get_all_photos_of_week, set_photo_of_week,
    update_group_streak, get_conn, get_active_members,
)
from bot.utils.time_utils import current_week_start, week_start_for, today_local, get_tz
from bot.utils.streak_utils import (
    compute_group_weekly_average, compute_daily_streak,
    compute_weekly_streak, compute_weekly_average, get_user_tier,
)
from bot.utils.viz_utils import generate_weekly_heatmap
from bot.utils.embed_utils import base_embed, COLOUR_SUCCESS, COLOUR_ERROR, COLOUR_ELITE

logger = logging.getLogger(__name__)


class SchedulerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="US/Eastern")
        self._register_jobs()
        self.scheduler.start()
        logger.info("Scheduler started.")

    def _register_jobs(self):
        # Monday 9 AM ET — group weekly summary
        self.scheduler.add_job(
            self.weekly_group_announcement,
            CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="US/Eastern"),
            id="weekly_group",
            replace_existing=True,
        )
        # Sunday 9 PM ET — photo of the week + DM summaries
        self.scheduler.add_job(
            self.sunday_wrap_up,
            CronTrigger(day_of_week="sun", hour=21, minute=0, timezone="US/Eastern"),
            id="sunday_wrap",
            replace_existing=True,
        )

    def cog_unload(self):
        self.scheduler.shutdown(wait=False)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_admin_channel(self) -> Optional[discord.TextChannel]:
        cid = get_setting("admin_channel_id")
        if not cid:
            return None
        ch = self.bot.get_channel(int(cid))
        return ch if isinstance(ch, discord.TextChannel) else None

    async def _get_fitness_channel(self) -> Optional[discord.TextChannel]:
        cid = get_setting("fitness_channel_id")
        if not cid:
            return None
        ch = self.bot.get_channel(int(cid))
        return ch if isinstance(ch, discord.TextChannel) else None

    # ── Sunday 9 PM — Heatmap + Photo of the Week + DM summaries ────────────

    async def sunday_wrap_up(self):
        logger.info("Running sunday_wrap_up task")
        today = today_local()
        week_start = week_start_for(today)

        # Post heatmap first, then photo, then DMs
        await self._post_heatmap(week_start)
        await self._select_photo_of_week(week_start)
        await self._send_dm_summaries(week_start)

    async def _post_heatmap(self, week_start: date):
        """Post the weekly heatmap to the fitness channel."""
        fitness_ch = await self._get_fitness_channel()
        if not fitness_ch:
            logger.warning("No fitness channel set; skipping heatmap.")
            return
        try:
            buf = generate_weekly_heatmap(week_start)
            file = discord.File(buf, filename="heatmap.png")
            embed = base_embed(
                f"📊 Week of {week_start.strftime('%b %d, %Y')} — Activity Heatmap",
                f"{week_start.strftime('%b %d')} – {(week_start + timedelta(days=6)).strftime('%b %d, %Y')}",
            )
            embed.set_image(url="attachment://heatmap.png")
            await fitness_ch.send(embed=embed, file=file)
            logger.info("Heatmap posted for week %s", week_start)
        except Exception as e:
            logger.warning("Heatmap generation failed: %s", e)

    async def _select_photo_of_week(self, week_start: date):
        fitness_ch = await self._get_fitness_channel()
        if not fitness_ch:
            logger.warning("No fitness channel set; skipping photo of the week.")
            return

        week_end = week_start + timedelta(days=6)

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM activity_logs WHERE activity_date BETWEEN ? AND ? AND verified>0",
                (week_start.isoformat(), week_end.isoformat()),
            ).fetchall()

        if not rows:
            logger.info("No verified activities this week; no Photo of the Week.")
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
                # Earliest post wins ties
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

        # Announce in fitness channel
        guild = fitness_ch.guild
        winner = guild.get_member(best_user_id)
        winner_name = winner.display_name if winner else f"User {best_user_id}"

        try:
            winning_msg = await fitness_ch.fetch_message(best_msg_id)
        except discord.NotFound:
            winning_msg = None

        embed = discord.Embed(
            title="📸 Photo of the Week!",
            description=(
                f"🏅 Congratulations to **{winner_name}** for this week's most-reacted workout photo!\n\n"
                f"**Week of:** {week_start.strftime('%b %d')} – {(week_start + timedelta(days=6)).strftime('%b %d, %Y')}\n"
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

        # Motivating message (no comparisons to others/group)
        if this_week_count == 0:
            message = "Every journey starts with a single step. Next week is a fresh slate — you've got this! 💪"
        elif not hit_goal:
            message = f"You logged **{this_week_count}** day(s) this week. Keep building that momentum — consistency is everything!"
        elif tier == "Elite":
            message = f"🔥 **Elite week!** You crushed it with {this_week_count} active days. Incredible consistency!"
        else:
            message = f"✅ **Goal hit!** {this_week_count} active days this week — you're right on track. Keep it rolling!"

        embed = discord.Embed(
            title=f"📊 Your Weekly Summary",
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

    # ── Monday 9 AM — Group weekly announcement ───────────────────────────────

    async def weekly_group_announcement(self, use_current_week: bool = False):
        logger.info("Running weekly_group_announcement task (use_current_week=%s)", use_current_week)
        fitness_ch = await self._get_fitness_channel()
        if not fitness_ch:
            logger.warning("No fitness channel set; skipping weekly announcement.")
            return

        today = today_local()
        if use_current_week:
            week_start = week_start_for(today)
        else:
            prev_monday = today - timedelta(days=7)
            week_start = week_start_for(prev_monday)

        avg, member_count = compute_group_weekly_average(week_start)
        goal = float(get_setting("goal_days_per_week") or 4)
        success = avg >= goal

        current_streak, best_streak = update_group_streak(success, week_start)

        embed = discord.Embed(
            title="📅 Weekly Group Update",
            colour=COLOUR_SUCCESS if success else COLOUR_ERROR,
        )
        week_label = f"{week_start.strftime('%b %d')} – {(week_start + timedelta(days=6)).strftime('%b %d, %Y')}"
        embed.add_field(name="Week", value=week_label, inline=False)
        embed.add_field(name="Group Average", value=f"**{avg}** days / member", inline=True)
        embed.add_field(name="Active Members", value=str(member_count), inline=True)
        embed.add_field(name="Goal", value=f"{goal} days/wk", inline=True)

        if success:
            embed.description = f"✅ The group hit the goal! Group streak: **{current_streak}** week(s) 🔥"
        else:
            embed.description = f"📉 Goal not met this week (avg {avg} < {goal}). Group streak reset. Let's bounce back!"

        if best_streak > 1:
            embed.add_field(name="Best Group Streak", value=f"**{best_streak}** weeks", inline=False)

        await fitness_ch.send(embed=embed)
        logger.info("Weekly group announcement sent. avg=%.2f success=%s", avg, success)

    # ── Public trigger methods (used by debug cog) ────────────────────────────

    async def trigger_end_week(self, use_current_week: bool = False):
        """
        Trigger the weekly announcement.
        use_current_week=True uses the current week (for debug/testing).
        use_current_week=False (default/prod) uses the previous week.
        """
        await self.weekly_group_announcement(use_current_week=use_current_week)

    async def trigger_sunday(self):
        await self.sunday_wrap_up()

    async def trigger_show_summary(self, channel: discord.TextChannel):
        """Post current week's heatmap to a given channel."""
        week_start = current_week_start()
        try:
            buf = generate_weekly_heatmap(week_start)
            file = discord.File(buf, filename="heatmap.png")
            embed = base_embed(
                f"📊 Current Week Heatmap",
                f"Week of {week_start.strftime('%b %d, %Y')}",
            )
            embed.set_image(url="attachment://heatmap.png")
            await channel.send(embed=embed, file=file)
        except Exception as e:
            await channel.send(f"❌ Error generating heatmap: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(SchedulerCog(bot))