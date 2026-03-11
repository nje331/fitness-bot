"""
debug_cog.py — Debug commands, only loaded when DEBUG=True.

/nextday      — Inserts an activity log for the invoker for tomorrow's date,
                simulating that a day has passed and they were active.
/endweek      — Triggers the full Monday weekly announcement including DMs.
/showsummary  — Posts the current week's heatmap silently (no follow-up).
/triggersunday — Triggers Sunday wrap-up (photo of week + DM summaries).
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

    @app_commands.command(
        name="nextday",
        description="[DEBUG] Log activity for yourself for tomorrow's date (simulates day advancing).",
    )
    async def nextday_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        tomorrow = today_local() + timedelta(days=1)
        upsert_member(interaction.user.id, interaction.user.display_name)
        inserted = log_activity(
            user_id=interaction.user.id,
            activity_date=tomorrow,
            verified=1,
            added_by=interaction.user.id,
        )

        if inserted:
            embed = discord.Embed(
                title="🧪 Next Day Activity Logged",
                description=(
                    f"Logged activity for **{interaction.user.display_name}** "
                    f"on **{tomorrow.isoformat()}** (tomorrow).\n\n"
                    "This simulates you being active on the next calendar day. "
                    "Use `/endweek` to trigger the weekly announcement."
                ),
                colour=COLOUR_WARNING,
            )
            embed.add_field(name="Today", value=str(today_local()), inline=True)
            embed.add_field(name="Logged For", value=str(tomorrow), inline=True)
        else:
            embed = discord.Embed(
                title="🧪 Already Logged",
                description=f"You already have activity logged for **{tomorrow.isoformat()}**.",
                colour=COLOUR_WARNING,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="endweek",
        description="[DEBUG] Trigger the full weekly announcement + DM summaries now.",
    )
    async def endweek_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(embed=warning_embed("Scheduler not loaded"), ephemeral=True)
            return
        try:
            # Pass current week so DMs go out for THIS week's data (useful in debug)
            await sched.trigger_end_week(use_current_week=True)
            await interaction.followup.send(
                embed=success_embed("Weekly announcement + DMs triggered!"), ephemeral=True
            )
        except Exception as e:
            logger.exception("Debug /endweek error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(
        name="showsummary",
        description="[DEBUG] Post the current week's heatmap to this channel.",
    )
    async def showsummary_cmd(self, interaction: discord.Interaction):
        # Defer publicly so the heatmap posts visibly, then delete the deferred acknowledgement
        await interaction.response.defer(ephemeral=True)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(embed=warning_embed("Scheduler not loaded"), ephemeral=True)
            return
        try:
            await sched.trigger_show_summary(interaction.channel)
            # Delete the ephemeral defer — no follow-up message, heatmap speaks for itself
            await interaction.delete_original_response()
        except Exception as e:
            logger.exception("Debug /showsummary error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(
        name="triggersunday",
        description="[DEBUG] Trigger Sunday wrap-up (photo of week + DM summaries).",
    )
    async def triggersunday_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(embed=warning_embed("Scheduler not loaded"), ephemeral=True)
            return
        try:
            await sched.trigger_sunday()
            await interaction.followup.send(
                embed=success_embed("Sunday wrap-up triggered!"), ephemeral=True
            )
        except Exception as e:
            logger.exception("Debug /triggersunday error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DebugCog(bot))
