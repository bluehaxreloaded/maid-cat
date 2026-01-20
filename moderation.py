import discord
from datetime import datetime, timezone, timedelta
from discord.ext import commands
from constants import JOIN_LEAVE_LOG_ID


def _format_account_age(created_at: datetime) -> str:
    """Return a rough human-readable age like '6 years, 8 months, 23 days'."""
    now = datetime.now(timezone.utc)
    delta = now - created_at

    # Approximate months/years
    days = delta.days
    years, days = divmod(days, 365)
    months, days = divmod(days, 30)

    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if days or not parts:
        parts.append(f"{days} day{'s' if days != 1 else ''}")

    return ", ".join(parts)


def _format_pst_time() -> str:
    """Format current time in PST as 'Today at 4:04PM' or '12/7/2025 at 4:04PM'."""
    pst = timezone(timedelta(hours=-8))  # PST is UTC-8
    now_pst = datetime.now(pst)
    
    # Format time (12-hour format with AM/PM)
    hour = now_pst.hour
    minute = now_pst.minute
    period = "AM" if hour < 12 else "PM"
    hour_12 = hour if hour <= 12 else hour - 12
    if hour_12 == 0:
        hour_12 = 12
    time_str = f"{hour_12}:{minute:02d}{period}"
    
    return f"Today at {time_str}"


class ModerationCog(commands.Cog):
    """Basic moderation/utility events like join/leave logging."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_member_log(self, member: discord.Member, joined: bool):
        """Send a join/leave embed to the JOIN_LEAVE_LOG_ID channel."""
        guild = member.guild
        channel = guild.get_channel(JOIN_LEAVE_LOG_ID)
        if channel is None:
            return

        action = "Member Joined" if joined else "Member Left"
        color = discord.Color.green() if joined else discord.Color.red()

        embed = discord.Embed(color=color)
        embed.set_author(name=action, icon_url=member.display_avatar.url)

        # Mention + username
        embed.add_field(
            name="",
            value=f"{member.mention} {member}",
            inline=False,
        )

        # Account age
        age_str = _format_account_age(member.created_at)
        embed.add_field(name="Account Age", value=age_str, inline=False)

        # Footer with ID and timestamp (PST)
        timestamp = _format_pst_time()
        embed.set_footer(text=f"ID: {member.id} • {timestamp}")

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._send_member_log(member, joined=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._send_member_log(member, joined=False)


def setup(bot: commands.Bot):
    bot.add_cog(ModerationCog(bot))

