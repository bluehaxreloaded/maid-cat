"""Case notes are kept in the database (cached in channel_state), with the topic only as a fallback."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import channel_state
import db
import helpee
from helpee import CASE_NOTES_HEADER, add_case_note, case_notes, reset_case_setup, topic_case_notes


@pytest.fixture(autouse=True)
def empty_state():
    stores = (channel_state._helpees, channel_state._notes, channel_state._setup_done, channel_state._delete_at)
    for store in stores:
        store.clear()
    yield
    for store in stores:
        store.clear()


def channel(channel_id=10, topic=None):
    return SimpleNamespace(id=channel_id, topic=topic, name="test-soap")


def test_topic_case_notes():
    topic = f"SOAP for <@1>\n\n{CASE_NOTES_HEADER}\n- first\n- second"
    assert topic_case_notes(topic) == ["first", "second"]
    assert topic_case_notes("SOAP for <@1>") == []
    assert topic_case_notes(None) == []


def test_saved_notes_win_over_the_topic():
    ch = channel(topic=f"SOAP for <@1>\n\n{CASE_NOTES_HEADER}\n- edited by someone")
    channel_state.remember(ch.id, 1)
    add_case_note(ch, "Reported   error code `022-2812`")
    assert case_notes(ch) == ["Reported error code `022-2812`"]


def test_topic_notes_are_the_fallback():
    ch = channel(topic=f"SOAP for <@1>\n\n{CASE_NOTES_HEADER}\n- from the topic")
    assert case_notes(ch) == ["from the topic"]


def test_notes_are_not_saved_twice_or_outside_helpee_channels():
    ch = channel()
    add_case_note(ch, "no helpee here")
    assert case_notes(ch) == []
    channel_state.remember(ch.id, 1)
    add_case_note(ch, "same")
    add_case_note(ch, "same")
    assert case_notes(ch) == ["same"]


def test_setup_complete_and_reset():
    ch = channel()
    channel_state.remember(ch.id, 1)
    helpee.complete_case_setup(ch)
    assert channel_state.setup_complete(ch.id)
    reset_case_setup(ch)
    assert not channel_state.setup_complete(ch.id)


def test_load_merges_with_notes_saved_before_it_finished(monkeypatch):
    async def fake_fetchall(sql, params=None):
        return [{"channel_id": 10, "user_id": 1, "status": "open", "case_notes": "saved\nalso saved",
                 "case_setup_complete": 1, "delete_at": None}]

    monkeypatch.setattr(db, "fetchall", fake_fetchall)
    channel_state._notes[10] = ["new", "saved"]
    channel_state._helpees[10] = 2
    asyncio.run(channel_state.load())
    assert channel_state.case_notes(10) == ["saved", "also saved", "new"]
    assert channel_state.helpee_id(channel(10)) == 2
    assert channel_state.setup_complete(10)


def test_old_notes_file_is_moved(tmp_path, monkeypatch):
    old = tmp_path / "case_notes.json"
    old.write_text(json.dumps({"10": {"notes": ["pending"], "complete": True}, "11": {"notes": [], "complete": False}}))
    monkeypatch.setattr(helpee, "OLD_NOTES_FILE", old)
    channel_state._notes[10] = ["already in the topic"]
    helpee.import_old_notes_file()
    assert channel_state.case_notes(10) == ["already in the topic", "pending"]
    assert channel_state.setup_complete(10)
    assert not channel_state.setup_complete(11)
    assert not old.exists()
    assert (tmp_path / "case_notes.json.imported").exists()
