"""
activity_cog.py — Core activity tracking.

Handles:
- on_message listener for workout photos in the fitness channel
- on_raw_reaction_add for admin ✅ verification
- /add and /remove activity admin commands
"""

import logging
from datetime import date, datetime

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import (
    get_setting, log_activity, remove_activity, upsert_member,
    get_member, verify_activity, get_conn,
)
from bot.utils.checks import is_bot_admin
from bot.utils.embed_utils import success_embed, error_embed, warning_embed
from bot.utils.time_utils import today_local

logger = logging.getLogger(__name__)


class ActivityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Photo listener ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild is None:
            return

        fitness_channel_id = get_setting("fitness_channel_id")
        if not fitness_channel_id:
            return
        if message.channel.id != int(fitness_channel_id):
            return

        # Must have at least one image or video attachment (GIFs excluded)
        def _is_valid(a: discord.Attachment) -> bool:
            ct = a.content_type or ""
            if ct.startswith("image/gif"):
                return False
            if ct.startswith("image/"):
                return True
            if ct.startswith("video/"):
                return True
            return False

        has_valid = any(_is_valid(a) for a in message.attachments)
        if not has_valid:
            return

        user = message.author
        upsert_member(user.id, user.display_name)

        member_row = get_member(user.id)
        if member_row and not member_row["is_active"]:
            return  # Inactive members don't earn credit

        activity_date = today_local()
        manual_verification = get_setting("manual_verification") == "1"

        verified_flag = 0 if manual_verification else 1
        inserted = log_activity(
            user_id=user.id,
            activity_date=activity_date,
            message_id=message.id,
            channel_id=message.channel.id,
            verified=verified_flag,
        )

        pog_emoji_str = get_setting("pog_emoji") or "💪"

        if inserted:
            try:
                try:
                    emoji_id = int(pog_emoji_str.split(":")[-1].rstrip(">"))
                    emoji = discord.utils.get(message.guild.emojis, id=emoji_id)
                    # logger.debug(str(emoji_id) + ' ' + emoji)
                    if emoji:
                        await message.add_reaction(emoji)
                    else:
                        await message.add_reaction("<:PogU:1481438595133866175>")
                except (ValueError, IndexError):
                    await message.add_reaction("🔥")
            except discord.HTTPException as e:
                logger.warning("Failed to react to message: %s", e)

            if manual_verification:
                logger.info(
                    "Activity PENDING verification — user=%s date=%s message=%s",
                    user.id, activity_date, message.id,
                )
            else:
                logger.info(
                    "Activity logged — user=%s date=%s", user.id, activity_date
                )
        else:
            logger.debug(
                "Duplicate activity post ignored — user=%s already has credit for %s (no reaction added)",
                user.id, activity_date,
            )

    # ── ✅ Reaction verification ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.emoji.name != "✅":
            return
        if payload.member and payload.member.bot:
            return

        # Only admins' reactions count
        from bot.database import is_admin
        if not is_admin(payload.user_id):
            guild = self.bot.get_guild(payload.guild_id)
            if guild:
                member = guild.get_member(payload.user_id)
                if not (member and member.guild_permissions.administrator):
                    return

        # Verify based on the original message's post date (not today)
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM activity_logs WHERE message_id=? AND verified=0",
                (payload.message_id,),
            ).fetchone()
        if row is None:
            return  # Already verified or not tracked

        verified = verify_activity(payload.message_id)
        if verified:
            logger.info(
                "Activity manually verified — message=%s by admin=%s",
                payload.message_id, payload.user_id,
            )

    # ── /addactivity admin command ────────────────────────────────────────────

    @app_commands.command(
        name="addactivity",
        description="[Admin] Manually grant activity credit to a user.",
    )
    @app_commands.describe(
        member="The member to credit",
        activity_date="Date in YYYY-MM-DD format (defaults to today)",
    )
    @is_bot_admin()
    async def add_activity_cmd(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        activity_date: str | None = None,
    ):
        if activity_date:
            try:
                d = date.fromisoformat(activity_date)
            except ValueError:
                await interaction.response.send_message(
                    embed=error_embed("Invalid Date", "Use YYYY-MM-DD format."), ephemeral=True
                )
                return
        else:
            d = today_local()

        upsert_member(member.id, member.display_name)
        inserted = log_activity(
            user_id=member.id,
            activity_date=d,
            verified=2,  # 2 = manually added by admin
            added_by=interaction.user.id,
        )
        if inserted:
            await interaction.response.send_message(
                embed=success_embed(
                    "Activity Added",
                    f"Granted **{member.display_name}** credit for **{d.isoformat()}**.",
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=warning_embed(
                    "Already Credited",
                    f"**{member.display_name}** already has activity credit for **{d.isoformat()}**.",
                ),
                ephemeral=True,
            )

    # ── /removeactivity admin command ────────────────────────────────────────

    @app_commands.command(
        name="removeactivity",
        description="[Admin] Remove an activity credit from a user.",
    )
    @app_commands.describe(
        member="The member to remove credit from",
        activity_date="Date in YYYY-MM-DD format",
    )
    @is_bot_admin()
    async def remove_activity_cmd(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        activity_date: str,
    ):
        try:
            d = date.fromisoformat(activity_date)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Invalid Date", "Use YYYY-MM-DD format."), ephemeral=True
            )
            return

        removed = remove_activity(member.id, d)
        if removed:
            await interaction.response.send_message(
                embed=success_embed(
                    "Activity Removed",
                    f"Removed credit for **{member.display_name}** on **{d.isoformat()}**.",
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=error_embed(
                    "Not Found",
                    f"No activity credit found for **{member.display_name}** on **{d.isoformat()}**.",
                ),
                ephemeral=True,
            )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                embed=discord.Embed(description="🔒 Admin only.", colour=0xED4245),
                ephemeral=True,
            )
        else:
            logger.exception("ActivityCog error: %s", error)


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityCog(bot))