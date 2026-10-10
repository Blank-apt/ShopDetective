"""CSV ingestion, column mapping and strict validation of the input contracts.

Monetary values are parsed from the original strings with ``Decimal`` and
stored as integer minor units (paise/cents/pence). IDs stay as strings, so
leading zeros are preserved. Invalid financial records stop the analysis
instead of being silently dropped.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from .config import CURRENCIES, MINOR_UNITS, Limits
from .schemas import ValidationIssue

ORDER_FIELDS: dict[str, str] = {
    "order_line_id": "Unique ID of one purchased item line",
    "product_id": "Stable product ID",
    "product_name": "Product display name",
    "date": "Purchase date (YYYY-MM-DD)",
    "sale_amount": "Amount paid for this item line",
    "refund_amount": "Total recorded refund for this line (0 if none)",
}
FEEDBACK_FIELDS: dict[str, str] = {
    "message_id": "Unique ID of one customer message",
    "order_line_id": "Exact purchase item line the message refers to",
    "message": "Customer feedback text",
}

# Obvious header synonyms used only to *suggest* a mapping; the user confirms.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "order_line_id": ("orderlineid", "lineid", "orderitemid", "itemlineid", "orderline"),
    "product_id": ("productid", "sku", "productcode", "itemid"),
    "product_name": ("productname", "product", "itemname", "name", "title"),
    "date": ("date", "purchasedate", "orderdate", "purchasedon"),
    "sale_amount": ("saleamount", "sale", "amount", "price", "amountpaid", "lineamount"),
    "refund_amount": ("refundamount", "refund", "refunded", "refundedamount"),
    "message_id": ("messageid", "feedbackid", "commentid", "reviewid", "ticketid"),
    "message": ("message", "feedback", "comment", "text", "review", "body"),
}

_MONEY_PATTERN = re.compile(r"^[+-]?\d+(\.\d+)?$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MoneyError(ValueError):
    pass


def parse_money_minor(raw: str) -> int:
    """Parse a plain decimal string into integer minor units.

    Accepts e.g. ``"800"``, ``"800.5"``, ``"800.50"``. Rejects empty values,
    thousands separators, currency symbols, exponent notation, NaN/Infinity,
    negative amounts and more than two decimal places.
    """
    text = (raw or "").strip()
    if text == "":
        raise MoneyError("amount is missing")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise MoneyError(
            f"'{text}' is not a plain number (use digits with '.' as the decimal "
            "point; no currency symbols or thousands separators)"
        ) from None
    if not value.is_finite():
        raise MoneyError(f"'{text}' is not a finite amount")
    if not _MONEY_PATTERN.match(text):
        raise MoneyError(f"'{text}' must be written in plain decimal notation, e.g. 800.00")
    if value < 0:
        raise MoneyError(f"'{text}' is negative")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MINOR_UNITS:
        raise MoneyError(f"'{text}' has more than {MINOR_UNITS} decimal places")
    return int(value.scaleb(MINOR_UNITS))


def format_minor(minor: int, currency: str) -> str:
    """Format minor units for display, e.g. ``INR 16,000.00``."""
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 10**MINOR_UNITS)
    return f"{currency} {sign}{whole:,}.{frac:0{MINOR_UNITS}d}"


def minor_to_decimal_str(minor: int) -> str:
    whole, frac = divmod(abs(minor), 10**MINOR_UNITS)
    return f"{'-' if minor < 0 else ''}{whole}.{frac:0{MINOR_UNITS}d}"


def parse_iso_date(raw: str) -> date:
    text = (raw or "").strip()
    if not _DATE_PATTERN.match(text):
        raise ValueError(f"'{text}' is not an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a valid calendar date") from None


# ---------------------------------------------------------------- CSV reading


@dataclass
class RawTable:
    name: str
    size_bytes: int
    frame: pd.DataFrame | None
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.frame is not None and not any(i.level == "error" for i in self.issues)


def read_csv_bytes(data: bytes, name: str, limits: Limits) -> RawTable:
    """Read a CSV upload as strings. Never guesses locale or number formats."""
    size = len(data)
    if size > limits.max_file_bytes:
        mb = limits.max_file_bytes / (1024 * 1024)
        return RawTable(
            name,
            size,
            None,
            [
                ValidationIssue(
                    "error",
                    "file_too_large",
                    f"{name} is {size / (1024 * 1024):.1f} MB; the limit is {mb:.0f} MB. "
                    "Split the file or raise SD_MAX_FILE_MB.",
                    name,
                )
            ],
        )
    if size == 0 or not data.strip():
        return RawTable(name, size, None, [ValidationIssue("error", "empty_file", f"{name} is empty.", name)])
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return RawTable(
            name,
            size,
            None,
            [
                ValidationIssue(
                    "error",
                    "encoding",
                    f"{name} is not UTF-8 text. In Excel use 'Save As → CSV UTF-8 (Comma delimited)' and upload again.",
                    name,
                )
            ],
        )
    try:
        frame = pd.read_csv(
            io.StringIO(text),
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            skipinitialspace=False,
        )
    except (pd.errors.ParserError, csv.Error) as exc:
        return RawTable(
            name,
            size,
            None,
            [
                ValidationIssue(
                    "error",
                    "parse_error",
                    f"{name} could not be parsed as a comma-separated file "
                    f"({str(exc).splitlines()[0][:160]}). Check that fields are separated "
                    "by commas and that text containing commas is wrapped in double quotes.",
                    name,
                )
            ],
        )
    except pd.errors.EmptyDataError:
        return RawTable(name, size, None, [ValidationIssue("error", "empty_file", f"{name} is empty.", name)])
    frame.columns = [str(c).strip() for c in frame.columns]
    issues: list[ValidationIssue] = []
    if len(frame.columns) == 1 and any(sep in frame.columns[0] for sep in (";", "\t", "|")):
        issues.append(
            ValidationIssue(
                "error",
                "delimiter",
                f"{name} appears to use ';', tab or '|' as the separator. Export it as comma-separated CSV.",
                name,
            )
        )
    if len(set(frame.columns)) != len(frame.columns):
        issues.append(ValidationIssue("error", "duplicate_columns", f"{name} has repeated column names.", name))
    if frame.empty:
        issues.append(ValidationIssue("error", "empty_file", f"{name} has a header but no data rows.", name))
    return RawTable(name, size, frame, issues)


def _norm_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def suggest_mapping(columns: list[str], required: dict[str, str]) -> dict[str, str | None]:
    """Suggest canonical-field -> column matches from obvious header names."""
    normalized = {_norm_header(c): c for c in columns}
    used: set[str] = set()
    suggestion: dict[str, str | None] = {}
    for canonical in required:
        candidates = (_norm_header(canonical),) + _SYNONYMS.get(canonical, ())
        match = None
        for cand in candidates:
            col = normalized.get(cand)
            if col is not None and col not in used:
                match = col
                break
        suggestion[canonical] = match
        if match:
            used.add(match)
    return suggestion


def apply_mapping(
    frame: pd.DataFrame, mapping: dict[str, str | None], required: dict[str, str], file: str
) -> tuple[pd.DataFrame | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    missing = [f for f in required if not mapping.get(f)]
    for f in missing:
        issues.append(
            ValidationIssue(
                "error",
                "unmapped_column",
                f"Required field '{f}' ({required[f]}) is not mapped to a column.",
                file,
                field=f,
            )
        )
    chosen = [mapping[f] for f in required if mapping.get(f)]
    if len(set(chosen)) != len(chosen):
        issues.append(
            ValidationIssue(
                "error",
                "mapping_reused",
                "The same source column is mapped to more than one required field.",
                file,
            )
        )
    absent = [c for c in chosen if c not in frame.columns]
    for c in absent:
        issues.append(ValidationIssue("error", "missing_column", f"Column '{c}' is not in {file}.", file))
    if issues:
        return None, issues
    canonical = pd.DataFrame({f: frame[mapping[f]].astype(str) for f in required})
    canonical.insert(0, "source_row", range(2, len(frame) + 2))  # CSV line numbers
    return canonical, issues


# ------------------------------------------------------------- validation


@dataclass
class OrdersValidation:
    orders: pd.DataFrame | None
    issues: list[ValidationIssue]
    name_conflicts: dict[str, list[str]]

    @property
    def ok(self) -> bool:
        return self.orders is not None and not any(i.level == "error" for i in self.issues)


def validate_orders(canonical: pd.DataFrame, limits: Limits, file: str = "orders.csv") -> OrdersValidation:
    issues: list[ValidationIssue] = []
    if len(canonical) > limits.max_order_lines:
        issues.append(
            ValidationIssue(
                "error",
                "too_many_rows",
                f"{file} has {len(canonical):,} purchase lines; the limit is "
                f"{limits.max_order_lines:,}. Filter the export or raise SD_MAX_ORDER_LINES.",
                file,
            )
        )
        return OrdersValidation(None, issues, {})

    rows: list[dict] = []
    seen: dict[str, tuple[int, tuple]] = {}
    for rec in canonical.to_dict("records"):
        line = int(rec["source_row"])
        olid = rec["order_line_id"].strip()
        pid = rec["product_id"].strip()
        pname = rec["product_name"].strip()
        row_errors = False
        for fname, value in (("order_line_id", olid), ("product_id", pid), ("product_name", pname)):
            if value == "":
                issues.append(ValidationIssue("error", "blank_value", f"'{fname}' is blank.", file, line, fname))
                row_errors = True
        try:
            purchase_date = parse_iso_date(rec["date"])
        except ValueError as exc:
            issues.append(ValidationIssue("error", "invalid_date", str(exc), file, line, "date"))
            row_errors = True
            purchase_date = None
        amounts: dict[str, int] = {}
        for fname in ("sale_amount", "refund_amount"):
            try:
                amounts[fname] = parse_money_minor(rec[fname])
            except MoneyError as exc:
                issues.append(ValidationIssue("error", "invalid_amount", f"{fname}: {exc}", file, line, fname))
                row_errors = True
                continue
            if amounts[fname] > limits.max_line_amount_minor:
                issues.append(
                    ValidationIssue(
                        "error",
                        "amount_too_large",
                        f"{fname}: '{rec[fname].strip()}' is above the supported maximum of "
                        f"{format_minor(limits.max_line_amount_minor, '').strip()} per purchase line. "
                        "Check the value (for example a misplaced decimal point or merged cells).",
                        file,
                        line,
                        fname,
                    )
                )
                del amounts[fname]
                row_errors = True
        if len(amounts) == 2 and amounts["refund_amount"] > amounts["sale_amount"]:
            issues.append(
                ValidationIssue(
                    "error",
                    "refund_exceeds_sale",
                    "refund_amount is larger than sale_amount for this line. Under the "
                    "line-level input contract a refund cannot exceed the line's sale amount.",
                    file,
                    line,
                    "refund_amount",
                )
            )
            row_errors = True
        signature = (pid, pname, rec["date"].strip(), rec["sale_amount"].strip(), rec["refund_amount"].strip())
        if olid:
            if olid in seen:
                first_line, first_sig = seen[olid]
                if first_sig == signature:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "duplicate_row",
                            f"Exact duplicate of line {first_line} for order_line_id '{olid}'; counted once.",
                            file,
                            line,
                            "order_line_id",
                        )
                    )
                else:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "conflicting_duplicate_id",
                            f"order_line_id '{olid}' also appears on line {first_line} with different values.",
                            file,
                            line,
                            "order_line_id",
                        )
                    )
                continue
            seen[olid] = (line, signature)
        if row_errors:
            continue
        rows.append(
            {
                "order_line_id": olid,
                "product_id": pid,
                "product_name": pname,
                "purchase_date": purchase_date,
                "sale_minor": amounts["sale_amount"],
                "refund_minor": amounts["refund_amount"],
                "source_row": line,
            }
        )

    if any(i.level == "error" for i in issues):
        return OrdersValidation(None, issues, {})

    # Exact Python-integer total, checked before int64 storage. Refunds never
    # exceed sales per line, so this also bounds every refund aggregate.
    total_sales = sum(r["sale_minor"] for r in rows)
    if total_sales > limits.max_total_amount_minor:
        issues.append(
            ValidationIssue(
                "error",
                "total_too_large",
                f"The sale_amount values in {file} add up to {format_minor(total_sales, '').strip()}, "
                f"above the supported total of {format_minor(limits.max_total_amount_minor, '').strip()} "
                "per file. Check for mis-scaled amounts or split the export.",
                file,
                field="sale_amount",
            )
        )
        return OrdersValidation(None, issues, {})

    orders = pd.DataFrame(rows)
    orders["sale_minor"] = orders["sale_minor"].astype("int64")
    orders["refund_minor"] = orders["refund_minor"].astype("int64")
    name_conflicts: dict[str, list[str]] = {}
    for pid, group in orders.groupby("product_id", sort=True):
        names = list(dict.fromkeys(group["product_name"]))
        if len(names) > 1:
            name_conflicts[str(pid)] = names
            issues.append(
                ValidationIssue(
                    "warning",
                    "product_name_conflict",
                    f"product_id '{pid}' has {len(names)} different names "
                    f"({', '.join(repr(n) for n in names[:4])}). Confirm one display name.",
                    file,
                    field="product_name",
                )
            )
    return OrdersValidation(orders, issues, name_conflicts)


@dataclass
class FeedbackValidation:
    feedback: pd.DataFrame | None
    issues: list[ValidationIssue]

    @property
    def ok(self) -> bool:
        return self.feedback is not None and not any(i.level == "error" for i in self.issues)


def validate_feedback(
    canonical: pd.DataFrame,
    known_order_ids: set[str],
    limits: Limits,
    file: str = "feedback.csv",
) -> FeedbackValidation:
    """Validate feedback rows. Unknown order references stay visible for review."""
    issues: list[ValidationIssue] = []
    if len(canonical) > limits.max_feedback_messages:
        issues.append(
            ValidationIssue(
                "error",
                "too_many_rows",
                f"{file} has {len(canonical):,} messages; the limit is "
                f"{limits.max_feedback_messages:,}. Raise SD_MAX_FEEDBACK_MESSAGES or split the file.",
                file,
            )
        )
        return FeedbackValidation(None, issues)
    rows: list[dict] = []
    seen: dict[str, tuple[int, tuple]] = {}
    for rec in canonical.to_dict("records"):
        line = int(rec["source_row"])
        mid = rec["message_id"].strip()
        olid = rec["order_line_id"].strip()
        text = rec["message"]
        if mid == "":
            issues.append(ValidationIssue("error", "blank_value", "'message_id' is blank.", file, line, "message_id"))
            continue
        signature = (olid, text)
        if mid in seen:
            first_line, first_sig = seen[mid]
            if first_sig == signature:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "duplicate_row",
                        f"Exact duplicate of line {first_line} for message_id '{mid}'; kept once.",
                        file,
                        line,
                        "message_id",
                    )
                )
            else:
                issues.append(
                    ValidationIssue(
                        "error",
                        "conflicting_duplicate_id",
                        f"message_id '{mid}' also appears on line {first_line} with different values.",
                        file,
                        line,
                        "message_id",
                    )
                )
            continue
        seen[mid] = (line, signature)
        input_issue = ""  # empty string, not None: pandas 3 str columns turn None into NaN
        if text.strip() == "":
            input_issue = "empty_message"
        elif len(text) > limits.max_message_chars:
            input_issue = "message_too_long"
        link_status = "linked" if olid in known_order_ids else "unknown"
        if link_status == "unknown":
            issues.append(
                ValidationIssue(
                    "warning",
                    "unknown_order_link",
                    f"message '{mid}' refers to order_line_id '{olid}', which is not in "
                    "orders.csv. It stays in the review queue and outside issue totals until "
                    "you link it to an existing purchase line.",
                    file,
                    line,
                    "order_line_id",
                )
            )
        if input_issue:
            issues.append(
                ValidationIssue(
                    "warning",
                    input_issue,
                    f"message '{mid}' is "
                    + (
                        "empty"
                        if input_issue == "empty_message"
                        else f"longer than {limits.max_message_chars} characters"
                    )
                    + "; it will not be sent to the AI and needs manual review.",
                    file,
                    line,
                    "message",
                )
            )
        rows.append(
            {
                "message_id": mid,
                "order_line_id": olid,
                "message": text,
                "link_status": link_status,
                "input_issue": input_issue,
                "source_row": line,
            }
        )
    if any(i.level == "error" for i in issues):
        return FeedbackValidation(None, issues)
    feedback = pd.DataFrame(
        rows,
        columns=["message_id", "order_line_id", "message", "link_status", "input_issue", "source_row"],
    )
    return FeedbackValidation(feedback, issues)


# ----------------------------------------------------------------- digests


def message_digest(message_id: str, text: str) -> str:
    return hashlib.sha256(f"{message_id}\x1f{text}".encode()).hexdigest()


def dataset_digest(orders: pd.DataFrame, feedback: pd.DataFrame, currency: str) -> str:
    """Stable digest of the canonical dataset content (order-independent)."""
    h = hashlib.sha256()
    h.update(f"currency={currency}\n".encode())
    for rec in sorted(
        orders[["order_line_id", "product_id", "product_name", "purchase_date", "sale_minor", "refund_minor"]]
        .astype(str)
        .itertuples(index=False, name=None)
    ):
        h.update(("\x1f".join(rec) + "\n").encode())
    h.update(b"--feedback--\n")
    for rec in sorted(
        feedback[["message_id", "order_line_id", "message"]].astype(str).itertuples(index=False, name=None)
    ):
        h.update(("\x1f".join(rec) + "\n").encode())
    return h.hexdigest()


def feedback_digest(feedback: pd.DataFrame) -> str:
    h = hashlib.sha256()
    for rec in sorted(
        feedback[["message_id", "order_line_id", "message"]].astype(str).itertuples(index=False, name=None)
    ):
        h.update(("\x1f".join(rec) + "\n").encode())
    return h.hexdigest()


def template_csv(fields: dict[str, str]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(list(fields))
    if "sale_amount" in fields:
        writer.writerow(["OL-0001", "P001", "Classic Hoodie", "2026-07-01", "800.00", "0.00"])
    else:
        writer.writerow(["M0001", "OL-0001", "The hoodie fits perfectly."])
    return buf.getvalue().encode("utf-8")


# ------------------------------------------------------------ dataset build


@dataclass
class BuildResult:
    dataset: object | None  # session.Dataset
    issues: list[ValidationIssue]
    name_conflicts: dict[str, list[str]]

    @property
    def ok(self) -> bool:
        return self.dataset is not None


def build_dataset(
    orders_raw: RawTable,
    feedback_raw: RawTable,
    orders_mapping: dict[str, str | None],
    feedback_mapping: dict[str, str | None],
    currency: str,
    limits: Limits,
    sample_name: str | None = None,
    confirmed_names: dict[str, str] | None = None,
):
    """Validate both tables and return a Dataset, or the blocking issues."""
    from .session import Dataset

    issues: list[ValidationIssue] = list(orders_raw.issues) + list(feedback_raw.issues)
    if not orders_raw.ok or not feedback_raw.ok:
        return BuildResult(None, issues, {})
    o_canon, o_issues = apply_mapping(orders_raw.frame, orders_mapping, ORDER_FIELDS, orders_raw.name)
    f_canon, f_issues = apply_mapping(feedback_raw.frame, feedback_mapping, FEEDBACK_FIELDS, feedback_raw.name)
    issues += o_issues + f_issues
    if o_canon is None or f_canon is None:
        return BuildResult(None, issues, {})
    ov = validate_orders(o_canon, limits, orders_raw.name)
    issues += ov.issues
    if not ov.ok:
        return BuildResult(None, issues, {})
    fv = validate_feedback(f_canon, set(ov.orders["order_line_id"]), limits, feedback_raw.name)
    issues += fv.issues
    if not fv.ok:
        return BuildResult(None, issues, ov.name_conflicts)
    confirmed = confirmed_names or {}
    unresolved = [pid for pid in ov.name_conflicts if pid not in confirmed]
    if unresolved:
        return BuildResult(None, issues, ov.name_conflicts)
    display = {}
    for pid, group in ov.orders.groupby("product_id", sort=True):
        display[str(pid)] = confirmed.get(str(pid), group["product_name"].iloc[0])
    if currency not in CURRENCIES:
        issues.append(ValidationIssue("error", "currency", f"Unsupported currency {currency}.", "settings"))
        return BuildResult(None, issues, ov.name_conflicts)
    dataset = Dataset(
        orders=ov.orders,
        feedback=fv.feedback,
        raw_orders=orders_raw.frame,
        raw_feedback=feedback_raw.frame,
        currency=currency,
        digest=dataset_digest(ov.orders, fv.feedback, currency),
        feedback_digest=feedback_digest(fv.feedback),
        sample_name=sample_name,
        file_info={
            orders_raw.name: orders_raw.size_bytes,
            feedback_raw.name: feedback_raw.size_bytes,
        },
        issues=issues,
        display_names=display,
    )
    return BuildResult(dataset, issues, ov.name_conflicts)


SAMPLE_CURRENCY = {"demo": "INR", "review_cases": "USD"}


def load_sample(name: str, limits: Limits | None = None):
    """Load a shipped synthetic sample dataset through the normal validation path."""
    from .config import DEMO_DIR, REVIEW_CASES_DIR

    folder = {"demo": DEMO_DIR, "review_cases": REVIEW_CASES_DIR}[name]
    limits = limits or Limits()
    o = read_csv_bytes((folder / "orders.csv").read_bytes(), "orders.csv", limits)
    f = read_csv_bytes((folder / "feedback.csv").read_bytes(), "feedback.csv", limits)
    result = build_dataset(
        o,
        f,
        {k: k for k in ORDER_FIELDS},
        {k: k for k in FEEDBACK_FIELDS},
        SAMPLE_CURRENCY[name],
        limits,
        sample_name=name,
    )
    if not result.ok:  # pragma: no cover - shipped files are validated by tests
        raise RuntimeError(f"Shipped sample '{name}' failed validation: {result.issues}")
    return result.dataset
