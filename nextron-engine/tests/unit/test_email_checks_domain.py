"""``nextron.domain.email_checks`` (Story 2.14): the vendored disposable list (its source pin
and SHA-256), subdomain matching, the MX rule, status normalising and the codes; and
``nextron.domain.email_finding.address_quote``, the find-on-site quote."""

import hashlib
from datetime import UTC, datetime

import pytest

from nextron.domain import email_checks
from nextron.domain.budget import QUOTAS, BudgetClass
from nextron.domain.email_checks import (
    DISPOSABLE_COMMIT,
    DISPOSABLE_SHA256,
    REASON_TEXT,
    CheckResult,
    InvalidReason,
    disposable_domains,
    is_disposable,
    mx_reason,
    normalise_status,
)
from nextron.domain.email_finding import address_quote
from nextron.ports.dns import MxRecord
from tests.contracts.support import PACKAGE

LIST = PACKAGE / "domain" / "data" / "disposable_domains.txt"
SOURCE = "https://github.com/disposable-email-domains/disposable-email-domains"


def header_and_body() -> tuple[list[str], bytes]:
    data = LIST.read_bytes()
    lines = data.split(b"\n")
    header = [line.decode() for line in lines if line.startswith(b"#")]
    body = data[sum(len(line) + 1 for line in lines[: len(header)]) :]
    return header, body


def test_the_list_below_its_header_is_the_pinned_upstream_file() -> None:
    header, body = header_and_body()

    assert hashlib.sha256(body).hexdigest() == DISPOSABLE_SHA256
    assert f"# Source: {SOURCE}" in header
    assert f"# Commit: {DISPOSABLE_COMMIT}" in header
    assert f"# SHA-256 of the list below this header: {DISPOSABLE_SHA256}" in header
    assert any("CC0" in line for line in header)
    assert DISPOSABLE_COMMIT == "f4cad16eb9fa2cd9ceb352ff5a8c213a25a10083"
    assert b"\r" not in body and not body.startswith(b"#")


def test_the_list_is_read_without_its_header() -> None:
    domains = disposable_domains()

    assert "mailinator.com" in domains
    assert len(domains) > 10_000 - 1_000
    assert not any(domain.startswith("#") or not domain for domain in domains)
    assert all(domain == domain.lower().strip() for domain in domains)


@pytest.mark.parametrize(
    ("domain", "listed"),
    [
        ("mailinator.com", True),
        ("MAILINATOR.COM.", True),
        ("eu.mailinator.com", True),  # its registrable domain is listed
        ("deep.eu.mailinator.com", True),
        ("smith-law.com", False),
        ("mailinator.com.example", False),
        ("xmailinatorq.com", False),
        ("localhost", False),  # not a registrable domain: only itself counts
    ],
)
def test_is_disposable_matches_the_domain_or_its_registrable_domain(
    domain: str, listed: bool
) -> None:
    assert is_disposable(domain) is listed


def test_is_disposable_refuses_a_non_str() -> None:
    with pytest.raises(TypeError):
        is_disposable(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        ((), InvalidReason.NO_MX),
        ((MxRecord(0, ""),), InvalidReason.NULL_MX),
        ((MxRecord(0, ""), MxRecord(5, "")), InvalidReason.NULL_MX),
        ((MxRecord(0, ""), MxRecord(10, "mx.example")), None),
        ((MxRecord(10, "mx.example"),), None),
    ],
)
def test_mx_reason(records: tuple[MxRecord, ...], reason: InvalidReason | None) -> None:
    assert mx_reason(records) == reason


@pytest.mark.parametrize(
    ("status", "stored"),
    [
        ("valid", "valid"),
        ("invalid", "invalid"),
        ("accept_all", "accept_all"),
        (" Webmail ", "webmail"),
        ("unknown", "unknown"),
        ("accept-all", "other"),
        ("x" * 33, "other"),
        ("x" * 32, "x" * 32),
        ("", "other"),
        ("vålid", "other"),
        ("checks_passed", "other"),  # a result code is never a status
        ("suppressed", "other"),
        ("quota_reached", "other"),
    ],
)
def test_normalise_status(status: str, stored: str) -> None:
    assert normalise_status(status) == stored


def test_normalise_status_refuses_a_non_str() -> None:
    with pytest.raises(TypeError):
        normalise_status(None)  # type: ignore[arg-type]


def test_the_codes_and_constants() -> None:
    assert [str(result) for result in CheckResult] == [
        "checks_passed",
        "invalid",
        "suppressed",
        "quota_reached",
    ]
    assert [str(reason) for reason in InvalidReason] == [
        "syntax",
        "disposable",
        "no_mx",
        "null_mx",
        "verifier_invalid",
    ]
    assert set(REASON_TEXT) == set(InvalidReason)
    assert email_checks.VERIFY_CONTACT_EMAIL_JOB == "verify_contact_email"
    assert email_checks.BUDGET_CLASS is BudgetClass.BLOCKABLE
    assert email_checks.HUNTER_QUOTA in {quota.name for quota in QUOTAS}
    assert email_checks.VERIFIER_DETAIL == "Hunter Email Verifier"
    assert all(len(str(code)) <= 32 for code in (*CheckResult, *InvalidReason))


# address_quote --------------------------------------------------------------------------------

AT = datetime(2026, 11, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "mailto", "quote"),
    [
        ("Jane Roe\nWrite to jane@roe.law today\nOther", (), "Write to jane@roe.law today"),
        ("Write to JANE@Roe.Law today", (), "Write to JANE@Roe.Law today"),
        ("Write to xjane@roe.law", (), None),
        ("Write to jane@roe.law.uk", (), None),
        (None, ("jane@roe.law",), "mailto:jane@roe.law"),
        ("No address here", ("JANE@roe.law",), "mailto:jane@roe.law"),
        ("Write to jane@roe.law", ("jane@roe.law",), "Write to jane@roe.law"),
        (None, (), None),
    ],
)
def test_address_quote(text: str | None, mailto: tuple[str, ...], quote: str | None) -> None:
    assert address_quote("jane@roe.law", text, mailto) == quote


def test_address_quote_windows_a_long_line() -> None:
    text = "a " * 400 + "jane@roe.law" + " b" * 400

    quote = address_quote("jane@roe.law", text, ())

    assert quote is not None and "jane@roe.law" in quote and len(quote) <= 300
