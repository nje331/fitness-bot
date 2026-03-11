"""
admin_cog.py — /admins, /settings, /members commands.
All admin-only.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import logging
from datetime import date

from bot.database import (
    add_admin, remove_admin, get_admins, is_admin,
    get_setting, set_setting, get_all_settings,
    upsert_member, set_member_active, get_conn,
)
from bot.utils.checks import is_bot_admin
from bot.utils.embed_utils import base_embed, success_embed, error_embed, COLOUR_PRIMARY

logger = logging.getLogger(__name__)


# ─── /admins ──────────────────────────────────────────────────────────────────

class AdminsView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=120)
        self.guild = guild

    async def _refresh(self, interaction: discord.Interaction):
        embed, view = await build_admins_embed_view(self.guild)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="➕ Add Admin", style=discord.ButtonStyle.green)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddAdminModal(self.guild))

    @discord.ui.button(label="➖ Remove Admin", style=discord.ButtonStyle.red)
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemoveAdminModal(self.guild))


class AddAdminModal(discord.ui.Modal, title="Add Admin"):
    user_input = discord.ui.TextInput(
        label="User ID or @mention",
        placeholder="123456789012345678",
        required=True,
    )

    def __init__(self, guild: discord.Guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_input.value.strip().strip("<@!>")
        try:
            uid = int(raw)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Invalid User", "Please enter a valid user ID."), ephemeral=True
            )
            return
        member = self.guild.get_member(uid)
        if member is None:
            await interaction.response.send_message(
                embed=error_embed("User Not Found", "That user isn't in this server."), ephemeral=True
            )
            return
        add_admin(uid, interaction.user.id)
        upsert_member(uid, member.display_name)
        embed, view = await build_admins_embed_view(self.guild)
        await interaction.response.edit_message(embed=embed, view=view)


class RemoveAdminModal(discord.ui.Modal, title="Remove Admin"):
    user_input = discord.ui.TextInput(
        label="User ID to remove",
        placeholder="123456789012345678",
        required=True,
    )

    def __init__(self, guild: discord.Guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_input.value.strip().strip("<@!>")
        try:
            uid = int(raw)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Invalid ID"), ephemeral=True
            )
            return
        if uid == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("Can't remove yourself"), ephemeral=True
            )
            return
        remove_admin(uid)
        embed, view = await build_admins_embed_view(self.guild)
        await interaction.response.edit_message(embed=embed, view=view)


async def build_admins_embed_view(guild: discord.Guild):
    rows = get_admins()
    embed = base_embed("🔐 Bot Admins", "Admins can configure the bot and manage activities.")
    if not rows:
        embed.description = "No bot admins configured yet. Discord Administrators always have access."
    else:
        lines = []
        for r in rows:
            m = guild.get_member(r["user_id"])
            name = m.display_name if m else f"Unknown ({r['user_id']})"
            lines.append(f"• **{name}** (`{r['user_id']}`)")
        embed.add_field(name=f"Current Admins ({len(rows)})", value="\n".join(lines), inline=False)
    view = AdminsView(guild)
    return embed, view


# ─── /settings ────────────────────────────────────────────────────────────────

class SettingsView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.guild = guild

    @discord.ui.button(label="📺 Set Fitness Channel", style=discord.ButtonStyle.primary, row=0)
    async def set_fitness_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelModal("fitness_channel_id", "Fitness Channel ID", self.guild))

    @discord.ui.button(label="🔔 Set Admin Channel", style=discord.ButtonStyle.primary, row=0)
    async def set_admin_ch(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelModal("admin_channel_id", "Admin Channel ID", self.guild))

    @discord.ui.button(label="🎯 Set Goal (days/wk)", style=discord.ButtonStyle.secondary, row=1)
    async def set_goal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FloatSettingModal("goal_days_per_week", "Goal (days/week)", "e.g. 4"))

    @discord.ui.button(label="🏆 Set Elite Goal", style=discord.ButtonStyle.secondary, row=1)
    async def set_elite(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FloatSettingModal("elite_days_per_week", "Elite Goal (days/week)", "e.g. 5.5"))

    @discord.ui.button(label="📅 Set Challenge Dates", style=discord.ButtonStyle.secondary, row=2)
    async def set_dates(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChallengeDatesModal())

    @discord.ui.button(label="🕐 Grace Days", style=discord.ButtonStyle.secondary, row=2)
    async def set_grace(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FloatSettingModal("grace_days", "Grace Days (0=off, 1=on)", "0 or 1"))

    @discord.ui.button(label="✅ Toggle Manual Verification", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_verification(self, interaction: discord.Interaction, button: discord.ui.Button):
        cur = get_setting("manual_verification")
        new = "0" if cur == "1" else "1"
        set_setting("manual_verification", new)
        state = "ON" if new == "1" else "OFF"
        await interaction.response.send_message(
            embed=success_embed(f"Manual Verification {state}"), ephemeral=True
        )

    @discord.ui.button(label="🌎 Set Timezone", style=discord.ButtonStyle.secondary, row=3)
    async def set_tz(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FloatSettingModal("timezone", "Timezone", "e.g. US/Eastern"))

    @discord.ui.button(label="🏅 Elite Reward Description", style=discord.ButtonStyle.secondary, row=4)
    async def set_elite_reward(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FloatSettingModal("elite_reward_text", "Elite Reward Description", "Describe the elite reward..."))


def build_settings_embed() -> discord.Embed:
    s = get_all_settings()
    mv = "✅ ON" if s["manual_verification"] == "1" else "🟡 OFF (auto)"
    grace = "OFF" if s["grace_days"] == "0" else f"{s['grace_days']} day(s)"
    fc = f"<#{s['fitness_channel_id']}>" if s["fitness_channel_id"] else "Not set"
    ac = f"<#{s['admin_channel_id']}>" if s["admin_channel_id"] else "Not set"

    embed = base_embed("⚙️ Bot Settings")
    embed.add_field(name="📺 Fitness Channel", value=fc, inline=True)
    embed.add_field(name="🔔 Admin Channel", value=ac, inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="🎯 Goal", value=f"{s['goal_days_per_week']} days/wk", inline=True)
    embed.add_field(name="🏆 Elite Goal", value=f"{s['elite_days_per_week']} days/wk", inline=True)
    embed.add_field(name="🕐 Grace Days", value=grace, inline=True)
    embed.add_field(name="✅ Manual Verification", value=mv, inline=True)
    embed.add_field(name="🌎 Timezone", value=s["timezone"], inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    cs = s["challenge_start"] or "Not set"
    ce = s["challenge_end"] or "Not set"
    embed.add_field(name="📅 Challenge", value=f"{cs} → {ce}", inline=False)
    embed.add_field(name="🏅 Elite Reward", value=s["elite_reward_text"] or "Not set", inline=False)
    return embed


class ChannelModal(discord.ui.Modal):
    channel_input = discord.ui.TextInput(
        label="Channel ID",
        placeholder="Right-click channel → Copy ID",
        required=True,
        max_length=25,
    )

    def __init__(self, setting_key: str, title: str, guild: discord.Guild):
        super().__init__(title=title[:45])
        self.setting_key = setting_key
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        val = self.channel_input.value.strip()
        try:
            cid = int(val)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Invalid channel ID"), ephemeral=True)
            return
        ch = self.guild.get_channel(cid)
        if ch is None:
            await interaction.response.send_message(embed=error_embed("Channel not found in this server"), ephemeral=True)
            return
        set_setting(self.setting_key, str(cid))
        await interaction.response.send_message(
            embed=success_embed("Channel Updated", f"Set to {ch.mention}"), ephemeral=True
        )


class FloatSettingModal(discord.ui.Modal):
    value_input = discord.ui.TextInput(label="Value", required=True, max_length=200)

    def __init__(self, setting_key: str, title: str, placeholder: str = ""):
        super().__init__(title=title[:45])
        self.setting_key = setting_key
        self.value_input.placeholder = placeholder

    async def on_submit(self, interaction: discord.Interaction):
        set_setting(self.setting_key, self.value_input.value.strip())
        await interaction.response.send_message(
            embed=success_embed("Setting Updated"), ephemeral=True
        )


class ChallengeDatesModal(discord.ui.Modal, title="Set Challenge Dates"):
    start = discord.ui.TextInput(label="Start Date (YYYY-MM-DD)", placeholder="2025-01-06", required=True)
    end   = discord.ui.TextInput(label="End Date (YYYY-MM-DD)", placeholder="2025-03-30", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            date.fromisoformat(self.start.value.strip())
            date.fromisoformat(self.end.value.strip())
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Invalid date format. Use YYYY-MM-DD."), ephemeral=True
            )
            return
        set_setting("challenge_start", self.start.value.strip())
        set_setting("challenge_end", self.end.value.strip())
        await interaction.response.send_message(
            embed=success_embed("Challenge Dates Set", f"{self.start.value} → {self.end.value}"),
            ephemeral=True,
        )


# ─── /members ────────────────────────────────────────────────────────────────

class MembersView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=120)
        self.guild = guild

    @discord.ui.button(label="➕ Add Member", style=discord.ButtonStyle.green)
    async def add_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddMemberModal(self.guild))

    @discord.ui.button(label="🚫 Make Inactive", style=discord.ButtonStyle.red)
    async def deactivate_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DeactivateMemberModal(self.guild))

    @discord.ui.button(label="✅ Reactivate", style=discord.ButtonStyle.secondary)
    async def reactivate_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReactivateMemberModal(self.guild))


def build_members_embed(guild: discord.Guild) -> discord.Embed:
    with get_conn() as conn:
        active = conn.execute("SELECT * FROM members WHERE is_active=1 ORDER BY username").fetchall()
        inactive = conn.execute("SELECT * FROM members WHERE is_active=0 ORDER BY username").fetchall()

    embed = base_embed("👥 Challenge Members")
    if active:
        lines = [f"• **{r['username']}** (`{r['user_id']}`)" for r in active]
        embed.add_field(name=f"Active ({len(active)})", value="\n".join(lines) or "None", inline=False)
    else:
        embed.add_field(name="Active", value="No active members yet.", inline=False)
    if inactive:
        lines = [f"• ~~{r['username']}~~ (`{r['user_id']}`)" for r in inactive]
        embed.add_field(name=f"Inactive ({len(inactive)})", value="\n".join(lines), inline=False)
    return embed


class AddMemberModal(discord.ui.Modal, title="Add Member"):
    user_input = discord.ui.TextInput(label="User ID", placeholder="123456789012345678", required=True)

    def __init__(self, guild: discord.Guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_input.value.strip().strip("<@!>")
        try:
            uid = int(raw)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Invalid user ID"), ephemeral=True)
            return
        member = self.guild.get_member(uid)
        if member is None:
            await interaction.response.send_message(embed=error_embed("User not in server"), ephemeral=True)
            return
        upsert_member(uid, member.display_name)
        # Ensure they're active
        set_member_active(uid, True)
        embed = build_members_embed(self.guild)
        await interaction.response.edit_message(embed=embed, view=MembersView(self.guild))


class DeactivateMemberModal(discord.ui.Modal, title="Make Member Inactive"):
    user_input = discord.ui.TextInput(label="User ID", placeholder="123456789012345678", required=True)

    def __init__(self, guild: discord.Guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_input.value.strip().strip("<@!>")
        try:
            uid = int(raw)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Invalid user ID"), ephemeral=True)
            return
        set_member_active(uid, False)
        embed = build_members_embed(self.guild)
        await interaction.response.edit_message(embed=embed, view=MembersView(self.guild))


class ReactivateMemberModal(discord.ui.Modal, title="Reactivate Member"):
    user_input = discord.ui.TextInput(label="User ID", placeholder="123456789012345678", required=True)

    def __init__(self, guild: discord.Guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_input.value.strip().strip("<@!>")
        try:
            uid = int(raw)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Invalid user ID"), ephemeral=True)
            return
        set_member_active(uid, True)
        embed = build_members_embed(self.guild)
        await interaction.response.edit_message(embed=embed, view=MembersView(self.guild))


# ─── Cog ─────────────────────────────────────────────────────────────────────

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="admins", description="Manage bot admins.")
    @is_bot_admin()
    async def admins_cmd(self, interaction: discord.Interaction):
        embed, view = await build_admins_embed_view(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="settings", description="Configure the fitness challenge bot.")
    @is_bot_admin()
    async def settings_cmd(self, interaction: discord.Interaction):
        embed = build_settings_embed()
        view = SettingsView(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="members", description="View and manage challenge members.")
    @is_bot_admin()
    async def members_cmd(self, interaction: discord.Interaction):
        embed = build_members_embed(interaction.guild)
        view = MembersView(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                embed=discord.Embed(description="🔒 You don't have permission to use this command.", colour=0xED4245),
                ephemeral=True,
            )
        else:
            logger.exception("Admin cog error: %s", error)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
