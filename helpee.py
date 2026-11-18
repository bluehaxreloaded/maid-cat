import discord
import asyncio
import json
import time
from pathlib import Path
import channel_state
from constants import (
    HELPEE_ROLE_ID,
    SOAP_CHANNEL_CATEGORY_ID,
    NNID_CHANNEL_CATEGORY_ID,
    TEMP_ARCHIVE_CATEGORY_ID,
)


def channel_name_for(user: discord.abc.User, suffix: str) -> str:
    """Name used for a user's SOAP/NNID channel."""
    # strip leading/trailing periods and then replace remaining periods with dashes
    return user.name.lstrip(".").rstrip(".").lower().replace(".", "-") + suffix


def helpee_id(channel: discord.abc.GuildChannel | None) -> int | None:
    """ID of the helpee a SOAP/NNID channel is for, from the database (the topic only if it isn't there)."""
    return channel_state.helpee_id(channel)


def _topic_mentions(channel: discord.TextChannel, user_id: int) -> bool:
    return helpee_id(channel) == user_id


def find_open_channel(
    guild: discord.Guild, user: discord.abc.User, category_ids: list[int], suffix: str
) -> discord.TextChannel | None:
    """Find a user's open SOAP/NNID channel by its helpee, falling back to its name."""
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


REOPENED_TITLE = "🔄 Channel Reopened"  # also marks where the inactivity timer starts over


async def restore_helpee_access(
    channel: discord.TextChannel, member: discord.Member, moved: dict[int, int] | None = None
):
    """Give a helpee access to their open channel again, e.g. after they left and rejoined the server.
    moved: channels just moved to another category (see sync_helpee_role)."""
    if not isinstance(member, discord.Member) or not _topic_mentions(channel, member.id):
        return  # only the channel's own helpee gets access
    try:
        if not channel.permissions_for(member).read_messages:
            await channel.set_permissions(member, read_messages=True)
    except Exception:
        pass
    await sync_helpee_role(member, moved=moved)


async def member_from_topic(channel: discord.TextChannel) -> discord.Member | None:
    """Get the helpee of a SOAP/NNID channel (from the database, the topic only as a fallback)."""
    uid = helpee_id(channel)
    if not uid:
        return None
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


# Case notes: user-caused errors, saved in the database and shown in the channel topic for Soapers.
# They only go in the topic once step 1 and step 2 are done, so the topic is edited once instead of on every
# mistake. Discord allows about 2 topic/name edits per 10 minutes per channel, so later notes are written at
# most once per 10 minutes, keeping one edit free for .boom. Notes not in the topic yet when a channel is
# archived go into the archive's own topic edit.
CASE_NOTES_HEADER = "Case Notes:"
TOPIC_LIMIT = 1024
OLD_NOTES_FILE = Path(__file__).parent / "case_notes.json"  # where notes were kept before the database
NOTE_EDIT_SPACING = 600  # seconds between case note edits in one channel
_note_locks: dict[int, asyncio.Lock] = {}
_last_note_edit: dict[int, float] = {}
_note_tasks: set[asyncio.Task] = set()  # keeps background writes alive until they finish


def _is_helpee_channel(channel) -> bool:
    return helpee_id(channel) is not None


def _schedule_write(channel: discord.TextChannel):
    task = asyncio.create_task(_write_case_notes(channel))
    _note_tasks.add(task)
    task.add_done_callback(_note_tasks.discard)


def add_case_note(channel: discord.TextChannel | None, note: str):
    """Record a user-caused error in a SOAP/NNID channel's case notes."""
    if not _is_helpee_channel(channel):
        return  # only helpee channels have case notes
    note = " ".join(note.split())  # one line per note
    if channel_state.add_case_note(channel.id, note) and channel_state.setup_complete(channel.id):
        _schedule_write(channel)


def complete_case_setup(channel: discord.TextChannel | None):
    """Step 1 and step 2 are done, so show the case notes saved so far in the topic."""
    if not _is_helpee_channel(channel):
        return
    channel_state.set_setup_complete(channel.id, True)
    if channel_state.case_notes(channel.id):
        _schedule_write(channel)


def reset_case_setup(channel: discord.TextChannel | None):
    """The channel setup is starting over (.reset), so hold new notes until step 2 is done again."""
    if channel is not None:
        channel_state.set_setup_complete(channel.id, False)


def topic_case_notes(topic: str | None) -> list[str]:
    """Case notes shown in a topic."""
    topic = topic or ""
    if CASE_NOTES_HEADER not in topic:
        return []
    return [line[2:] for line in topic.split(CASE_NOTES_HEADER, 1)[1].split("\n") if line.startswith("- ")]


def case_notes(channel: discord.TextChannel) -> list[str]:
    """All case notes for a channel, from the database (the topic only if it has none saved)."""
    return channel_state.case_notes(channel.id) or topic_case_notes(channel.topic)


def import_old_notes_file():
    """Move notes from case_notes.json, where they were kept before the database, then rename the file."""
    if not OLD_NOTES_FILE.exists():
        return
    try:
        with open(OLD_NOTES_FILE, "r", encoding="utf-8") as f:
            old = json.load(f)
        for channel_id, entry in old.items():
            for note in entry.get("notes", []):
                channel_state.add_case_note(int(channel_id), note)
            if entry.get("complete"):
                channel_state.set_setup_complete(int(channel_id), True)
        OLD_NOTES_FILE.replace(OLD_NOTES_FILE.with_suffix(".json.imported"))
        print(f"Moved case notes for {len(old)} channels from {OLD_NOTES_FILE.name} to the database")
    except (OSError, ValueError, AttributeError) as e:
        print(f"Could not move case notes from {OLD_NOTES_FILE.name}: {e}")


def append_case_notes(topic: str | None, notes: list[str]) -> str:
    """Topic with the notes added under Case Notes (skipping duplicates, within Discord's topic limit)."""
    topic = (topic or "").rstrip()
    new_lines = [f"- {n}" for n in notes if f"- {n}" not in topic.split("\n")]
    if not new_lines:
        return topic
    if CASE_NOTES_HEADER not in topic:
        topic += f"\n\n{CASE_NOTES_HEADER}"
    for line in new_lines:
        if len(topic) + 1 + len(line) > TOPIC_LIMIT:
            break
        topic += f"\n{line}"
    return topic


def _topic_has_notes(topic: str | None, notes: list[str]) -> bool:
    return append_case_notes(topic, notes) == (topic or "").rstrip()


async def _write_case_notes(channel: discord.TextChannel):
    lock = _note_locks.setdefault(channel.id, asyncio.Lock())
    async with lock:
        last = _last_note_edit.get(channel.id)
        if last is not None:
            wait = last + NOTE_EDIT_SPACING - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        notes = channel_state.case_notes(channel.id)
        if not notes or _topic_has_notes(channel.topic, notes):
            return  # an earlier write already included them
        try:
            fresh = await channel.guild.fetch_channel(channel.id)
            if fresh.category and fresh.category.id == TEMP_ARCHIVE_CATEGORY_ID:
                return  # archiving writes them into its own topic edit
            if not _topic_has_notes(fresh.topic, notes):
                await fresh.edit(topic=append_case_notes(fresh.topic, notes))
                _last_note_edit[channel.id] = time.monotonic()
        except discord.HTTPException as e:
            print(f"Could not add case notes to #{channel.name}: {e}")


def safe_note_text(text: str, limit: int = 60) -> str:
    """User input shown inside a case note, without anything that would break the formatting."""
    text = " ".join(str(text).replace("`", "'").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
