"""
debug_cog.py — Debug commands, only loaded when DEBUG=True.

/nextday       — Advances the bot's internal date by 1 day and logs activity for the invoker
                 on that new "today". Subsequent commands see the advanced date.
/resetday      — Resets the internal date offset back to 0 (real today).
/endweek       — Triggers the full Monday weekly announcement including DMs.
/showsummary   — Posts the current week's heatmap to this channel.
/triggersunday — Triggers the full Sunday wrap-up (heatmap + photo of week + DMs).
"""

import logging
from datetime import date, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import log_activity, upsert_member, get_setting
from bot.utils.embed_utils import base_embed, warning_embed, success_embed, COLOUR_WARNING
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
        description="[DEBUG] Advance the bot's date by 1 day and log your activity on that new date.",
    )
    async def nextday_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        # Advance the bot's internal date offset
        current_offset = getattr(self.bot, "debug_date_offset", 0)
        new_offset = current_offset + 1
        self.bot.debug_date_offset = new_offset

        # today_local() now returns real_today + new_offset
        simulated_today = today_local()  # already reflects new offset

        upsert_member(interaction.user.id, interaction.user.display_name)
        inserted = log_activity(
            user_id=interaction.user.id,
            activity_date=simulated_today,
            verified=1,
            added_by=interaction.user.id,
        )

        real_today = date.today()
        embed = discord.Embed(
            title="🧪 Day Advanced",
            colour=COLOUR_WARNING,
        )
        embed.add_field(name="Real Date", value=str(real_today), inline=True)
        embed.add_field(name="Bot Sees", value=str(simulated_today), inline=True)
        embed.add_field(name="Offset", value=f"+{new_offset} day(s)", inline=True)

        if inserted:
            embed.description = (
                f"Advanced the bot's date to **{simulated_today}** and logged activity "
                f"for **{interaction.user.display_name}**.\n\n"
                "Use `/nextday` again to advance another day, or `/endweek` to trigger the weekly announcement."
            )
        else:
            embed.description = (
                f"Advanced the bot's date to **{simulated_today}**.\n"
                f"⚠️ **{interaction.user.display_name}** already has activity logged for this date — no new entry added."
            )

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
            description=f"Bot date offset reset. Bot now sees **{real_today}** (real today).",
            colour=COLOUR_WARNING,
        )
        await interaction.followup.send(embed=embed)

    # ── /endweek ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="endweek",
        description="[DEBUG] Trigger the full weekly announcement + DM summaries now.",
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
        description="[DEBUG] Trigger full Sunday wrap-up: heatmap + photo of week + DMs.",
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
                embed=success_embed("Sunday Wrap-up Triggered", "Heatmap, Photo of the Week, and DMs sent.")
            )
        except Exception as e:
            logger.exception("Debug /triggersunday error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(DebugCog(bot))