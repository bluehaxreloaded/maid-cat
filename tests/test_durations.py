from datetime import datetime, timedelta, timezone

import pytest

from moderation import _format_account_age, _format_timeout_duration, parse_duration


@pytest.mark.parametrize(
    "text, expected",
    [
        ("30m", timedelta(minutes=30)),
        ("12h", timedelta(hours=12)),
        ("7d", timedelta(days=7)),
        ("2w", timedelta(weeks=2)),
        ("1mo", timedelta(days=30)),
        ("1d12h", timedelta(days=1, hours=12)),
        ("1 day 12 hours", timedelta(days=1, hours=12)),
        (" 3 Days ", timedelta(days=3)),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", [None, "", "perm", "Permanent", "forever"])
def test_parse_duration_permanent(text):
    assert parse_duration(text) is None


@pytest.mark.parametrize("text", ["spam", "5", "5x", "0d", "1d and stuff", "d5"])
def test_parse_duration_not_a_length(text):
    assert parse_duration(text) is False


def test_parse_duration_caps_at_ten_years():
    assert parse_duration("500y") == timedelta(days=3650)


def test_timeout_duration_rounds_up():
    until = datetime.now(timezone.utc) + timedelta(hours=1, minutes=4, seconds=30)
    assert _format_timeout_duration(until) == "1 hour, 5 minutes"


def test_timeout_duration_expired():
    assert _format_timeout_duration(datetime.now(timezone.utc) - timedelta(seconds=1)) == "expired"


def test_account_age():
    created = datetime.now(timezone.utc) - timedelta(days=365 * 2 + 30 + 3)
    assert _format_account_age(created) == "2 years, 1 month, 3 days"
    assert _format_account_age(datetime.now(timezone.utc)) == "0 days"
