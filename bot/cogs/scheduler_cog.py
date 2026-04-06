"""
scheduler_cog.py — Scheduled tasks using discord.ext.tasks.

Monday 8 AM ET:
  1. Weekly heatmap + group summary posted to fitness channel
  2. Group trend chart posted alongside the heatmap
  3. Photo of the Week selected and announced
  4. Personal DM summaries sent to opted-in members

Hourly safety check:
  - If it's Monday AND the hour is >= 8 AND the weekly send hasn't fired yet today, trigger it.
  - The 8 AM task always sets last_weekly_sent before the next hourly tick, preventing double-sends.
"""

import logging
from datetime import date, timedelta, datetime, timezone, time as dtime
from typing import Optional
from zoneinfo import ZoneInfo
import asyncio

import discord
from discord.ext import commands, tasks
import pytz

from bot.database import (
    get_setting, set_setting, set_photo_of_week,
    update_group_streak, get_conn,
    get_total_activity_count, get_most_active_day_of_week,
)
from bot.utils.time_utils import current_week_start, week_start_for, today_local
from bot.utils.streak_utils import (
    compute_group_weekly_average, compute_daily_streak,
    compute_weekly_streak, compute_weekly_average, get_user_tier,
)
from bot.utils.viz_utils import generate_weekly_heatmap, generate_user_activity_chart, generate_group_trend_chart
from bot.utils.embed_utils import base_embed, COLOUR_SUCCESS, COLOUR_ERROR, COLOUR_ELITE, COLOUR_WARNING

logger = logging.getLogger(__name__)

_ET = pytz.timezone("US/Eastern")


def et_time(hour: int, minute: int) -> dtime:
    """Return a UTC-fixed dtime for the given ET wall-clock time (accounts for DST)."""
    now_et = datetime.now(_ET).replace(hour=hour, minute=minute, second=0, microsecond=0)
    utc_dt = now_et - now_et.utcoffset()
    return dtime(hour=utc_dt.hour, minute=utc_dt.minute, tzinfo=timezone.utc)


_MONDAY_8AM_ET = et_time(8, 0)


class SchedulerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.monday_tasks.start()
        self.hourly_check.start()
        logger.info("Scheduler started.")

    def cog_unload(self):
        self.monday_tasks.cancel()
        self.hourly_check.cancel()

    # ── Admin channel helpers ─────────────────────────────────────────────────

    async def _get_admin_channel(self) -> Optional[discord.TextChannel]:
        cid = get_setting("admin_channel_id")
        if not cid:
            return None
        ch = self.bot.get_channel(int(cid))
        return ch if isinstance(ch, discord.TextChannel) else None

    async def _admin_log(self, message: str = "", embed: discord.Embed = None):
        try:
            ch = await self._get_admin_channel()
            if ch:
                await ch.send(content=message or None, embed=embed)
            else:
                logger.info("Admin log (no channel): %s", message)
        except Exception as e:
            logger.warning("Failed to post admin log: %s", e)

    async def _admin_error(self, title: str, description: str):
        from bot.utils.embed_utils import error_embed
        embed = error_embed(title, description)
        ch = await self._get_admin_channel()
        if ch:
            try:
                await ch.send(embed=embed)
            except Exception as e:
                logger.warning("Failed to post admin error: %s", e)
        logger.error("Admin error — %s: %s", title, description)

    # ── Monday 8 AM loop ──────────────────────────────────────────────────────

    @tasks.loop(time=_MONDAY_8AM_ET)
    async def monday_tasks(self):
        today = today_local()
        if today.weekday() != 0:
            logger.info("Daily Check: weekday=%d not Monday, skipping.", today.weekday())
            return
        logger.info("Monday 8 AM task firing.")
        prev_monday = today - timedelta(days=7)
        week_start = week_start_for(prev_monday)
        # Set the flag BEFORE running so the hourly check (if it fires concurrently)
        # will see it and skip. _run_weekly is idempotent for the same week_start.
        set_setting("last_weekly_sent", today.isoformat())
        await self._run_weekly(week_start)

    @monday_tasks.before_loop
    async def before_monday_tasks(self):
        await self.bot.wait_until_ready()

    # ── Hourly safety check ───────────────────────────────────────────────────

    @tasks.loop(hours=1)
    async def hourly_check(self):
        today = today_local()
        last_sent = get_setting("last_weekly_sent")
        logger.debug("hourly_check: today=%s weekday=%s last_sent=%s", today, today.weekday(), last_sent)

        if today.weekday() != 0:
            return  # Not Monday

        if last_sent == today.isoformat():
            return  # Already sent today

        # Only trigger the safety send if the current local time is strictly after 8:00 AM ET.
        now_et = datetime.now(ZoneInfo("America/New_York"))
        if (now_et.hour < 8) or (now_et.hour == 8 and now_et.minute == 0):
            logger.debug("Hourly check: before or exactly at 8:00 AM ET, standing by.")
            return

        logger.warning("Hourly check: Monday send not recorded — triggering safety send now.")
        prev_monday = today - timedelta(days=7)
        week_start = week_start_for(prev_monday)
        set_setting("last_weekly_sent", today.isoformat())
        await self._run_weekly(week_start)

    @hourly_check.before_loop
    async def before_hourly_check(self):
        await self.bot.wait_until_ready()

    # ── Orchestrator ──────────────────────────────────────────────────────────

    async def _run_weekly(self, week_start: date):
        await self._post_heatmap_and_summary(week_start)
        await self._select_photo_of_week(week_start)
        await self._send_dm_summaries(week_start)

    # ── Channel helper ────────────────────────────────────────────────────────

    async def _get_fitness_channel(self) -> Optional[discord.TextChannel]:
        cid = get_setting("fitness_channel_id")
        if not cid:
            return None
        ch = self.bot.get_channel(int(cid))
        return ch if isinstance(ch, discord.TextChannel) else None

    # ── 1. Heatmap + group summary + trend chart ──────────────────────────────

    async def _post_heatmap_and_summary(self, week_start: date):
        fitness_ch = await self._get_fitness_channel()
        if not fitness_ch:
            logger.warning("No fitness channel set; skipping heatmap.")
            await self._admin_error("Weekly Send Failed", "No fitness channel configured — heatmap not posted.")
            return

        week_end = week_start + timedelta(days=6)
        week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

        avg, member_count = compute_group_weekly_average(week_start)
        goal = float(get_setting("goal_days_per_week") or 4)
        success = avg >= goal
        current_streak, best_streak, new_record = update_group_streak(success, week_start)

        colour = COLOUR_SUCCESS if success else COLOUR_ERROR

        if success:
            if current_streak == 1:
                streak_line = "✅ **We're back!** The group hit the goal this week — the streak is alive. Let's string some weeks together. 💪"
            else:
                streak_line = f"✅ **We did it again!** {current_streak} weeks in a row and counting. The group is on a roll. 🔥"
        else:
            if new_record:
                streak_line = (
                    f"📉 Tough week — the streak ends at **{best_streak}** weeks, a new all-time record. "
                    f"That run was something special. Now let's start a new one."
                )
            elif best_streak > 1:
                streak_line = (
                    f"📉 Didn't quite get there this week (avg **{avg}** vs goal **{goal}**). "
                    f"Streak resets, but our best is still **{best_streak}** weeks — let's chase it down. "
                    f"Reset. Refocus. Next week is a fresh shot. 💪"
                )
            else:
                streak_line = (
                    f"📉 Not our week (avg **{avg}** vs goal **{goal}**). "
                    f"Every week is a blank slate — let's show up stronger next time."
                )

        description = (
            f"{streak_line}\n\n"
            f"**Group avg:** {avg} days/member  •  "
            f"**Goal:** {goal} days/wk  •  "
            f"**Members:** {member_count}"
        )

        embed = discord.Embed(
            title=f"📊 Weekly Summary — {week_label}",
            description=description,
            colour=colour,
        )
        embed.set_footer(text="💪 Activity Challenge Bot")

        files = []
        try:
            heatmap_buf = generate_weekly_heatmap(week_start)
            heatmap_file = discord.File(heatmap_buf, filename="heatmap.png")
            embed.set_image(url="attachment://heatmap.png")
            files.append(heatmap_file)
        except Exception as e:
            logger.warning("Heatmap generation failed: %s", e)
            await self._admin_error("Heatmap Generation Failed", str(e))

        await fitness_ch.send(embed=embed, files=files if files else discord.utils.MISSING)

        # Trend chart — exclude the current (in-progress) week so the chart only
        # shows completed weeks.
        start_str = get_setting("challenge_start")
        if start_str:
            try:
                from datetime import date as _date
                challenge_start = _date.fromisoformat(start_str)
                trend_buf = generate_group_trend_chart(challenge_start, until=week_start)
                trend_file = discord.File(trend_buf, filename="trend.png")
                trend_embed = discord.Embed(
                    title="📈 Challenge Progress — Group Avg by Week",
                    colour=0x5865F2,
                )
                trend_embed.set_image(url="attachment://trend.png")
                trend_embed.set_footer(text="💪 Activity Challenge Bot")
                await fitness_ch.send(embed=trend_embed, file=trend_file)
            except Exception as e:
                logger.warning("Trend chart failed: %s", e)
                await self._admin_error("Trend Chart Failed", str(e))

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
            if row["message_id"] is None or row["channel_id"] is None:
                continue
            try:
                ch = self.bot.get_channel(int(row["channel_id"]))
                if ch is None:
                    continue
                msg = await ch.fetch_message(int(row["message_id"]))
                reaction_count = sum(r.count for r in msg.reactions)
                if reaction_count > best_reactions:
                    best_reactions = reaction_count
                    best_msg_id = int(row["message_id"])
                    best_user_id = row["user_id"]
                    best_channel_id = int(row["channel_id"])
            except (discord.NotFound, discord.HTTPException) as e:
                logger.warning("Couldn't fetch message %s: %s", row["message_id"], e)

        if best_msg_id is None:
            for row in rows:
                best_user_id = row["user_id"]
                break

        if best_user_id is None:
            return

        set_photo_of_week(week_start, best_user_id, best_msg_id, best_channel_id, max(best_reactions, 0))

        guild = fitness_ch.guild
        winner = guild.get_member(best_user_id)
        winner_name = winner.display_name if winner else f"User {best_user_id}"
        week_label = f"{week_start.strftime('%b %d')} – {(week_start + timedelta(days=6)).strftime('%b %d, %Y')}"

        embed = discord.Embed(title="📸 Photo of the Week!", colour=COLOUR_ELITE)

        if best_msg_id is not None:
            embed.description = (
                f"🏅 **{winner_name}** takes this week's crown — most-reacted photo of the bunch!\n\n"
                f"**Week of:** {week_label}\n"
                f"**Reactions:** {best_reactions}"
            )
            try:
                winning_msg = await fitness_ch.fetch_message(best_msg_id)
                embed.add_field(name="Jump to post", value=winning_msg.jump_url, inline=False)
                if winning_msg.attachments:
                    embed.set_image(url=winning_msg.attachments[0].url)
            except discord.NotFound:
                pass
        else:
            embed.description = (
                f"🏅 **{winner_name}** was the standout member this week!\n\n"
                f"**Week of:** {week_label}"
            )

        await fitness_ch.send(embed=embed)
        logger.info("Photo of the Week announced: user=%s message=%s", best_user_id, best_msg_id)

    # ── 3. DM summaries ───────────────────────────────────────────────────────

    async def _send_dm_summaries(self, week_start: date):
        with get_conn() as conn:
            members = conn.execute(
                "SELECT * FROM members WHERE is_active=1 AND dm_updates=1"
            ).fetchall()

        goal = float(get_setting("goal_days_per_week") or 4)
        elite_goal = float(get_setting("elite_days_per_week") or 5.5)

        sent_to: list[str] = []
        errors: list[str] = []

        for m in members:
            try:
                username = await self._dm_user_summary(m["user_id"], week_start, goal, elite_goal)
                sent_to.append(username)
            except Exception as e:
                err_msg = f"{m['username']} (ID {m['user_id']}): {e}"
                errors.append(err_msg)
                logger.warning("Failed to DM user %s: %s", m["user_id"], e)

        # Admin log
        week_label = f"{week_start.strftime('%b %d')} – {(week_start + timedelta(days=6)).strftime('%b %d, %Y')}"
        embed = discord.Embed(
            title="📬 Weekly DMs Sent",
            colour=COLOUR_SUCCESS if not errors else COLOUR_WARNING,
        )
        embed.add_field(
            name=f"✅ Delivered ({len(sent_to)})",
            value="\n".join(f"• {n}" for n in sent_to) if sent_to else "—",
            inline=False,
        )
        if errors:
            embed.add_field(
                name=f"⚠️ Failed ({len(errors)})",
                value="\n".join(f"• {e}" for e in errors),
                inline=False,
            )
        embed.set_footer(text=f"Week of {week_label}")
        await self._admin_log(embed=embed)

    async def _dm_user_summary(self, user_id: int, week_start: date, goal: float, elite_goal: float) -> str:
        """Send weekly DM to one user. Returns display name. Raises on failure."""
        from bot.database import get_activity_for_week
        user = await self.bot.fetch_user(user_id)
        if user is None:
            raise ValueError("User not found")

        week_days = get_activity_for_week(user_id, week_start)
        this_week_count = len(week_days)
        avg = compute_weekly_average(user_id)
        tier = get_user_tier(user_id)
        daily_streak, best_daily = compute_daily_streak(user_id)
        weekly_streak, best_weekly = compute_weekly_streak(user_id)
        total_days = get_total_activity_count(user_id)
        best_day = get_most_active_day_of_week(user_id)

        hit_goal = this_week_count >= goal
        hit_elite = this_week_count >= elite_goal
        colour = COLOUR_SUCCESS if hit_goal else COLOUR_ERROR

        if this_week_count == 0:
            message = (
                "No activity logged this week — life happens, no judgment. "
                "But next week is a blank slate. Even one walk counts. Come back swinging. 🚶"
            )
        elif hit_elite:
            message = (
                f"**Elite week.** 🔥 {this_week_count} days, no shortcuts, no excuses. "
                f"You're setting the standard — keep that energy going."
            )
        elif hit_goal:
            message = (
                f"**Goal hit!** 🎯 {this_week_count} active days this week — you're right where you need to be. "
                f"Keep that rhythm and the results will follow."
            )
        else:
            days_short = goal - this_week_count
            proximity = "a day away" if days_short <= 1 else ("a couple days away" if days_short <= 2 else "a few days away")
            days_from_elite = elite_goal - this_week_count
            if this_week_count >= goal - 1 and days_from_elite <= 1:
                elite_nudge = " You were just a day away from Elite status too — that's within reach."
            elif this_week_count >= 5:
                elite_nudge = f" At {this_week_count} days you're knocking on Elite's door — a few strong weeks and you'll be right on track!"
            else:
                elite_nudge = " A few strong weeks and you'll be right on track!"
            message = f"You logged **{this_week_count}** day(s) this week — {proximity} from the goal.{elite_nudge}"

        embed = discord.Embed(title="📊 Your Weekly Summary", description=message, colour=colour)

        tier_emoji = "🥇" if tier == "Elite" else ("🎯" if tier == "Baseline" else "📈")
        embed.add_field(name="This Week", value=f"**{this_week_count}** active days", inline=True)
        embed.add_field(name="Weekly Avg", value=f"**{avg}** days/wk", inline=True)
        embed.add_field(name="Tier", value=f"{tier_emoji} **{tier}**", inline=True)
        embed.add_field(name="🔥 Daily Streak", value=f"Current: **{daily_streak}** | Best: **{best_daily}**", inline=False)
        embed.add_field(name="📅 Weekly Streak", value=f"Current: **{weekly_streak}** wks | Best: **{best_weekly}** wks", inline=False)
        embed.add_field(name="🏆 Total Days Logged", value=f"**{total_days}** days since the challenge started", inline=False)
        if best_day:
            embed.add_field(name="📆 Your Strongest Day", value=f"You tend to show up most on **{best_day}s** — lean into it.", inline=False)

        embed.set_footer(text="Want to stop receiving these? Use /updates in the server.")

        files = []
        try:
            # Exclude current week so the chart only shows completed data
            chart_buf = generate_user_activity_chart(user_id, exclude_current_week=True)
            if chart_buf:
                chart_file = discord.File(chart_buf, filename="activity.png")
                embed.set_image(url="attachment://activity.png")
                files.append(chart_file)
        except Exception as e:
            logger.warning("Activity chart failed for user %s: %s", user_id, e)

        await user.send(embed=embed, files=files if files else discord.utils.MISSING)
        logger.info("DM summary sent to user %s", user_id)
        return user.display_name

    # ── Public trigger methods (used by debug cog) ────────────────────────────

    async def trigger_end_week(self, use_current_week: bool = False):
        today = today_local()
        week_start = week_start_for(today) if use_current_week else week_start_for(today - timedelta(days=7))
        await self._run_weekly(week_start)

    async def trigger_sunday(self):
        week_start = week_start_for(today_local())
        await self._run_weekly(week_start)

    async def trigger_show_summary(self, channel: discord.TextChannel):
        week_start = current_week_start()
        week_end = week_start + timedelta(days=6)
        week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

        avg, member_count = compute_group_weekly_average(week_start)
        goal = float(get_setting("goal_days_per_week") or 4)
        success = avg >= goal

        embed = discord.Embed(
            title=f"📊 Current Week — {week_label}",
            description=(
                f"**Group avg:** {avg} days/member  •  "
                f"**Goal:** {goal} days/wk  •  "
                f"**Members:** {member_count}"
            ),
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