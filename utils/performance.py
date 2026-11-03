"""Read-only performance reporting rules, shared with evaluation approval."""

from collections import defaultdict
from datetime import datetime


def evaluation_points(average_rating):
    if average_rating >= 4.5:
        return 2, 0
    if average_rating >= 3.5:
        return 1, 0
    if average_rating < 1.5:
        return 0, 2
    if average_rating < 2.5:
        return 0, 1
    return 0, 0


def actual_evaluation_points(average_rating):
    if average_rating >= 4.5:
        return 5, 0
    if average_rating >= 3.5:
        return 3, 0
    if average_rating < 1.5:
        return 0, 5
    if average_rating < 2.5:
        return 0, 3
    return 0, 0


def unique_attendance(records):
    """Prefer the latest completed punch, then record ID, once per date."""
    selected = {}
    for row in records:
        key = (row.clock_out or datetime.min, row.id or 0)
        previous = selected.get(row.date)
        if previous is None or key > (previous.clock_out or datetime.min, previous.id or 0):
            selected[row.date] = row
    return [selected[day] for day in sorted(selected)]


def merit_report(attendance, evaluations, quizzes, awards, leaves=(), start=None, end=None):
    records = unique_attendance(attendance)
    approved_leave = lambda day: any(
        leave.start_date <= day <= leave.end_date for leave in leaves
    )
    categories = {
        "attendance": (
            sum(row.status == "Present" for row in records),
            sum(row.status == "Absent" and not approved_leave(row.date) for row in records),
        ),
    }
    groups = defaultdict(list)
    excluded_evaluations = 0
    for row in evaluations:
        kind = str(row.category or "").split("_", 1)[0]
        if (
            row.approval_status != "Approved" or kind not in {"admin", "peer"}
            or row.rating is None or not 1 <= row.rating <= 5 or row.date is None
        ):
            if row.date is None or (
                (start is None or row.date.date() >= start)
                and (end is None or row.date.date() <= end)
            ):
                excluded_evaluations += 1
            continue
        groups[(kind, row.evaluator_id, row.date.year, row.date.month)].append(row)
    averages = {"admin": [], "peer": []}
    for kind in averages:
        totals = [0, 0]
        for (group_kind, _, _, _), answers in groups.items():
            if group_kind != kind:
                continue
            group_date = max(answer.date for answer in answers).date()
            if (start is not None and group_date < start) or (end is not None and group_date > end):
                continue
            average = sum(answer.rating for answer in answers) / len(answers)
            averages[kind].append(average)
            points = (actual_evaluation_points if kind == "admin" else evaluation_points)(average)
            totals = [totals[i] + points[i] for i in range(2)]
        categories[kind] = tuple(totals)
    official = [
        q for q in quizzes
        if q.is_official is True and q.total_points > 0 and 0 <= q.score <= q.total_points
    ]
    categories["quiz"] = (sum(q.score / q.total_points >= .75 for q in official) * 5, 0)
    other_awards = [row for row in awards if row.source != "evaluation"]
    categories["recorded_awards"] = (
        sum(row.merit_points or 0 for row in other_awards),
        sum(row.demerit_points or 0 for row in other_awards),
    )
    return {
        "categories": categories,
        "merit": sum(pair[0] for pair in categories.values()),
        "demerit": sum(pair[1] for pair in categories.values()),
        "admin_average": sum(averages["admin"]) / len(averages["admin"]) if averages["admin"] else None,
        "peer_average": sum(averages["peer"]) / len(averages["peer"]) if averages["peer"] else None,
        "official_quizzes": len(official),
        "invalid_quizzes": sum(
            q.is_official is True and (q.total_points <= 0 or not 0 <= q.score <= q.total_points)
            for q in quizzes
        ),
        "quiz_average": sum(q.score / q.total_points * 100 for q in official) / len(official) if official else None,
        "excluded_evaluations": excluded_evaluations,
        "duplicate_attendance": len(attendance) - len(records),
        "legacy_awards": sum(not row.source for row in other_awards),
    }
