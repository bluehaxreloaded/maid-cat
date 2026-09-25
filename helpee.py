import discord
import re
from constants import (
    HELPEE_ROLE_ID,
    SOAP_CHANNEL_CATEGORY_ID,
    NNID_CHANNEL_CATEGORY_ID,
)


def channel_name_for(user: discord.abc.User, suffix: str) -> str:
    """Name used for a user's SOAP/NNID channel."""
    # strip leading/trailing periods and then replace remaining periods with dashes
    return user.name.lstrip(".").rstrip(".").lower().replace(".", "-") + suffix


def _topic_mentions(channel: discord.TextChannel, user_id: int) -> bool:
    return re.search(rf"<@!?{user_id}>", channel.topic or "") is not None


def find_open_channel(
    guild: discord.Guild, user: discord.abc.User, category_ids: list[int], suffix: str
) -> discord.TextChannel | None:
    """Find a user's open SOAP/NNID channel by the user ID in its topic, falling back to its name."""
    name = channel_name_for(user, suffix)
    by_name = None
    for channel in guild.text_channels:
        if not channel.category or channel.category.id not in category_ids:
            continue
        if _topic_mentions(channel, user.id):
            return channel
        if by_name is None and channel.name == name:
            by_name = channel
    return by_name


async def restore_helpee_access(channel: discord.TextChannel, member: discord.Member):
    """Give a helpee access to their open channel again, e.g. after they left and rejoined the server."""
    if not isinstance(member, discord.Member) or not _topic_mentions(channel, member.id):
        return  # only the helpee named in the channel topic gets access
    try:
        if not channel.permissions_for(member).read_messages:
            await channel.set_permissions(member, read_messages=True)
    except Exception:
        pass
    await sync_helpee_role(member)


async def member_from_topic(channel: discord.TextChannel) -> discord.Member | None:
    """Get the helpee named in a SOAP/NNID channel topic."""
    m = re.search(r"<@!?(\d+)>", channel.topic or "")
    if not m:
        return None
    uid = int(m.group(1))
    member = channel.guild.get_member(uid)
    if member is None:
        try:
            member = await channel.guild.fetch_member(uid)
        except discord.HTTPException:
            member = None
    return member


async def sync_helpee_role(member: discord.Member, moved: dict[int, int] | None = None):
    """Give the helpee role while the member has an open SOAP/NNID channel outside the manual category, otherwise remove it.
    Manual channels don't restrict the rest of the server.
    moved maps channel ID -> category ID for channels just moved, since the cache updates a moment later."""
    if not HELPEE_ROLE_ID or not isinstance(member, discord.Member):
        return
    role = member.guild.get_role(HELPEE_ROLE_ID)
    if not role:
        return
    moved = moved or {}

    def category_id(channel):
        if channel.id in moved:
            return moved[channel.id]
        return channel.category.id if channel.category else None

    should_have = any(
        category_id(channel) in (SOAP_CHANNEL_CATEGORY_ID, NNID_CHANNEL_CATEGORY_ID)
        and _topic_mentions(channel, member.id)
        for channel in member.guild.text_channels
    )
    try:
        if should_have and role not in member.roles:
            await member.add_roles(role)
        elif not should_have and role in member.roles:
            await member.remove_roles(role)
    except Exception:
        pass
