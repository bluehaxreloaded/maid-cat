"""When an archived channel gets deleted is kept in the database (cached in channel_state), not read from the topic."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import channel_state
import db

TOPIC = "Archived. Deletion scheduled: 2026-01-04 12:00:00 UTC. SOAP for <@1>"
FROM_TOPIC = datetime(2026, 1, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def empty_state():
    channel_state._delete_at.clear()
    yield
    channel_state._delete_at.clear()


def channel(channel_id=20, topic=None):
    return SimpleNamespace(id=channel_id, topic=topic)


def test_topic_is_the_fallback():
    assert channel_state.deletion_time(channel(topic=TOPIC)) == FROM_TOPIC
    assert channel_state.archived_at(channel(topic=TOPIC)) == FROM_TOPIC - timedelta(days=channel_state.ARCHIVE_DAYS)
    assert channel_state.deletion_time(channel(topic="SOAP for <@1>")) is None
    assert channel_state.deletion_time(None) is None


def test_saved_time_wins_over_an_edited_topic():
    started = channel_state.start_deletion_timer(20)
    expected = datetime.now(timezone.utc) + timedelta(days=channel_state.ARCHIVE_DAYS)
    assert abs((started - expected).total_seconds()) < 5
    assert channel_state.deletion_time(channel(topic=TOPIC)) == started
    assert channel_state.deletion_time(channel(topic="someone removed it")) == started


def test_cancel_and_adopt():
    channel_state.start_deletion_timer(20)
    channel_state.cancel_deletion_timer(20)
    assert channel_state.deletion_time(channel()) is None
    channel_state.adopt_deletion_time(20, FROM_TOPIC)
    assert channel_state.deletion_time(channel()) == FROM_TOPIC
    channel_state.adopt_deletion_time(20, FROM_TOPIC + timedelta(days=1))  # doesn't replace a saved time
    assert channel_state.deletion_time(channel()) == FROM_TOPIC


def test_load_only_takes_times_of_archived_channels(monkeypatch):
    saved = datetime(2026, 2, 1, 8, 30)  # naive UTC, as the database stores it

    async def fake_fetchall(sql, params=None):
        return [
            {"channel_id": 20, "user_id": 1, "status": "archived", "case_notes": None, "case_setup_complete": 0, "delete_at": saved},
            {"channel_id": 21, "user_id": 1, "status": "open", "case_notes": None, "case_setup_complete": 0, "delete_at": saved},
        ]

    monkeypatch.setattr(db, "fetchall", fake_fetchall)
    asyncio.run(channel_state.load())
    assert channel_state.deletion_time(channel(20, TOPIC)) == saved.replace(tzinfo=timezone.utc)
    assert channel_state.deletion_time(channel(21)) is None
