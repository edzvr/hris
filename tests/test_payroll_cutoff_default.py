from datetime import date
from unittest.mock import patch

from hris import payroll_cutoff_from_request


def test_default_payroll_cutoff_is_the_active_period():
    active_start = date(2026, 10, 17)
    with patch('hris.current_payroll_cutoff', return_value=(active_start, date(2026, 10, 23))):
        assert payroll_cutoff_from_request() == (
            active_start,
            date(2026, 10, 24),
        )


def test_explicit_payroll_cutoff_selection_is_preserved():
    with patch('hris.current_payroll_cutoff') as current_cutoff:
        assert payroll_cutoff_from_request('2026-10-03') == (
            date(2026, 10, 3),
            date(2026, 10, 10),
        )
    current_cutoff.assert_not_called()
