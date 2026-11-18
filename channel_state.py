"""Who each SOAP/NNID channel is for, kept in the database instead of read back from the channel topic.

The topic still names the helpee for people to see, but the bot reads it from here. The channels table is
loaded into memory at startup so lookups never wait on the database, and every change is written to both.
The topic is only used for a channel the database doesn't know yet, or when the database is off.
"""

import re

import db

MENTION_RE = re.compile(r"<@!?(\d+)>")

_helpees: dict[int, int] = {}  # channel ID -> helpee ID


async def load():
    """Fill the cache from the database. Run at startup, before channels are synced."""
    rows = await db.fetchall(
        "SELECT channel_id, user_id FROM channels WHERE status <> 'deleted' AND user_id IS NOT NULL"
    )
    _helpees.clear()
    _helpees.update({r["channel_id"]: r["user_id"] for r in rows or []})


def remember(channel_id: int, user_id: int | None):
    if user_id:
        _helpees[channel_id] = user_id


def forget(channel_id: int):
    _helpees.pop(channel_id, None)


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
