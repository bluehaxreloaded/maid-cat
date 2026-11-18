from datetime import datetime, timedelta

from soap_automation import WAIT_MIN_SAMPLES, _typical_wait, _wait_words

NOON = datetime(2026, 1, 1, 12, 0)


def _waits(when: datetime, minutes: list[float]):
    return [(when, m * 60) for m in minutes]


def test_median_of_requests_near_this_time():
    waits = _waits(NOON, [10, 20, 30, 40, 50])
    assert _typical_wait(waits, NOON) == 30 * 60


def test_median_of_an_even_count():
    waits = _waits(NOON, [10, 20, 30, 40, 50, 60])
    assert _typical_wait(waits, NOON) == 35 * 60


def test_ignores_requests_at_other_times():
    waits = _waits(NOON, [10] * WAIT_MIN_SAMPLES) + _waits(NOON + timedelta(hours=6), [300] * 20)
    assert _typical_wait(waits, NOON) == 10 * 60


def test_wraps_around_midnight():
    waits = _waits(datetime(2026, 1, 1, 23, 30), [15] * WAIT_MIN_SAMPLES)
    assert _typical_wait(waits, datetime(2026, 1, 2, 0, 30)) == 15 * 60


def test_too_few_requests_gives_no_estimate():
    assert _typical_wait(_waits(NOON, [10] * (WAIT_MIN_SAMPLES - 1)), NOON) is None


def test_wait_words():
    assert _wait_words(60) == "a few minutes"
    assert _wait_words(5 * 60) == "about 5 minutes"
    assert _wait_words(22 * 60) == "about 20 minutes"
    assert _wait_words(23 * 60) == "about 25 minutes"
    assert _wait_words(60 * 60) == "about 1 hour"
    assert _wait_words(150 * 60) == "about 2 hours"
