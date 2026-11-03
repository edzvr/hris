"""Database-driven SSS, PhilHealth and Pag-IBIG contribution services.

All monetary values are computed with Decimal and rounded half-up to centavos.
Contribution tables are effective-dated rows in the database; no rates are read
from source code after the initial official defaults are seeded.
"""

import csv
import io
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from sqlalchemy.exc import IntegrityError

from models import GovContributionAudit, GovContributionSetting, SSSContributionBracket, db

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
# Payroll months starting here use these database rules; earlier saved history is untouched.
RULES_START = date(2026, 10, 1)
WORKWEEK_DAYS = {5: 261, 6: 313}
AMOUNT_KEYS = (
    "sss", "philhealth", "pagibig",
    "employer_sss", "employer_sss_ec", "employer_philhealth", "employer_pagibig",
)
OFFICIAL_SOURCES = (
    "SSS Circular 2024-006 (Jan 2025 schedule); PhilHealth Advisory 2025-0002 (5%, "
    "PHP 10,000 floor, PHP 100,000 ceiling); Pag-IBIG Circular 460 (PHP 10,000 maximum "
    "fund salary, Feb 2024)"
)


class ContributionSettingsError(ValueError):
    """Raised when contribution rules are missing or invalid."""


def money(value):
    try:
        amount = Decimal(str(value if value is not None else 0))
    except InvalidOperation as exc:
        raise ContributionSettingsError(f"Invalid amount: {value!r}") from exc
    if not amount.is_finite():
        raise ContributionSettingsError(f"Invalid amount: {value!r}")
    return amount.quantize(CENT, ROUND_HALF_UP)


def month_start(value):
    return value.replace(day=1)


# ----------------------------------------------------------------- results


@dataclass(frozen=True)
class SSSResult:
    compensation: Decimal = ZERO
    salary_from: Decimal = ZERO
    salary_to: Decimal | None = None
    msc: Decimal = ZERO
    employee: Decimal = ZERO
    employer: Decimal = ZERO
    ec: Decimal = ZERO

    @property
    def bracket_label(self):
        if self.msc == ZERO:
            return "No compensation"
        upper = f"{self.salary_to:,.2f}" if self.salary_to is not None else "and above"
        return f"{self.salary_from:,.2f} - {upper}"

    @property
    def mpf_msc(self):
        """MSC portion above PHP 20,000 credited to the Mandatory Provident Fund (WISP)."""
        return max(self.msc - Decimal("20000"), ZERO)

    @property
    def employer_total(self):
        return self.employer + self.ec

    @property
    def total(self):
        return self.employee + self.employer + self.ec


@dataclass(frozen=True)
class PhilHealthResult:
    daily_rate: Decimal = ZERO
    workweek: int = 5
    emr: Decimal = ZERO
    basis: Decimal = ZERO
    rate: Decimal = ZERO
    premium: Decimal = ZERO
    employee: Decimal = ZERO
    employer: Decimal = ZERO

    @property
    def factor(self):
        return WORKWEEK_DAYS[self.workweek]

    @property
    def total(self):
        return self.employee + self.employer


@dataclass(frozen=True)
class PagibigResult:
    compensation: Decimal = ZERO
    fund_salary: Decimal = ZERO
    employee_rate: Decimal = ZERO
    employer_rate: Decimal = ZERO
    employee: Decimal = ZERO
    employer: Decimal = ZERO

    @property
    def total(self):
        return self.employee + self.employer


@dataclass(frozen=True)
class MonthlyContribution:
    sss: SSSResult = field(default_factory=SSSResult)
    philhealth: PhilHealthResult = field(default_factory=PhilHealthResult)
    pagibig: PagibigResult = field(default_factory=PagibigResult)

    @property
    def employee_total(self):
        return self.sss.employee + self.philhealth.employee + self.pagibig.employee

    @property
    def employer_total(self):
        return self.sss.employer_total + self.philhealth.employer + self.pagibig.employer

    @property
    def remittance_total(self):
        return self.employee_total + self.employer_total

    def amounts(self):
        return {
            "sss": self.sss.employee,
            "philhealth": self.philhealth.employee,
            "pagibig": self.pagibig.employee,
            "employer_sss": self.sss.employer,
            "employer_sss_ec": self.sss.ec,
            "employer_philhealth": self.philhealth.employer,
            "employer_pagibig": self.pagibig.employer,
        }


def zero_amounts():
    return {key: ZERO for key in AMOUNT_KEYS}


# ------------------------------------------------------------ repository


def official_sss_2025_rows():
    """Regular SS + EC + MPF schedule for business employers, effective January 2025."""
    rows = [(ZERO, Decimal("5249.99"), Decimal("5000"))]
    for msc in range(5500, 35000, 500):
        rows.append((Decimal(msc - 250), Decimal(msc + 249) + Decimal("0.99"), Decimal(msc)))
    rows.append((Decimal("34750"), None, Decimal("35000")))
    return [
        {
            "salary_from": low, "salary_to": high, "msc": msc,
            "employee_share": money(msc * Decimal("0.05")),
            "employer_share": money(msc * Decimal("0.10")),
            "ec": Decimal("10") if msc < 15000 else Decimal("30"),
        }
        for low, high, msc in rows
    ]


def official_default_values():
    return {
        "effective_month": date(2025, 1, 1),
        "philhealth_rate": Decimal("0.05"),
        "philhealth_floor": Decimal("10000"),
        "philhealth_ceiling": Decimal("100000"),
        "default_workweek": 5,
        "pagibig_low_threshold": Decimal("1500"),
        "pagibig_employee_rate_low": Decimal("0.01"),
        "pagibig_employee_rate_high": Decimal("0.02"),
        "pagibig_employer_rate": Decimal("0.02"),
        "pagibig_max_fund_salary": Decimal("10000"),
        "source_reference": OFFICIAL_SOURCES,
    }


def ensure_default_settings():
    """Seed the official 2025 rules once; existing administrator versions are never changed."""
    if GovContributionSetting.query.first() is not None:
        return False
    values = official_default_values()
    setting = GovContributionSetting(**{
        key: (float(value) if isinstance(value, Decimal) else value)
        for key, value in values.items()
    })
    setting.sss_brackets = [
        SSSContributionBracket(**{
            key: (float(value) if value is not None else None) for key, value in row.items()
        })
        for row in official_sss_2025_rows()
    ]
    db.session.add(setting)
    try:
        db.session.commit()
    except IntegrityError:
        # Another worker seeded the same effective month first.
        db.session.rollback()
        return False
    logger.info("Seeded official government contribution settings effective 2025-01.")
    return True


class ContributionSettingsRepository:
    @staticmethod
    def for_month(month):
        setting = GovContributionSetting.query.filter(
            GovContributionSetting.effective_month <= month_start(month)
        ).order_by(GovContributionSetting.effective_month.desc()).first()
        if setting is None:
            raise ContributionSettingsError(
                f"No government contribution settings are effective for {month:%B %Y}."
            )
        errors = validate_setting(setting_values(setting), bracket_values(setting.sss_brackets))
        if errors:
            raise ContributionSettingsError(
                f"Contribution settings effective {setting.effective_month:%B %Y} are invalid: "
                + "; ".join(errors)
            )
        return setting


def setting_values(setting):
    return {
        "effective_month": setting.effective_month,
        "philhealth_rate": money_rate(setting.philhealth_rate),
        "philhealth_floor": money(setting.philhealth_floor),
        "philhealth_ceiling": money(setting.philhealth_ceiling),
        "default_workweek": setting.default_workweek,
        "pagibig_low_threshold": money(setting.pagibig_low_threshold),
        "pagibig_employee_rate_low": money_rate(setting.pagibig_employee_rate_low),
        "pagibig_employee_rate_high": money_rate(setting.pagibig_employee_rate_high),
        "pagibig_employer_rate": money_rate(setting.pagibig_employer_rate),
        "pagibig_max_fund_salary": money(setting.pagibig_max_fund_salary),
        "source_reference": setting.source_reference or "",
    }


def money_rate(value):
    rate = Decimal(str(value if value is not None else "NaN"))
    if not rate.is_finite():
        raise ContributionSettingsError(f"Invalid rate: {value!r}")
    return rate.quantize(Decimal("0.000001"), ROUND_HALF_UP)


def bracket_values(brackets):
    return [
        {
            "salary_from": money(row.salary_from),
            "salary_to": money(row.salary_to) if row.salary_to is not None else None,
            "msc": money(row.msc),
            "employee_share": money(row.employee_share),
            "employer_share": money(row.employer_share),
            "ec": money(row.ec),
        }
        for row in sorted(brackets, key=lambda item: item.salary_from)
    ]


def validate_setting(values, brackets):
    errors = []
    if not Decimal("0") < values["philhealth_rate"] <= Decimal("1"):
        errors.append("PhilHealth rate must be greater than 0 and at most 100%")
    if values["philhealth_floor"] <= 0 or values["philhealth_ceiling"] < values["philhealth_floor"]:
        errors.append("PhilHealth floor must be positive and not above the ceiling")
    if values["default_workweek"] not in WORKWEEK_DAYS:
        errors.append("Default workweek must be 5 or 6 days")
    for key in ("pagibig_employee_rate_low", "pagibig_employee_rate_high", "pagibig_employer_rate"):
        if not Decimal("0") <= values[key] <= Decimal("1"):
            errors.append("Pag-IBIG rates must be between 0% and 100%")
            break
    if values["pagibig_low_threshold"] < 0 or values["pagibig_max_fund_salary"] <= 0:
        errors.append("Pag-IBIG threshold must be non-negative and maximum fund salary positive")
    errors.extend(validate_brackets(brackets))
    return errors


def validate_brackets(brackets):
    if not brackets:
        return ["SSS contribution table has no brackets"]
    errors = []
    if brackets[0]["salary_from"] != ZERO:
        errors.append("The first SSS bracket must start at 0.00")
    for index, row in enumerate(brackets, start=1):
        if any(row[key] < 0 for key in ("msc", "employee_share", "employer_share", "ec")):
            errors.append(f"SSS row {index}: amounts cannot be negative")
        if row["msc"] <= 0:
            errors.append(f"SSS row {index}: MSC must be positive")
        last = index == len(brackets)
        if row["salary_to"] is None and not last:
            errors.append(f"SSS row {index}: only the last bracket may have no upper limit")
        if row["salary_to"] is not None and row["salary_to"] < row["salary_from"]:
            errors.append(f"SSS row {index}: salary_to is below salary_from")
        if last and row["salary_to"] is not None:
            errors.append("The last SSS bracket must have no upper limit (leave salary_to blank)")
        if index > 1:
            previous = brackets[index - 2]
            if previous["salary_to"] is not None and row["salary_from"] != previous["salary_to"] + CENT:
                errors.append(
                    f"SSS row {index}: must start at {previous['salary_to'] + CENT:,.2f} "
                    "(no gaps or overlaps)"
                )
            if row["msc"] <= previous["msc"]:
                errors.append(f"SSS row {index}: MSC must increase")
    return errors


def parse_sss_table(text):
    """Parse CSV lines: salary_from,salary_to,msc,employee_share,employer_share,ec."""
    rows = []
    errors = []
    reader = csv.reader(io.StringIO(text or ""))
    for line_number, cells in enumerate(reader, start=1):
        cells = [cell.strip().replace(",", "") for cell in cells]
        if not any(cells) or cells[0].lower().startswith("salary"):
            continue
        if len(cells) != 6:
            errors.append(f"Line {line_number}: expected 6 values")
            continue
        try:
            rows.append({
                "salary_from": money(cells[0]),
                "salary_to": money(cells[1]) if cells[1] else None,
                "msc": money(cells[2]),
                "employee_share": money(cells[3]),
                "employer_share": money(cells[4]),
                "ec": money(cells[5]),
            })
        except ContributionSettingsError:
            errors.append(f"Line {line_number}: values must be numbers")
    rows.sort(key=lambda row: row["salary_from"])
    return rows, errors


def format_sss_table(brackets):
    lines = ["salary_from,salary_to,msc,employee_share,employer_share,ec"]
    for row in brackets:
        lines.append(",".join([
            f"{row['salary_from']:.2f}",
            f"{row['salary_to']:.2f}" if row["salary_to"] is not None else "",
            f"{row['msc']:.2f}", f"{row['employee_share']:.2f}",
            f"{row['employer_share']:.2f}", f"{row['ec']:.2f}",
        ]))
    return "\n".join(lines)


# --------------------------------------------------------- agency services


class SSSService:
    def __init__(self, setting):
        self.brackets = bracket_values(setting.sss_brackets)

    def compute(self, compensation):
        compensation = money(compensation)
        if compensation <= 0:
            return SSSResult()
        for row in self.brackets:
            if row["salary_from"] <= compensation and (
                row["salary_to"] is None or compensation <= row["salary_to"]
            ):
                return SSSResult(
                    compensation=compensation, salary_from=row["salary_from"],
                    salary_to=row["salary_to"], msc=row["msc"],
                    employee=row["employee_share"], employer=row["employer_share"], ec=row["ec"],
                )
        raise ContributionSettingsError(f"No SSS bracket covers PHP {compensation:,.2f}.")


class PhilHealthService:
    """Daily-paid basis: Equivalent Monthly Rate, never reduced by absences or tardiness."""

    def __init__(self, setting):
        self.values = setting_values(setting)

    def resolve_workweek(self, workweek):
        workweek = int(workweek or self.values["default_workweek"])
        if workweek not in WORKWEEK_DAYS:
            raise ContributionSettingsError("PhilHealth workweek must be 5 or 6 days.")
        return workweek

    def compute(self, daily_rate, workweek=None, has_compensation=True):
        workweek = self.resolve_workweek(workweek)
        daily_rate = money(daily_rate)
        if not has_compensation or daily_rate <= 0:
            return PhilHealthResult(daily_rate=daily_rate, workweek=workweek, rate=self.values["philhealth_rate"])
        emr = money(daily_rate * WORKWEEK_DAYS[workweek] / Decimal(12))
        basis = min(max(emr, self.values["philhealth_floor"]), self.values["philhealth_ceiling"])
        premium = money(basis * self.values["philhealth_rate"])
        employee = money(premium / 2)
        return PhilHealthResult(
            daily_rate=daily_rate, workweek=workweek, emr=emr, basis=basis,
            rate=self.values["philhealth_rate"], premium=premium,
            employee=employee, employer=premium - employee,
        )


class PagibigService:
    def __init__(self, setting):
        self.values = setting_values(setting)

    def compute(self, compensation):
        compensation = money(compensation)
        if compensation <= 0:
            return PagibigResult()
        fund_salary = min(compensation, self.values["pagibig_max_fund_salary"])
        employee_rate = (
            self.values["pagibig_employee_rate_low"]
            if compensation <= self.values["pagibig_low_threshold"]
            else self.values["pagibig_employee_rate_high"]
        )
        employer_rate = self.values["pagibig_employer_rate"]
        return PagibigResult(
            compensation=compensation, fund_salary=fund_salary,
            employee_rate=employee_rate, employer_rate=employer_rate,
            employee=money(fund_salary * employee_rate), employer=money(fund_salary * employer_rate),
        )


class GovernmentContributionCalculator:
    def __init__(self, setting):
        self.setting = setting
        self.sss = SSSService(setting)
        self.philhealth = PhilHealthService(setting)
        self.pagibig = PagibigService(setting)

    @classmethod
    def for_month(cls, month):
        return cls(ContributionSettingsRepository.for_month(month))

    def monthly(self, compensation, daily_rate, workweek=None):
        compensation = money(compensation)
        has_compensation = compensation > 0
        return MonthlyContribution(
            sss=self.sss.compute(compensation),
            philhealth=self.philhealth.compute(daily_rate, workweek, has_compensation),
            pagibig=self.pagibig.compute(compensation),
        )


class PayrollService:
    """Spread a monthly contribution across weekly cutoffs.

    Earlier cutoffs deduct an estimate from that cutoff's pace; the last cutoff
    of the payroll month deducts the exact monthly amount minus what earlier
    cutoffs already deducted (negative means a refund of over-deduction).
    """

    @staticmethod
    def estimate(calculator, cutoff_basis, cutoff_count, daily_rate, workweek):
        if money(cutoff_basis) <= 0:
            return zero_amounts(), ZERO
        monthly = calculator.monthly(money(cutoff_basis) * cutoff_count, daily_rate, workweek)
        amounts = {key: money(value / cutoff_count) for key, value in monthly.amounts().items()}
        employee_total = amounts["sss"] + amounts["philhealth"] + amounts["pagibig"]
        return amounts, employee_total * cutoff_count

    @classmethod
    def allocate(cls, calculator, bases, index, prior_amounts, daily_rate, workweek):
        count = len(bases)
        if index < count - 1:
            return cls.estimate(calculator, bases[index], count, daily_rate, workweek)
        monthly = calculator.monthly(sum((money(value) for value in bases), ZERO), daily_rate, workweek)
        already = zero_amounts()
        for position in range(count - 1):
            previous = prior_amounts[position]
            if previous is None:
                previous, _ = cls.estimate(calculator, bases[position], count, daily_rate, workweek)
            for key in AMOUNT_KEYS:
                already[key] += money(previous.get(key, 0))
        amounts = {key: value - already[key] for key, value in monthly.amounts().items()}
        return amounts, monthly.employee_total


# ------------------------------------------------------ summary services


def _sum(rows, getter):
    return sum((getter(row) for row in rows), ZERO)


class PortalPaymentService:
    @staticmethod
    def summarize(rows):
        def totals(selected):
            sss_ee = _sum(selected, lambda row: row["sss_ee"])
            sss_er = _sum(selected, lambda row: row["sss_er"])
            sss_ec = _sum(selected, lambda row: row["sss_ec"])
            ph_ee = _sum(selected, lambda row: row["ph_ee"])
            ph_er = _sum(selected, lambda row: row["ph_er"])
            pi_ee = _sum(selected, lambda row: row["pi_ee"])
            pi_er = _sum(selected, lambda row: row["pi_er"])
            deducted = _sum(selected, lambda row: row["deducted_ee"])
            employee_share = sss_ee + ph_ee + pi_ee
            return {
                "employees": len(selected),
                "sss": {"ee": sss_ee, "er": sss_er, "ec": sss_ec, "total": sss_ee + sss_er + sss_ec},
                "philhealth": {"ee": ph_ee, "er": ph_er, "total": ph_ee + ph_er},
                "pagibig": {"ee": pi_ee, "er": pi_er, "total": pi_ee + pi_er},
                "employee_share": employee_share,
                "employer_share": sss_er + sss_ec + ph_er + pi_er,
                "overall": sss_ee + sss_er + sss_ec + ph_ee + ph_er + pi_ee + pi_er,
                "deducted_ee": deducted,
                "ee_difference": employee_share - deducted,
            }

        companies = sorted({row["company"] for row in rows})
        return {
            "companies": {company: totals([row for row in rows if row["company"] == company])
                          for company in companies},
            "overall": totals(rows),
        }


class LaborCostService:
    @staticmethod
    def summarize(rows):
        gross = _sum(rows, lambda row: row["gross_pay"])
        sss_er = _sum(rows, lambda row: row["sss_er"])
        sss_ec = _sum(rows, lambda row: row["sss_ec"])
        ph_er = _sum(rows, lambda row: row["ph_er"])
        pi_er = _sum(rows, lambda row: row["pi_er"])
        employer_cost = sss_er + sss_ec + ph_er + pi_er
        return {
            "employees": len(rows),
            "gross": gross,
            "sss_er": sss_er, "sss_ec": sss_ec, "ph_er": ph_er, "pi_er": pi_er,
            "employer_contributions": employer_cost,
            "government_liability": _sum(rows, lambda row: row["remittance_total"]),
            "labor_cost": gross + employer_cost,
        }


class AuditTrailService:
    @staticmethod
    def record(actor, action, entity_id=None, details=None):
        name = actor.full_name() if actor is not None and hasattr(actor, "full_name") else "System"
        db.session.add(GovContributionAudit(
            actor_id=getattr(actor, "id", None), actor_name=(name or "")[:120],
            action=action[:80], entity_id=entity_id,
            details=json.dumps(details or {}, default=str, sort_keys=True),
            created_at=datetime.utcnow(),
        ))


# ---------------------------------------------------------------- exports


class GovernmentReportsService:
    MIMETYPES = {
        "csv": ("text/csv", "csv"),
        "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
        "pdf": ("application/pdf", "pdf"),
    }

    @staticmethod
    def _cell(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + value
        return value

    @classmethod
    def export(cls, title, subtitle, headers, rows, fmt):
        if fmt not in cls.MIMETYPES:
            raise ValueError("Unsupported export format.")
        rows = [[cls._cell(value) for value in row] for row in rows]
        buffer = io.BytesIO()
        if fmt == "csv":
            text = io.StringIO()
            writer = csv.writer(text)
            writer.writerow([title])
            writer.writerow([subtitle])
            writer.writerow(headers)
            writer.writerows(rows)
            buffer.write(text.getvalue().encode("utf-8-sig"))
        elif fmt == "xlsx":
            from openpyxl import Workbook
            from openpyxl.styles import Font

            workbook = Workbook()
            sheet = workbook.active
            sheet.title = title[:31]
            sheet.append([title])
            sheet.append([subtitle])
            sheet.append(headers)
            for cell in sheet[3]:
                cell.font = Font(bold=True)
            sheet["A1"].font = Font(bold=True, size=13)
            for row in rows:
                sheet.append(row)
            for column_cells in sheet.iter_cols(min_row=3):
                width = max(len(str(cell.value or "")) for cell in column_cells)
                sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 10), 40)
                for cell in column_cells[1:]:
                    if isinstance(cell.value, float):
                        cell.number_format = "#,##0.00"
            sheet.freeze_panes = "A4"
            workbook.save(buffer)
        else:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import landscape, legal
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

            styles = getSampleStyleSheet()
            document = SimpleDocTemplate(
                buffer, pagesize=landscape(legal), leftMargin=18, rightMargin=18,
                topMargin=24, bottomMargin=24, title=title,
            )
            small = styles["BodyText"].clone("small", fontSize=6, leading=7)
            data = [[Paragraph(f"<b>{header}</b>", small) for header in headers]]
            for row in rows:
                data.append([
                    Paragraph(f"{value:,.2f}" if isinstance(value, float) else str(value if value is not None else ""), small)
                    for value in row
                ])
            table = Table(data, repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e4efe5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            document.build([
                Paragraph(title, styles["Title"]), Paragraph(subtitle, styles["BodyText"]),
                Spacer(1, 8), table,
            ])
        buffer.seek(0)
        mimetype, extension = cls.MIMETYPES[fmt]
        return buffer, mimetype, extension


def is_finite_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
