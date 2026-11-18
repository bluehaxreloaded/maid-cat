"""Who each SOAP/NNID channel is for, its case notes and when it gets deleted after being archived,
kept in the database instead of read back from the channel topic.

The topic still shows both for people to see, but the bot reads them from here. The channels table is
loaded into memory at startup so lookups never wait on the database, and every change is written to both.
The topic is only used for a channel the database doesn't know yet, or when the database is off.
"""

import asyncio
import re
import traceback
from datetime import datetime, timedelta, timezone

import db

MENTION_RE = re.compile(r"<@!?(\d+)>")
ARCHIVE_DAYS = 3  # archived channels are deleted this long after being archived
ARCHIVE_DELETION_RE = re.compile(r"Archived\.\s*Deletion scheduled:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*UTC\.")

_helpees: dict[int, int] = {}  # channel ID -> helpee ID
_notes: dict[int, list[str]] = {}  # channel ID -> case notes, oldest first
_setup_done: set[int] = set()  # channels where step 1 and step 2 are done, so case notes go in the topic
_delete_at: dict[int, datetime] = {}  # archived channel ID -> when it gets deleted (UTC)
_saves: set[asyncio.Task] = set()  # keeps background saves alive until they finish
_save_locks: dict[int, asyncio.Lock] = {}  # one save at a time per channel, so an older one can't land last
_loaded = False


async def load():
    """Fill the cache from the database. Run at startup, before channels are synced."""
    global _loaded
    rows = await db.fetchall(
        "SELECT channel_id, user_id, status, case_notes, case_setup_complete, delete_at FROM channels WHERE status <> 'deleted'"
    )
    # Merged with anything saved since the bot started, in case that happened before this finished
    for r in rows or []:
        if r["user_id"]:
            _helpees.setdefault(r["channel_id"], r["user_id"])
        if r["case_notes"]:
            saved = r["case_notes"].split("\n")
            _notes[r["channel_id"]] = saved + [n for n in _notes.get(r["channel_id"], []) if n not in saved]
        if r["case_setup_complete"]:
            _setup_done.add(r["channel_id"])
        if r["status"] == "archived" and r["delete_at"]:
            _delete_at.setdefault(r["channel_id"], r["delete_at"].replace(tzinfo=timezone.utc))
    _loaded = True


async def ensure_loaded():
    """Load the cache if startup couldn't, so saving a channel never overwrites what the database has."""
    if not _loaded:
        await load()


def remember(channel_id: int, user_id: int | None):
    if user_id:
        _helpees[channel_id] = user_id


def forget(channel_id: int):
    _helpees.pop(channel_id, None)
    _notes.pop(channel_id, None)
    _setup_done.discard(channel_id)
    _delete_at.pop(channel_id, None)


def known(channel_id: int) -> bool:
    return channel_id in _helpees


def helpee_from_topic(channel) -> int | None:
    m = MENTION_RE.search(getattr(channel, "topic", None) or "")
    return int(m.group(1)) if m else None


def helpee_id(channel) -> int | None:
    """ID of the helpee a SOAP/NNID channel is for."""
    if channel is None:
        return None
    return _helpees.get(getattr(channel, "id", None)) or helpee_from_topic(channel)


def case_notes(channel_id: int) -> list[str]:
    return list(_notes.get(channel_id, []))


def add_case_note(channel_id: int, note: str) -> bool:
    """Save a case note. False if the channel already has it."""
    notes = _notes.setdefault(channel_id, [])
    if note in notes:
        return False
    notes.append(note)
    _save(channel_id)
    return True


def adopt_case_notes(channel_id: int, notes: list[str]):
    """Notes found in the topic of a channel the database had none for (the caller saves the channel)."""
    if notes and channel_id not in _notes:
        _notes[channel_id] = list(notes)


def setup_complete(channel_id: int) -> bool:
    return channel_id in _setup_done


def set_setup_complete(channel_id: int, done: bool):
    if done != (channel_id in _setup_done):
        (_setup_done.add if done else _setup_done.discard)(channel_id)
        _save(channel_id)


def deletion_from_topic(topic: str | None) -> datetime | None:
    match = ARCHIVE_DELETION_RE.search(topic or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def deletion_time(channel) -> datetime | None:
    """When an archived channel gets deleted, or None if its timer isn't set."""
    if channel is None:
        return None
    return _delete_at.get(getattr(channel, "id", None)) or deletion_from_topic(getattr(channel, "topic", None))


def archived_at(channel) -> datetime | None:
    deletion = deletion_time(channel)
    return deletion - timedelta(days=ARCHIVE_DAYS) if deletion else None


def start_deletion_timer(channel_id: int) -> datetime:
    """Archived now: save when the channel gets deleted."""
    _delete_at[channel_id] = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=ARCHIVE_DAYS)
    _save(channel_id, "archive")
    return _delete_at[channel_id]


def adopt_deletion_time(channel_id: int, deletion: datetime | None):
    """A deletion time found in the topic of a channel the database had none for (the caller saves the channel)."""
    if deletion and channel_id not in _delete_at:
        _delete_at[channel_id] = deletion


def cancel_deletion_timer(channel_id: int):
    """The channel left the archive, so it won't be deleted."""
    if _delete_at.pop(channel_id, None) is not None:
        _save(channel_id, "archive")


def _save(channel_id: int, part: str = "notes"):
    if not db.enabled():
        return
    task = asyncio.create_task(_write(channel_id, part))
    _saves.add(task)
    task.add_done_callback(_saves.discard)


def _naive(dt: datetime | None) -> datetime | None:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt else None


async def _write(channel_id: int, part: str):
    # If the row isn't there yet, saving the new channel writes these too
    try:
        async with _save_locks.setdefault(channel_id, asyncio.Lock()):
            await db.ensure_schema()
            if part == "archive":
                deletion = _delete_at.get(channel_id)
                await db.execute(
                    "UPDATE channels SET archived_at = %s, delete_at = %s WHERE channel_id = %s",
                    (_naive(deletion - timedelta(days=ARCHIVE_DAYS)) if deletion else None, _naive(deletion), channel_id),
                )
            else:
                await db.execute(
                    "UPDATE channels SET case_notes = %s, case_setup_complete = %s WHERE channel_id = %s",
                    ("\n".join(_notes.get(channel_id, [])) or None, channel_id in _setup_done, channel_id),
                )
    except Exception:
        print(f"Could not save the {part} details of channel {channel_id}:")
        traceback.print_exc()
