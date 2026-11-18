"""Who each SOAP/NNID channel is for and its case notes, kept in the database instead of read back from the topic.

The topic still shows both for people to see, but the bot reads them from here. The channels table is
loaded into memory at startup so lookups never wait on the database, and every change is written to both.
The topic is only used for a channel the database doesn't know yet, or when the database is off.
"""

import asyncio
import re
import traceback

import db

MENTION_RE = re.compile(r"<@!?(\d+)>")

_helpees: dict[int, int] = {}  # channel ID -> helpee ID
_notes: dict[int, list[str]] = {}  # channel ID -> case notes, oldest first
_setup_done: set[int] = set()  # channels where step 1 and step 2 are done, so case notes go in the topic
_saves: set[asyncio.Task] = set()  # keeps background saves alive until they finish
_save_locks: dict[int, asyncio.Lock] = {}  # one save at a time per channel, so an older one can't land last
_loaded = False


async def load():
    """Fill the cache from the database. Run at startup, before channels are synced."""
    global _loaded
    rows = await db.fetchall(
        "SELECT channel_id, user_id, case_notes, case_setup_complete FROM channels WHERE status <> 'deleted'"
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


def _save(channel_id: int):
    if not db.enabled():
        return
    task = asyncio.create_task(_write(channel_id))
    _saves.add(task)
    task.add_done_callback(_saves.discard)


async def _write(channel_id: int):
    # If the row isn't there yet, saving the new channel writes these too
    try:
        async with _save_locks.setdefault(channel_id, asyncio.Lock()):
            await db.ensure_schema()
            await db.execute(
                "UPDATE channels SET case_notes = %s, case_setup_complete = %s WHERE channel_id = %s",
                ("\n".join(_notes.get(channel_id, [])) or None, channel_id in _setup_done, channel_id),
            )
    except Exception:
        print(f"Could not save case notes for channel {channel_id}:")
        traceback.print_exc()
