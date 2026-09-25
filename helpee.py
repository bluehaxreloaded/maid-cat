import discord
import asyncio
import json
import re
import time
from pathlib import Path
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
# Notes are saved to case_notes.json and only written once step 1 and step 2 are done, so the topic is
# edited once instead of on every mistake. Discord allows about 2 topic/name edits per 10 minutes per
# channel, so later notes are written at most once per 10 minutes, keeping one edit free for .boom.
# Notes still pending when a channel is archived go into the archive's own topic edit.
CASE_NOTES_HEADER = "Case Notes:"
TOPIC_LIMIT = 1024
NOTES_FILE = Path(__file__).parent / "case_notes.json"
NOTE_EDIT_SPACING = 600  # seconds between case note edits in one channel
_notes_data: dict[str, dict] | None = None  # {channel_id: {"notes": [...], "complete": bool}}
_note_locks: dict[int, asyncio.Lock] = {}
_last_note_edit: dict[int, float] = {}
_note_tasks: set[asyncio.Task] = set()  # keeps background writes alive until they finish


def _is_helpee_channel(channel) -> bool:
    return channel is not None and re.search(r"<@!?\d+>", getattr(channel, "topic", None) or "") is not None


def _notes() -> dict[str, dict]:
    global _notes_data
    if _notes_data is None:
        try:
            with open(NOTES_FILE, "r", encoding="utf-8") as f:
                _notes_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _notes_data = {}
    return _notes_data


def _save_notes():
    try:
        tmp = NOTES_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_notes(), f)
        tmp.replace(NOTES_FILE)
    except OSError as e:
        print(f"Could not save case notes: {e}")


def _schedule_write(channel: discord.TextChannel):
    task = asyncio.create_task(_write_case_notes(channel))
    _note_tasks.add(task)
    task.add_done_callback(_note_tasks.discard)


def add_case_note(channel: discord.TextChannel | None, note: str):
    """Record a user-caused error under Case Notes in a SOAP/NNID channel topic."""
    if not _is_helpee_channel(channel):
        return  # only helpee channels have case notes
    entry = _notes().setdefault(str(channel.id), {"notes": [], "complete": False})
    note = " ".join(note.split())  # one line per note
    if note not in entry["notes"]:
        entry["notes"].append(note)
        _save_notes()
    if entry["complete"]:
        _schedule_write(channel)


def complete_case_setup(channel: discord.TextChannel | None):
    """Step 1 and step 2 are done, so write any case notes saved so far."""
    if not _is_helpee_channel(channel):
        return
    entry = _notes().setdefault(str(channel.id), {"notes": [], "complete": False})
    if not entry["complete"]:
        entry["complete"] = True
        _save_notes()
    if entry["notes"]:
        _schedule_write(channel)


def pending_case_notes(channel_id: int) -> list[str]:
    """Case notes saved for a channel but not written to its topic yet."""
    return list(_notes().get(str(channel_id), {}).get("notes", []))


def clear_case_notes(channel_id: int):
    """Forget a channel's case notes, e.g. once they're written into its archive topic."""
    if _notes().pop(str(channel_id), None) is not None:
        _save_notes()


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


async def _write_case_notes(channel: discord.TextChannel):
    lock = _note_locks.setdefault(channel.id, asyncio.Lock())
    async with lock:
        last = _last_note_edit.get(channel.id)
        if last is not None:
            wait = last + NOTE_EDIT_SPACING - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        notes = pending_case_notes(channel.id)
        if not notes:
            return  # an earlier write already included them
        try:
            fresh = await channel.guild.fetch_channel(channel.id)
            if fresh.category and fresh.category.id == TEMP_ARCHIVE_CATEGORY_ID:
                return  # archiving writes them into its own topic edit
            new_topic = append_case_notes(fresh.topic, notes)
            if new_topic != (fresh.topic or "").rstrip():
                await fresh.edit(topic=new_topic)
                _last_note_edit[channel.id] = time.monotonic()
            entry = _notes().get(str(channel.id))
            if entry:
                entry["notes"] = [n for n in entry["notes"] if n not in notes]  # keep ones added meanwhile
                _save_notes()
        except discord.HTTPException as e:
            print(f"Could not add case notes to #{channel.name}: {e}")


def safe_note_text(text: str, limit: int = 60) -> str:
    """User input shown inside a case note, without anything that would break the formatting."""
    text = " ".join(str(text).replace("`", "'").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
