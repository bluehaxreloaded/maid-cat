import discord
import asyncio
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


# Case notes: user-caused errors recorded in the channel topic for Soapers.
# Discord only allows about 2 topic edits per 10 minutes per channel, so notes are written in the
# background and any that pile up while waiting are merged into one edit.
CASE_NOTES_HEADER = "Case Notes:"
TOPIC_LIMIT = 1024
_pending_notes: dict[int, list[str]] = {}
_note_locks: dict[int, asyncio.Lock] = {}
_note_tasks: set[asyncio.Task] = set()  # keeps background writes alive until they finish


def add_case_note(channel: discord.TextChannel | None, note: str):
    """Record a user-caused error under Case Notes in a SOAP/NNID channel topic."""
    if channel is None or not re.search(r"<@!?\d+>", getattr(channel, "topic", None) or ""):
        return  # only helpee channels have case notes
    note = " ".join(note.split())  # one line per note
    _pending_notes.setdefault(channel.id, []).append(note)
    task = asyncio.create_task(_write_case_notes(channel))
    _note_tasks.add(task)
    task.add_done_callback(_note_tasks.discard)


async def _write_case_notes(channel: discord.TextChannel):
    lock = _note_locks.setdefault(channel.id, asyncio.Lock())
    async with lock:
        notes = _pending_notes.pop(channel.id, [])
        if not notes:
            return  # an earlier write already included them
        try:
            fresh = await channel.guild.fetch_channel(channel.id)
            topic = (fresh.topic or "").rstrip()
            if CASE_NOTES_HEADER not in topic:
                topic += f"\n\n{CASE_NOTES_HEADER}"
            new_topic = topic
            for note in notes:
                line = f"- {note}"
                if line in new_topic.split("\n"):
                    continue
                if len(new_topic) + 1 + len(line) > TOPIC_LIMIT:
                    break
                new_topic += f"\n{line}"
            if new_topic != topic:
                await fresh.edit(topic=new_topic)
        except discord.HTTPException as e:
            print(f"Could not add case note to #{channel.name}: {e}")


def safe_note_text(text: str, limit: int = 60) -> str:
    """User input shown inside a case note, without anything that would break the formatting."""
    text = " ".join(str(text).replace("`", "'").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
