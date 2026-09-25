import discord
import re
from constants import HELPEE_ROLE_ID


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
    if HELPEE_ROLE_ID:
        try:
            role = member.guild.get_role(HELPEE_ROLE_ID)
            if role and role not in member.roles:
                await member.add_roles(role)
        except Exception:
            pass
