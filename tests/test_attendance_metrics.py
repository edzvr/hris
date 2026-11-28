from datetime import date, datetime
from types import SimpleNamespace

from hris import attendance_record_metrics


def make_record(company, attendance_date, clock_in, clock_out):
    return SimpleNamespace(
        company=company,
        date=attendance_date,
        clock_in=clock_in,
        clock_out=clock_out,
    )


def test_eight_eleven_is_late_but_on_time_clock_out_has_no_undertime():
    record = make_record(
        "Auto Expert",
        date(2026, 10, 15),
        datetime(2026, 10, 15, 8, 11),
        datetime(2026, 10, 15, 17, 0),
    )

    metrics = attendance_record_metrics(record)

    assert metrics["is_late"] is True
    assert metrics["late_minutes"] == 11
    assert metrics["undertime_hours"] == 0


def test_early_clock_out_calculates_undertime_without_lateness():
    record = make_record(
        "Auto Expert",
        date(2026, 10, 15),
        datetime(2026, 10, 15, 8, 0),
        datetime(2026, 10, 15, 16, 0),
    )

    metrics = attendance_record_metrics(record)

    assert metrics["is_late"] is False
    assert metrics["undertime_hours"] == 1


def test_four_worked_hours_counts_as_half_day():
    record = make_record(
        "Auto Expert",
        date(2026, 10, 15),
        datetime(2026, 10, 15, 8, 0),
        datetime(2026, 10, 15, 12, 0),
    )

    metrics = attendance_record_metrics(record)

    assert metrics["worked_hours"] == 4
    assert metrics["undertime_hours"] == 4
    assert metrics["half_day"] is True


def test_trece_sunday_morning_shift_is_full_scheduled_day():
    record = make_record(
        "Trece-Uno",
        date(2026, 10, 18),
        datetime(2026, 10, 18, 8, 0),
        datetime(2026, 10, 18, 12, 0),
    )

    metrics = attendance_record_metrics(record)

    assert metrics["worked_hours"] == 4
    assert metrics["undertime_hours"] == 0
    assert metrics["half_day"] is False


def test_other_sunday_is_not_counted_as_scheduled_work():
    record = make_record(
        "Auto Expert",
        date(2026, 10, 18),
        datetime(2026, 10, 18, 8, 0),
        datetime(2026, 10, 18, 12, 0),
    )

    metrics = attendance_record_metrics(record)

    assert metrics["undertime_hours"] == 0
    assert metrics["half_day"] is False