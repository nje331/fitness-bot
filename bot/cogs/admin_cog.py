"""
admin_cog.py — /admins, /settings, /members commands.
All admin-only.

Key design:
- Channel selects use Discord ChannelSelect (no ID copying)
- Member add/deactivate/reactivate use Discord UserSelect
- Settings embeds live-update in place (no ephemeral pop-ups)
- Toggle buttons flip green <-> red
- All views disable on timeout
- Every admin action posts a summary to the admin channel
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
    get_total_activity_count,
)
from bot.utils.checks import is_bot_admin
from bot.utils.embed_utils import base_embed, error_embed, success_embed, COLOUR_PRIMARY, COLOUR_WARNING

logger = logging.getLogger(__name__)


# ── Admin channel helper (module-level so views can use it) ──────────────────

async def _post_admin_log(bot: commands.Bot, embed: discord.Embed) -> None:
    """Post an embed to the admin channel. Silently skips if not configured."""
    try:
        cid = get_setting("admin_channel_id")
        if not cid:
            return
        ch = bot.get_channel(int(cid))
        if isinstance(ch, discord.TextChannel):
            await ch.send(embed=embed)
    except Exception as e:
        logger.warning("Failed to post admin log: %s", e)


async def _post_admin_error(bot: commands.Bot, title: str, description: str) -> None:
    embed = error_embed(title, description)
    await _post_admin_log(bot, embed)
    logger.error("Admin error — %s: %s", title, description)


# ── Shared timeout mixin ──────────────────────────────────────────────────────

class DisableOnTimeout(discord.ui.View):
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if hasattr(self, "_message") and self._message:
            try:
                await self._message.edit(view=self)
            except Exception:
                pass


# ─── /admins ──────────────────────────────────────────────────────────────────

class AdminsView(DisableOnTimeout):
    def __init__(self, guild: discord.Guild, bot: commands.Bot):
        super().__init__(timeout=180)
        self.guild = guild
        self.bot = bot
        self._message: Optional[discord.Message] = None

    @discord.ui.button(label="➕ Add Admin", style=discord.ButtonStyle.green)
    async def add_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddAdminModal(self.guild, self.bot, self._message))

    @discord.ui.button(label="➖ Remove Admin", style=discord.ButtonStyle.red)
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemoveAdminModal(self.guild, self.bot, self._message))


class AddAdminModal(discord.ui.Modal, title="Add Admin"):
    user_input = discord.ui.TextInput(
        label="User ID",
        placeholder="123456789012345678  (right-click user -> Copy ID)",
        required=True,
    )

    def __init__(self, guild: discord.Guild, bot: commands.Bot, message: Optional[discord.Message]):
        super().__init__()
        self.guild = guild
        self.bot = bot
        self._message = message

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

        embed = discord.Embed(title="🔐 Admin Added", colour=COLOUR_PRIMARY)
        embed.add_field(name="New Admin", value=f"{member.mention} (`{uid}`)", inline=True)
        embed.add_field(name="Added By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        embed2, view = build_admins_embed_view(self.guild, self.bot)
        view._message = self._message
        await interaction.response.edit_message(embed=embed2, view=view)


class RemoveAdminModal(discord.ui.Modal, title="Remove Admin"):
    user_input = discord.ui.TextInput(
        label="User ID to remove",
        placeholder="123456789012345678",
        required=True,
    )

    def __init__(self, guild: discord.Guild, bot: commands.Bot, message: Optional[discord.Message]):
        super().__init__()
        self.guild = guild
        self.bot = bot
        self._message = message

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_input.value.strip().strip("<@!>")
        try:
            uid = int(raw)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Invalid ID"), ephemeral=True)
            return
        if uid == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("Can't remove yourself"), ephemeral=True
            )
            return
        member = self.guild.get_member(uid)
        member_name = member.display_name if member else f"User {uid}"
        remove_admin(uid)

        embed = discord.Embed(title="🔐 Admin Removed", colour=COLOUR_WARNING)
        embed.add_field(name="Removed", value=f"{member_name} (`{uid}`)", inline=True)
        embed.add_field(name="Removed By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        embed2, view = build_admins_embed_view(self.guild, self.bot)
        view._message = self._message
        await interaction.response.edit_message(embed=embed2, view=view)


def build_admins_embed_view(guild: discord.Guild, bot: commands.Bot) -> tuple:
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
    view = AdminsView(guild, bot)
    return embed, view


# ─── /settings ────────────────────────────────────────────────────────────────

def build_settings_embed() -> discord.Embed:
    s = get_all_settings()
    mv = "✅ ON" if s["manual_verification"] == "1" else "🟡 OFF (auto)"
    grace_val = s.get("grace_days", "0")
    grace = "OFF" if grace_val == "0" else f"{grace_val} day(s)"
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
    embed.add_field(name="📅 Challenge", value=f"{cs} -> {ce}", inline=False)
    embed.add_field(name="🏅 Elite Reward", value=s["elite_reward_text"] or "Not set", inline=False)
    return embed


class SettingsView(DisableOnTimeout):
    def __init__(self, guild: discord.Guild, bot: commands.Bot):
        super().__init__(timeout=300)
        self.guild = guild
        self.bot = bot
        self._message: Optional[discord.Message] = None
        self._refresh_toggle_styles()

    def _refresh_toggle_styles(self):
        mv = get_setting("manual_verification") == "1"
        self.toggle_verification.style = discord.ButtonStyle.red if mv else discord.ButtonStyle.green
        self.toggle_verification.label = "✅ Manual Verify: ON" if mv else "✅ Manual Verify: OFF"

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="📺 Set Fitness Channel...",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1, row=0,
    )
    async def fitness_channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        ch = select.values[0]
        set_setting("fitness_channel_id", str(ch.id))

        embed = discord.Embed(title="⚙️ Setting Changed", colour=COLOUR_PRIMARY)
        embed.add_field(name="Fitness Channel", value=f"<#{ch.id}>", inline=True)
        embed.add_field(name="Changed By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        await interaction.response.edit_message(embed=build_settings_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="🔔 Set Admin Channel...",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1, row=1,
    )
    async def admin_channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        ch = select.values[0]
        set_setting("admin_channel_id", str(ch.id))

        embed = discord.Embed(title="⚙️ Setting Changed", colour=COLOUR_PRIMARY)
        embed.add_field(name="Admin Channel", value=f"<#{ch.id}>", inline=True)
        embed.add_field(name="Changed By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        await interaction.response.edit_message(embed=build_settings_embed(), view=self)

    @discord.ui.button(label="🎯 Goal (days/wk)", style=discord.ButtonStyle.secondary, row=2)
    async def set_goal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            NumericSettingModal("goal_days_per_week", "Goal (days/week)", "e.g. 4", self._message, self.bot)
        )

    @discord.ui.button(label="🏆 Elite Goal", style=discord.ButtonStyle.secondary, row=2)
    async def set_elite(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            NumericSettingModal("elite_days_per_week", "Elite Goal (days/week)", "e.g. 5.5", self._message, self.bot)
        )

    @discord.ui.button(label="🕐 Grace Days (0-7)", style=discord.ButtonStyle.secondary, row=2)
    async def set_grace(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GraceDaysModal(self._message, self.bot))

    @discord.ui.button(label="✅ Manual Verify: OFF", style=discord.ButtonStyle.green, row=3)
    async def toggle_verification(self, interaction: discord.Interaction, button: discord.ui.Button):
        cur = get_setting("manual_verification")
        new = "0" if cur == "1" else "1"
        set_setting("manual_verification", new)
        self._refresh_toggle_styles()

        embed = discord.Embed(title="⚙️ Setting Changed", colour=COLOUR_PRIMARY)
        embed.add_field(name="Manual Verification", value="✅ ON" if new == "1" else "🟡 OFF (auto)", inline=True)
        embed.add_field(name="Changed By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        await interaction.response.edit_message(embed=build_settings_embed(), view=self)

    @discord.ui.button(label="📅 Challenge Dates", style=discord.ButtonStyle.secondary, row=3)
    async def set_dates(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChallengeDatesModal(self._message, self.bot))

    @discord.ui.button(label="🌎 Timezone", style=discord.ButtonStyle.secondary, row=4)
    async def set_tz(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            TextSettingModal("timezone", "Timezone", "e.g. US/Eastern", self._message, self.bot)
        )

    @discord.ui.button(label="🏅 Elite Reward", style=discord.ButtonStyle.secondary, row=4)
    async def set_elite_reward(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            TextSettingModal("elite_reward_text", "Elite Reward Description", "Describe the reward...", self._message, self.bot)
        )


class NumericSettingModal(discord.ui.Modal):
    value_input = discord.ui.TextInput(label="Value", required=True, max_length=10)

    def __init__(self, setting_key: str, title: str, placeholder: str, message, bot: commands.Bot):
        super().__init__(title=title[:45])
        self.setting_key = setting_key
        self.value_input.placeholder = placeholder
        self._message = message
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value_input.value.strip()
        try:
            float(raw)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Invalid value", "Please enter a number."), ephemeral=True
            )
            return
        set_setting(self.setting_key, raw)

        embed = discord.Embed(title="⚙️ Setting Changed", colour=COLOUR_PRIMARY)
        embed.add_field(name=self.setting_key, value=raw, inline=True)
        embed.add_field(name="Changed By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        await interaction.response.edit_message(embed=build_settings_embed())


class GraceDaysModal(discord.ui.Modal, title="Set Grace Days (0-7)"):
    value_input = discord.ui.TextInput(
        label="Grace Days",
        placeholder="0 = off, 1-7 = gap days allowed without breaking streak",
        required=True, max_length=1,
    )

    def __init__(self, message, bot: commands.Bot):
        super().__init__()
        self._message = message
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.value_input.value.strip()
        try:
            val = int(raw)
            if not (0 <= val <= 7):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Invalid value", "Grace days must be a whole number from 0 to 7."),
                ephemeral=True,
            )
            return
        set_setting("grace_days", str(val))

        embed = discord.Embed(title="⚙️ Setting Changed", colour=COLOUR_PRIMARY)
        embed.add_field(name="Grace Days", value=str(val), inline=True)
        embed.add_field(name="Changed By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        await interaction.response.edit_message(embed=build_settings_embed())


class TextSettingModal(discord.ui.Modal):
    value_input = discord.ui.TextInput(label="Value", required=True, max_length=200)

    def __init__(self, setting_key: str, title: str, placeholder: str, message, bot: commands.Bot):
        super().__init__(title=title[:45])
        self.setting_key = setting_key
        self.value_input.placeholder = placeholder
        self._message = message
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        set_setting(self.setting_key, self.value_input.value.strip())

        embed = discord.Embed(title="⚙️ Setting Changed", colour=COLOUR_PRIMARY)
        embed.add_field(name=self.setting_key, value=self.value_input.value.strip()[:200], inline=True)
        embed.add_field(name="Changed By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        await interaction.response.edit_message(embed=build_settings_embed())


class ChallengeDatesModal(discord.ui.Modal, title="Set Challenge Dates"):
    start = discord.ui.TextInput(label="Start Date (YYYY-MM-DD)", placeholder="2025-01-06", required=True)
    end   = discord.ui.TextInput(label="End Date (YYYY-MM-DD)", placeholder="2025-03-30", required=True)

    def __init__(self, message, bot: commands.Bot):
        super().__init__()
        self._message = message
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        try:
            date.fromisoformat(self.start.value.strip())
            date.fromisoformat(self.end.value.strip())
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Invalid date format", "Use YYYY-MM-DD."), ephemeral=True
            )
            return
        set_setting("challenge_start", self.start.value.strip())
        set_setting("challenge_end", self.end.value.strip())

        embed = discord.Embed(title="⚙️ Challenge Dates Updated", colour=COLOUR_PRIMARY)
        embed.add_field(name="Start", value=self.start.value.strip(), inline=True)
        embed.add_field(name="End", value=self.end.value.strip(), inline=True)
        embed.add_field(name="Changed By", value=interaction.user.mention, inline=False)
        await _post_admin_log(self.bot, embed)

        await interaction.response.edit_message(embed=build_settings_embed())


# ─── /members ────────────────────────────────────────────────────────────────

def build_members_embed(guild: discord.Guild) -> discord.Embed:
    with get_conn() as conn:
        active = conn.execute("SELECT * FROM members WHERE is_active=1 ORDER BY username").fetchall()
        inactive = conn.execute("SELECT * FROM members WHERE is_active=0 ORDER BY username").fetchall()

    embed = base_embed("👥 Challenge Members")
    if active:
        lines = [f"• **{r['username']}** (`{r['user_id']}`)" for r in active]
        embed.add_field(name=f"Active ({len(active)})", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Active", value="No active members yet.", inline=False)
    if inactive:
        lines = [f"• ~~{r['username']}~~ (`{r['user_id']}`)" for r in inactive]
        embed.add_field(name=f"Inactive ({len(inactive)})", value="\n".join(lines), inline=False)
    return embed


class MembersView(DisableOnTimeout):
    def __init__(self, guild: discord.Guild, bot: commands.Bot):
        super().__init__(timeout=180)
        self.guild = guild
        self.bot = bot
        self._message: Optional[discord.Message] = None

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="➕ Add member to challenge...",
        min_values=1, max_values=1, row=0,
    )
    async def add_member_select(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        member = select.values[0]
        upsert_member(member.id, member.display_name)
        set_member_active(member.id, True)

        embed = discord.Embed(title="👥 Member Added", colour=COLOUR_PRIMARY)
        embed.add_field(name="Member", value=f"{member.mention} ({member.display_name})", inline=True)
        embed.add_field(name="Added By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        view = MembersView(self.guild, self.bot)
        view._message = self._message
        await interaction.response.edit_message(embed=build_members_embed(self.guild), view=view)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="🚫 Make member inactive...",
        min_values=1, max_values=1, row=1,
    )
    async def deactivate_member_select(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        member = select.values[0]
        set_member_active(member.id, False)

        embed = discord.Embed(title="🚫 Member Deactivated", colour=COLOUR_WARNING)
        embed.add_field(name="Member", value=f"{member.mention} ({member.display_name})", inline=True)
        embed.add_field(name="By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        view = MembersView(self.guild, self.bot)
        view._message = self._message
        await interaction.response.edit_message(embed=build_members_embed(self.guild), view=view)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="✅ Reactivate member...",
        min_values=1, max_values=1, row=2,
    )
    async def reactivate_member_select(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        member = select.values[0]
        upsert_member(member.id, member.display_name)
        set_member_active(member.id, True)

        embed = discord.Embed(title="✅ Member Reactivated", colour=COLOUR_PRIMARY)
        embed.add_field(name="Member", value=f"{member.mention} ({member.display_name})", inline=True)
        embed.add_field(name="By", value=interaction.user.mention, inline=True)
        await _post_admin_log(self.bot, embed)

        view = MembersView(self.guild, self.bot)
        view._message = self._message
        await interaction.response.edit_message(embed=build_members_embed(self.guild), view=view)


# ─── Cog ─────────────────────────────────────────────────────────────────────

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="admins", description="Manage bot admins.")
    @is_bot_admin()
    async def admins_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed, view = build_admins_embed_view(interaction.guild, self.bot)
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
        view._message = msg

    @app_commands.command(name="settings", description="Configure the fitness challenge bot.")
    @is_bot_admin()
    async def settings_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = build_settings_embed()
        view = SettingsView(interaction.guild, self.bot)
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
        view._message = msg

    @app_commands.command(name="members", description="View and manage challenge members.")
    @is_bot_admin()
    async def members_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = build_members_embed(interaction.guild)
        view = MembersView(interaction.guild, self.bot)
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
        view._message = msg

    # ── /streaks ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="streaks",
        description="[Admin] View best streaks and total active days for all members.",
    )
    @is_bot_admin()
    async def streaks_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        from bot.utils.streak_utils import compute_daily_streak, compute_weekly_streak

        with get_conn() as conn:
            active_rows = conn.execute(
                "SELECT * FROM members WHERE is_active=1 ORDER BY username"
            ).fetchall()
            inactive_rows = conn.execute(
                "SELECT * FROM members WHERE is_active=0 ORDER BY username"
            ).fetchall()

        def _member_stats(rows):
            results = []
            for r in rows:
                uid = r["user_id"]
                total = get_total_activity_count(uid)
                _, best_daily = compute_daily_streak(uid)
                _, best_weekly = compute_weekly_streak(uid)
                results.append({
                    "username": r["username"],
                    "user_id": uid,
                    "total": total,
                    "best_daily": best_daily,
                    "best_weekly": best_weekly,
                })
            results.sort(key=lambda x: x["total"], reverse=True)
            return results

        active_stats = _member_stats(active_rows)
        inactive_stats = _member_stats(inactive_rows)

        combined_total = sum(s["total"] for s in active_stats)

        embed = discord.Embed(title="🏆 Member Streaks & Activity", colour=0x5865F2)

        if active_stats:
            lines = []
            for s in active_stats:
                lines.append(
                    f"**{s['username']}** — "
                    f"{s['total']} days total  |  "
                    f"🔥 Best daily: {s['best_daily']}  |  "
                    f"📅 Best weekly: {s['best_weekly']} wks"
                )
            embed.add_field(
                name=f"Active Members ({len(active_stats)})",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(name="Active Members", value="None.", inline=False)

        embed.add_field(
            name="📊 Combined Active Days",
            value=f"**{combined_total}** total days logged by all active members",
            inline=False,
        )

        if inactive_stats:
            lines = []
            for s in inactive_stats:
                lines.append(
                    f"~~{s['username']}~~ — "
                    f"{s['total']} days  |  "
                    f"🔥 {s['best_daily']}  |  "
                    f"📅 {s['best_weekly']} wks"
                )
            embed.add_field(
                name=f"Inactive Members ({len(inactive_stats)})",
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(text="Sorted by total active days. Best streaks are all-time highs.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="🔒 You don't have permission to use this command.",
                        colour=0xED4245,
                    ),
                    ephemeral=True,
                )
        else:
            logger.exception("Admin cog error: %s", error)
            await _post_admin_error(self.bot, "Admin Cog Error", str(error))


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))