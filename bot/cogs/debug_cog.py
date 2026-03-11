"""
debug_cog.py — Debug commands, only loaded when DEBUG=True.

/nextday       — Advance the bot's date by 1 day. Post a photo/video normally to log activity.
/resetday      — Reset the date offset back to real today.
/endweek       — Trigger the full weekly announcement + DMs.
/showsummary   — Post current week heatmap to this channel.
/triggersunday — Trigger the full Monday wrap-up for the current week.
"""

import logging
from datetime import date, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_conn
from bot.utils.embed_utils import warning_embed, success_embed, COLOUR_WARNING
from bot.utils.time_utils import today_local

logger = logging.getLogger(__name__)


class DebugCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.warning("DebugCog loaded — DEBUG MODE IS ACTIVE")

    def _get_scheduler_cog(self):
        return self.bot.cogs.get("SchedulerCog")

    # ── /nextday ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="nextday",
        description="[DEBUG] Advance the bot's date by 1 day. Post a photo/video to log activity as normal.",
    )
    async def nextday_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        current_offset = getattr(self.bot, "debug_date_offset", 0)
        new_offset = current_offset + 1
        self.bot.debug_date_offset = new_offset

        simulated_today = today_local()  # reflects new offset
        real_today = date.today()

        # Show which members already have credit on the new date vs. who's clear
        with get_conn() as conn:
            members = conn.execute(
                "SELECT user_id, username FROM members WHERE is_active=1"
            ).fetchall()
            already_credited = {
                row["user_id"]
                for row in conn.execute(
                    "SELECT user_id FROM activity_logs WHERE activity_date=? AND verified>0",
                    (simulated_today.isoformat(),),
                ).fetchall()
            }

        status_lines = []
        for m in members:
            if m["user_id"] in already_credited:
                status_lines.append(f"⚠️ **{m['username']}** — already has credit for this date")
            else:
                status_lines.append(f"✅ **{m['username']}** — clear")

        embed = discord.Embed(
            title="🧪 Day Advanced",
            description=(
                f"Bot date is now **{simulated_today}**.\n"
                "Post a photo or video in the activity channel to log credit as normal."
            ),
            colour=COLOUR_WARNING,
        )
        embed.add_field(name="Real Date", value=str(real_today), inline=True)
        embed.add_field(name="Bot Sees", value=str(simulated_today), inline=True)
        embed.add_field(name="Offset", value=f"+{new_offset} day(s)", inline=True)
        if status_lines:
            embed.add_field(name="Member Status", value="\n".join(status_lines), inline=False)

        await interaction.followup.send(embed=embed)

    # ── /resetday ─────────────────────────────────────────────────────────────

    @app_commands.command(
        name="resetday",
        description="[DEBUG] Reset the bot's date offset back to real today.",
    )
    async def resetday_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        self.bot.debug_date_offset = 0
        real_today = date.today()
        embed = discord.Embed(
            title="🧪 Date Reset",
            description=(
                f"Date offset reset to 0. Bot now sees **{real_today}** (real today).\n\n"
                "Note: any activity logs written during debug are still in the DB. "
                "Run `/cleardebugdata` to remove them."
            ),
            colour=COLOUR_WARNING,
        )
        await interaction.followup.send(embed=embed)

    # ── /cleardebugdata ───────────────────────────────────────────────────────

    @app_commands.command(
        name="cleardebugdata",
        description="[DEBUG] Delete all activity logs with a date strictly after real today.",
    )
    async def cleardebugdata_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        real_today = date.today().isoformat()

        with get_conn() as conn:
            # Show what will be deleted first
            rows = conn.execute(
                "SELECT activity_date, COUNT(*) as c FROM activity_logs "
                "WHERE activity_date > ? GROUP BY activity_date ORDER BY activity_date",
                (real_today,),
            ).fetchall()

            deleted = conn.execute(
                "DELETE FROM activity_logs WHERE activity_date > ?",
                (real_today,),
            ).rowcount

        embed = discord.Embed(
            title="🧹 Debug Data Cleared",
            colour=COLOUR_WARNING,
        )

        if deleted == 0:
            embed.description = f"No future-dated activity logs found (cutoff: {real_today}). Nothing to delete."
        else:
            detail = "\n".join(f"• {r['activity_date']}: {r['c']} log(s)" for r in rows)
            embed.description = (
                f"Deleted **{deleted}** activity log(s) with dates after **{real_today}**:\n\n"
                f"{detail}\n\n"
                "All members are now clear to earn activity credit on their next post."
            )

        await interaction.followup.send(embed=embed)

    # ── /endweek ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="endweek",
        description="[DEBUG] Trigger the full weekly announcement + DMs now.",
    )
    async def endweek_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(embed=warning_embed("Scheduler not loaded"))
            return
        try:
            await sched.trigger_end_week(use_current_week=True)
            await interaction.followup.send(
                embed=success_embed("Weekly Announcement Triggered", "Heatmap + DMs sent for the current week.")
            )
        except Exception as e:
            logger.exception("Debug /endweek error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}")

    # ── /showsummary ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="showsummary",
        description="[DEBUG] Post the current week's heatmap to this channel.",
    )
    async def showsummary_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(embed=warning_embed("Scheduler not loaded"))
            return
        try:
            await sched.trigger_show_summary(interaction.channel)
            await interaction.delete_original_response()
        except Exception as e:
            logger.exception("Debug /showsummary error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}")

    # ── /triggersunday ────────────────────────────────────────────────────────

    @app_commands.command(
        name="triggersunday",
        description="[DEBUG] Trigger full weekly wrap-up: heatmap + photo of week + DMs.",
    )
    async def triggersunday_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(embed=warning_embed("Scheduler not loaded"))
            return
        try:
            await sched.trigger_sunday()
            await interaction.followup.send(
                embed=success_embed("Wrap-up Triggered", "Heatmap, Photo of the Week, and DMs sent.")
            )
        except Exception as e:
            logger.exception("Debug /triggersunday error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(DebugCog(bot))