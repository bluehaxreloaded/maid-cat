"""Small helpers that decide what users and Staff see."""

from datetime import datetime, timezone
from types import SimpleNamespace

import channel_state
from helpee import CASE_NOTES_HEADER, TOPIC_LIMIT, append_case_notes, channel_name_for, helpee_id, safe_note_text
from records import limit_text, restricted_text, serial_base, utc
from status import _ago


def test_serial_base_drops_the_check_digit():
    assert serial_base("YJM123456784") == "YJM12345678"
    assert serial_base(" yjm12345678 ") == "YJM12345678"
    assert serial_base("not a serial") == "NOT A SERIAL"
    assert serial_base(None) is None


def test_utc_makes_naive_utc():
    aware = datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc).astimezone()
    assert utc(aware) == datetime(2026, 1, 1, 4, 0)
    assert utc(datetime(2026, 1, 1)) == datetime(2026, 1, 1)
    assert utc(None) is None


def test_restricted_text():
    ends = datetime(2026, 1, 1)
    timed = restricted_text("SOAP", {"expires_at": ends})
    assert f"<t:{int(ends.replace(tzinfo=timezone.utc).timestamp())}:R>" in timed
    assert "may be temporary or permanent" in restricted_text("NNID", {"expires_at": None})


def test_limit_text():
    until = datetime(2026, 1, 1)
    assert limit_text({"max": 1, "period": "week", "until": until}).startswith(
        "You can only receive 1 SOAP transfer per week."
    )
    assert "2 SOAP transfers per 30 days" in limit_text({"max": 2, "period": "month", "until": until})


def test_channel_name_for():
    assert channel_name_for(SimpleNamespace(name="..Some.User."), "-soap") == "some-user-soap"


def test_helpee_id_falls_back_to_the_topic():
    assert helpee_id(SimpleNamespace(id=1, topic="SOAP for <@123456>")) == 123456
    assert helpee_id(SimpleNamespace(id=1, topic="SOAP for <@!42>")) == 42
    assert helpee_id(SimpleNamespace(id=1, topic=None)) is None
    assert helpee_id(None) is None


def test_saved_helpee_wins_over_the_topic():
    channel = SimpleNamespace(id=555, topic="This is the SOAP channel for <@111>")
    channel_state.remember(555, 222)
    try:
        assert helpee_id(channel) == 222
        channel.topic = "someone edited the topic"
        assert helpee_id(channel) == 222
    finally:
        channel_state.forget(555)
    assert helpee_id(channel) is None


def test_append_case_notes():
    topic = append_case_notes("SOAP for <@1>", ["first", "second"])
    assert topic == f"SOAP for <@1>\n\n{CASE_NOTES_HEADER}\n- first\n- second"
    assert append_case_notes(topic, ["first"]) == topic  # no duplicates
    assert append_case_notes(topic, ["third"]).endswith("- second\n- third")


def test_append_case_notes_stays_within_the_topic_limit():
    topic = append_case_notes("x" * (TOPIC_LIMIT - 30), ["short", "a much longer note that won't fit"])
    assert len(topic) <= TOPIC_LIMIT
    assert "- short" in topic


def test_safe_note_text():
    assert safe_note_text("hi `there`\n  you") == "hi 'there' you"
    assert safe_note_text("x" * 100) == "x" * 57 + "..."


def test_ago():
    assert _ago(1) == "1 second"
    assert _ago(59) == "59 seconds"
    assert _ago(60) == "1 minute"
    assert _ago(3599) == "59 minutes"
    assert _ago(3600 * 2 + 60 * 5) == "2h 5m"
