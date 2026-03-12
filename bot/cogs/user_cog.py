"""
user_cog.py — User-facing commands.

/help, /status, /updates, /photos
"""

import logging
from datetime import date, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import (
    get_setting, get_all_photos_of_week, get_activity_for_week,
    set_dm_updates, get_member, upsert_member, get_conn,
)
from bot.utils.time_utils import current_week_start, week_start_for, today_local, challenge_dates
from bot.utils.streak_utils import (
    compute_daily_streak, compute_weekly_streak,
    compute_weekly_average, get_user_tier,
)
from bot.utils.embed_utils import (
    base_embed, success_embed, error_embed, build_status_embed,
    COLOUR_PRIMARY, COLOUR_SUCCESS,
)

logger = logging.getLogger(__name__)


# ─── /photos gallery ─────────────────────────────────────────────────────────

class PhotosView(discord.ui.View):
    def __init__(self, photos: list, bot: commands.Bot, guild_id: int, page: int = 0):
        super().__init__(timeout=120)
        self.photos = photos  # list of DB rows, most recent first
        self.bot = bot
        self.guild_id = guild_id
        self.page = page
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= len(self.photos) - 1

    async def build_embed(self) -> discord.Embed:
        if not self.photos:
            return base_embed("📸 Photos of the Week", "No photos selected yet!")

        photo = self.photos[self.page]
        week_start = date.fromisoformat(photo["week_start"])
        week_end = week_start + timedelta(days=6)
        week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

        # Resolve winner name
        winner_name = f"User {photo['user_id']}"
        if self.guild_id:
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                member = guild.get_member(photo["user_id"])
                if member:
                    winner_name = member.display_name

        embed = discord.Embed(
            title=f"📸 Photo of the Week — {week_label}",
            colour=COLOUR_PRIMARY,
        )
        embed.add_field(name="🏅 Winner", value=winner_name, inline=True)
        embed.add_field(name="Reactions", value=str(photo["reaction_count"]), inline=True)
        embed.add_field(name="Week", value=week_label, inline=True)
        embed.set_footer(text=f"Week {self.page + 1} of {len(self.photos)}")

        # Only try to fetch image/jump URL if there's an actual message
        channel_id = photo["channel_id"]
        message_id = photo["message_id"]

        if channel_id and message_id:
            channel_id = int(channel_id)
            message_id = int(message_id)
            jump_url = f"https://discord.com/channels/{self.guild_id}/{channel_id}/{message_id}"
            try:
                ch = self.bot.get_channel(channel_id)
                if ch:
                    msg = await ch.fetch_message(message_id)
                    if msg.attachments:
                        embed.set_image(url=msg.attachments[0].url)
            except (discord.NotFound, discord.HTTPException):
                pass  # Image just won't show; jump link still useful
            embed.add_field(name="📎 Original Post", value=f"[Jump to photo]({jump_url})", inline=False)
        else:
            embed.description = f"**{winner_name}** was the standout member this week!"

        return embed

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(self.photos) - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.select(
        placeholder="Jump to week...",
        min_values=1,
        max_values=1,
        options=[discord.SelectOption(label="Loading...", value="0")],
    )
    async def week_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.page = int(select.values[0])
        self._update_buttons()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    def populate_select(self):
        options = []
        for i, p in enumerate(self.photos[:25]):
            ws = date.fromisoformat(p["week_start"])
            label = f"Week of {ws.strftime('%b %d, %Y')}"
            options.append(discord.SelectOption(label=label[:100], value=str(i)))
        self.week_select.options = options if options else [
            discord.SelectOption(label="No weeks yet", value="0")
        ]


class UserCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /help ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="help", description="Learn about the fitness challenge and bot commands.")
    async def help_cmd(self, interaction: discord.Interaction):
        from bot.database import is_admin

        goal = get_setting("goal_days_per_week") or "4"
        elite = get_setting("elite_days_per_week") or "5.5"
        elite_reward = get_setting("elite_reward_text") or "TBD by admins"
        manual_verify = get_setting("manual_verification") == "1"
        fitness_ch_id = get_setting("fitness_channel_id")
        fitness_ch = f"<#{fitness_ch_id}>" if fitness_ch_id else "the fitness channel"

        user_is_admin = (
            is_admin(interaction.user.id)
            or interaction.user.guild_permissions.administrator
        )

        embed = discord.Embed(
            title="🏃 Activity Challenge — Let's Get Moving",
            colour=COLOUR_PRIMARY,
        )

        embed.add_field(
            name="🏁 What's the Challenge?",
            value=(
                f"Stay active as many days as you can throughout the challenge. "
                f"Log your workouts, build your streak, and help the group hit the goal together.\n"
                f"• **Baseline goal:** {goal} days/week average\n"
                f"• **Elite goal:** {elite} days/week average\n"
                f"• Hit the goal by the end and you're invited to the **celebratory event** 🎉\n"
                f"• Ryan & Nathan are matching donations up to **$100 each**!"
            ),
            inline=False,
        )

        verify_note = (
            "An admin will react ✅ to your photo before it counts — usually quick!"
            if manual_verify
            else "Your photo is counted automatically — no waiting needed."
        )
        embed.add_field(
            name="📸 How to Log Activity",
            value=(
                f"Post **any activity photo** in {fitness_ch} — a walk, a run, a gym session, anything that gets you moving.\n"
                f"• One photo per day earns credit — multiple posts won't stack.\n"
                f"• Honor system: if you got out and moved, it counts.\n"
                f"• {verify_note}"
            ),
            inline=False,
        )

        embed.add_field(
            name="🏅 Photo of the Week",
            value=(
                "Every Monday, the most-reacted activity photo from the previous week gets crowned **Photo of the Week**. "
                "React to your favorites — it matters."
            ),
            inline=False,
        )

        embed.add_field(
            name="📟 Your Commands",
            value=(
                "`/help` — This message\n"
                "`/status` — Your tier, streak, and weekly average\n"
                "`/updates` — Manage your weekly DM summaries\n"
                "`/photos` — Browse the Photo of the Week gallery"
            ),
            inline=False,
        )

        if user_is_admin:
            embed.add_field(
                name="🔐 Admin Commands",
                value=(
                    "`/admins` — Manage bot admins\n"
                    "`/settings` — Configure the bot\n"
                    "`/members` — Add/deactivate members\n"
                    "`/addactivity` — Manually credit a user\n"
                    "`/removeactivity` — Remove a credit"
                ),
                inline=False,
            )

        if self.bot.debug_mode:
            embed.add_field(
                name="🛠️ Debug Commands",
                value=(
                    "`/nextday` — Advance bot's date by 1 day\n"
                    "`/resetday` — Reset bot date back to real today\n"
                    "`/endweek` — Trigger weekly announcement + DMs now\n"
                    "`/showsummary` — Post current week heatmap here\n"
                    "`/triggersunday` — Trigger full Monday wrap-up now"
                ),
                inline=False,
            )
            embed.set_footer(text=f"⚠️ DEBUG MODE ACTIVE | Elite Reward: {elite_reward}")
        else:
            embed.set_footer(text=f"Elite Reward: {elite_reward}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /status ───────────────────────────────────────────────────────────────

    @app_commands.command(name="status", description="View your current challenge status.")
    async def status_cmd(self, interaction: discord.Interaction):
        user = interaction.user
        upsert_member(user.id, user.display_name)

        member_row = get_member(user.id)
        if member_row and not member_row["is_active"]:
            await interaction.response.send_message(
                embed=error_embed("Inactive Member", "You're currently marked as inactive in the challenge."),
                ephemeral=True,
            )
            return

        goal = float(get_setting("goal_days_per_week") or 4)
        elite_goal = float(get_setting("elite_days_per_week") or 5.5)

        avg = compute_weekly_average(user.id)
        tier = get_user_tier(user.id)
        daily_streak, best_daily = compute_daily_streak(user.id)
        weekly_streak, best_weekly = compute_weekly_streak(user.id)

        week_start = current_week_start()
        this_week_rows = get_activity_for_week(user.id, week_start)
        this_week_count = len(this_week_rows)

        embed = build_status_embed(
            member=user,
            tier=tier,
            daily_streak=daily_streak,
            best_daily=best_daily,
            weekly_streak=weekly_streak,
            best_weekly=best_weekly,
            avg=avg,
            this_week_count=this_week_count,
            goal=goal,
            elite_goal=elite_goal,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /updates ──────────────────────────────────────────────────────────────

    @app_commands.command(name="updates", description="Manage your weekly DM summary updates.")
    async def updates_cmd(self, interaction: discord.Interaction):
        upsert_member(interaction.user.id, interaction.user.display_name)
        member_row = get_member(interaction.user.id)
        currently_on = bool(member_row["dm_updates"]) if member_row else True

        view = UpdatesToggleView(interaction.user.id, currently_on)
        embed = build_updates_embed(currently_on)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ── /photos ───────────────────────────────────────────────────────────────

    @app_commands.command(name="photos", description="Browse the Photo of the Week gallery.")
    async def photos_cmd(self, interaction: discord.Interaction):
        photos = get_all_photos_of_week()
        if not photos:
            await interaction.response.send_message(
                embed=base_embed("📸 Photos of the Week", "No photos have been selected yet — keep posting! 📷"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        view = PhotosView(photos, bot=self.bot, guild_id=interaction.guild_id, page=0)
        view.populate_select()
        embed = await view.build_embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def build_updates_embed(enabled: bool) -> discord.Embed:
    status = "✅ **ON**" if enabled else "🔕 **OFF**"
    toggle_hint = "Use the button below to turn them off." if enabled else "Use the button below to turn them back on."
    embed = base_embed(
        "📬 Weekly DM Updates",
        f"Your weekly summaries are currently: {status}\n\n"
        "Every Monday you'll get a personal DM with:\n"
        "• Your activity count for the week\n"
        "• Daily & weekly streaks\n"
        "• Your tier, average, and total days logged\n"
        "• A chart of your activity across the challenge\n\n"
        f"{toggle_hint}",
    )
    return embed


class UpdatesToggleView(discord.ui.View):
    def __init__(self, user_id: int, enabled: bool):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.enabled = enabled
        self._update_button()

    def _update_button(self):
        self.toggle_btn.label = "Turn OFF" if self.enabled else "Turn ON"
        self.toggle_btn.style = (
            discord.ButtonStyle.red if self.enabled else discord.ButtonStyle.green
        )

    @discord.ui.button(label="Toggle", style=discord.ButtonStyle.primary)
    async def toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.enabled = not self.enabled
        set_dm_updates(self.user_id, self.enabled)
        self._update_button()
        embed = build_updates_embed(self.enabled)
        await interaction.response.edit_message(embed=embed, view=self)


async def setup(bot: commands.Bot):
    await bot.add_cog(UserCog(bot))