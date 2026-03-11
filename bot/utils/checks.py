"""
checks.py — Discord app_commands checks for admin gating.
"""

import discord
from discord import app_commands
from bot.database import is_admin


def is_bot_admin():
    """Check that the invoker is a bot admin (DB) OR has Administrator permission in Discord."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if is_admin(interaction.user.id):
            return True
        raise app_commands.CheckFailure(
            "You must be a bot admin or Discord Administrator to use this command."
        )
    return app_commands.check(predicate)
