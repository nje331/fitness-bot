"""
bot.py — Fitness Challenge Discord Bot entry point.

Usage:
    python bot.py            # Production mode
    python bot.py --debug    # Debug mode (loads debug cog, uses TESTBOT_DISCORD_TOKEN)

Environment variables (from .env):
    DISCORD_TOKEN          — production bot token
    TESTBOT_DISCORD_TOKEN  — debug bot token
    DEBUG                  — set to "true" to force debug mode
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ─── CLI flags ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Fitness Challenge Discord Bot")
parser.add_argument("--debug", action="store_true", help="Enable debug mode")
args, _ = parser.parse_known_args()

DEBUG = args.debug or os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

# ─── Logging ──────────────────────────────────────────────────────────────────

log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/logs/bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

if DEBUG:
    logger.warning("=" * 50)
    logger.warning("  DEBUG MODE ACTIVE — using TESTBOT_DISCORD_TOKEN")
    logger.warning("=" * 50)

# ─── Database init ────────────────────────────────────────────────────────────

from bot.database import init_db
init_db()

# ─── Bot setup ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True


class FitnessBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",  # Unused but required
            intents=intents,
            help_command=None,
        )
        self.debug_mode = DEBUG

    async def setup_hook(self):
        # Load all cogs
        cogs = [
            "bot.cogs.admin_cog",
            "bot.cogs.activity_cog",
            "bot.cogs.scheduler_cog",
            "bot.cogs.user_cog",
        ]
        if DEBUG:
            cogs.append("bot.cogs.debug_cog")

        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info("Loaded cog: %s", cog)
            except Exception as e:
                logger.exception("Failed to load cog %s: %s", cog, e)

        # Sync slash commands globally
        synced = await self.tree.sync()
        logger.info("Synced %d slash command(s).", len(synced))

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Connected to %d guild(s).", len(self.guilds))
        if DEBUG:
            await self.change_presence(
                status=discord.Status.do_not_disturb,
                activity=discord.Activity(
                    type=discord.ActivityType.playing,
                    name="DEBUG MODE 🛠️",
                ),
            )
        else:
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name="/help to get started 💪",
                ),
            )

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ):
        if isinstance(error, discord.app_commands.CheckFailure):
            try:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="🔒 You don't have permission to use this command.",
                        colour=0xED4245,
                    ),
                    ephemeral=True,
                )
            except discord.InteractionResponded:
                pass
        else:
            logger.exception("Unhandled app command error: %s", error)


def main():
    token_key = "TESTBOT_DISCORD_TOKEN" if DEBUG else "DISCORD_TOKEN"
    token = os.getenv(token_key)
    if not token:
        logger.critical("Missing token: %s is not set in .env", token_key)
        sys.exit(1)

    bot = FitnessBot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
