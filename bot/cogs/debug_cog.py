"""
debug_cog.py — Debug commands, only loaded when DEBUG=True.

Commands:
  /nextday      — Simulate advancing one day (shows what "tomorrow" would trigger)
  /endweek      — Trigger the Monday weekly announcement immediately
  /showsummary  — Post the current week's heatmap to the current channel
"""

import logging
from datetime import date, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embed_utils import base_embed, warning_embed, COLOUR_WARNING

logger = logging.getLogger(__name__)


class DebugCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.warning("DebugCog loaded — DEBUG MODE IS ACTIVE")

    def _get_scheduler_cog(self):
        return self.bot.cogs.get("SchedulerCog")

    @app_commands.command(name="nextday", description="[DEBUG] Simulate advancing to the next day.")
    async def nextday_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="🧪 Next Day Simulated",
            description=(
                "Simulated advancing one day.\n"
                "• Sunday tasks run on Sunday 9 PM trigger\n"
                "• Monday tasks run on Monday 9 AM trigger\n\n"
                "Use `/endweek` to manually trigger the weekly announcement or `/showsummary` for the heatmap."
            ),
            colour=COLOUR_WARNING,
        )
        embed.add_field(name="Today (server)", value=str(date.today()), inline=True)
        embed.add_field(name="Simulated Tomorrow", value=str(date.today() + timedelta(days=1)), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="endweek", description="[DEBUG] Trigger the weekly group announcement now.")
    async def endweek_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(
                embed=warning_embed("Scheduler not loaded"), ephemeral=True
            )
            return
        try:
            await sched.trigger_end_week()
            await interaction.followup.send(
                embed=base_embed("✅ Weekly announcement triggered!"), ephemeral=True
            )
        except Exception as e:
            logger.exception("Debug /endweek error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="showsummary", description="[DEBUG] Post the current week's heatmap here.")
    async def showsummary_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(
                embed=warning_embed("Scheduler not loaded"), ephemeral=True
            )
            return
        try:
            await sched.trigger_show_summary(interaction.channel)
            await interaction.followup.send("✅ Heatmap posted above.", ephemeral=True)
        except Exception as e:
            logger.exception("Debug /showsummary error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @app_commands.command(name="triggersunday", description="[DEBUG] Trigger Sunday wrap-up (photo of week + DMs).")
    async def triggersunday_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        sched = self._get_scheduler_cog()
        if sched is None:
            await interaction.followup.send(embed=warning_embed("Scheduler not loaded"), ephemeral=True)
            return
        try:
            await sched.trigger_sunday()
            await interaction.followup.send(
                embed=base_embed("✅ Sunday wrap-up triggered!"), ephemeral=True
            )
        except Exception as e:
            logger.exception("Debug /triggersunday error: %s", e)
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DebugCog(bot))
