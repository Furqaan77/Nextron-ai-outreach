"""The AI Test Sets' files (FR-73, FR-21, FR-40, AD-12, AD-17, AD-18, NFR-26, NFR-27, Story 4.6).

- **Every file parses.** Each ``tests/ai_sets/<set>/*.json`` file is read by its set's parser
  (``nextron.domain.ai_test_sets.set_case``, or ``extraction_case`` for Epic 2's fixtures), the
  shipped sets are at least the spec's sizes, and every case key is unique within its set.
- **Synthetic content only (the guard).** Every file under ``tests/ai_sets`` is scanned (each
  JSON string, keys included): an email address's domain must be ``example.com`` or
  ``example.co.uk`` or a subdomain of one; so must a URL's host (``http://``, ``https://`` or
  ``www.``, decision 9); and every run of 7 or more digits that isn't a date must be a
  fictional number (UK Ofcom drama numbers: 01632 960xxx, 020 7946 0xxx, 01xx 496 0xxx, 07700
  900xxx, also with +44; US: NXX 555-01xx, also with +1), whatever spaces, slashes or
  no-break spaces split it. Every bare domain name (any ``label.tld``) is a reserved one too;
  only Epic 2's extraction fixtures may also use reserved ``.test`` names. Self-tests prove
  each check catches a planted real value and passes a reserved one.
- **The seeds.** Each Segment, Market and Signal type a case names exists in ``seed/``; a
  Signal's tier is its type's tier, and it carries ``observed_on`` exactly when its type is an
  Event. Each fixture fact (a case's ``facts``) equals a ``seed/company_facts.yaml`` fact at
  version 1, with the same wording, allowed for the case's Segment and Market. A drafting-smoke
  case expects its strongest Event (Tier first, then the newest), as Story 4.13's
  ``wrong_signal`` rule does, and at least 5 cases list it after another Signal, so "the first
  Signal listed" never passes the set.
- **Coverage.** ``coverage_gaps`` finds nothing missing in the Reply, Grounding and drafting
  smoke sets (the drafting pairs are every seeded Segment with every seeded Market).
"""

import json
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, Final

import pytest

from nextron.domain import seed_yaml
from nextron.domain.ai_test_cases import TestCase, extraction_case
from nextron.domain.ai_test_gate import MIN_CASES, TestSet
from nextron.domain.ai_test_sets import coverage_gaps, is_event, set_case

ENGINE: Final = Path(__file__).resolve().parents[2]
AI_SETS: Final = ENGINE / "tests" / "ai_sets"
SEED: Final = ENGINE / "seed"
NEW_SETS: Final = (TestSet.REPLY, TestSet.GROUNDING, TestSet.DRAFTING_SMOKE)
SHIPPED_SIZES: Final = {
    TestSet.EXTRACTION: 11,
    TestSet.REPLY: 50,
    TestSet.GROUNDING: 30,
    TestSet.DRAFTING_SMOKE: 10,
}
RESERVED_DOMAINS: Final = ("example.com", "example.co.uk")

EMAIL: Final = re.compile(r"[A-Za-z0-9._%+'-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
URL: Final = re.compile(r"\b(?:https?://|www\.)([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)", re.IGNORECASE)
BARE_DOMAIN: Final = re.compile(r"(?<![@\w.-])((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![\w@-])")
TEST_TLD: Final = ".test"  # RFC 2606: Epic 2's extraction fixtures use it
PHONE: Final = re.compile("\\+?\\(?\\d[\\d \\t().\\-/\u00a0]{5,}\\d")
MIN_NOT_FIRST: Final = 5
DATES: Final = (
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{4}[/.]\d{1,2}[/.]\d{1,2}"),
    re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"),
)
FICTIONAL_UK: Final = (
    re.compile(r"01632960\d{3}"),
    re.compile(r"02079460\d{3}"),
    re.compile(r"01\d{2}4960\d{3}"),
    re.compile(r"07700900\d{3}"),
)
FICTIONAL_US: Final = re.compile(r"[2-9]\d{2}55501\d{2}")
MIN_PHONE_DIGITS: Final = 7


# The guard --------------------------------------------------------------------------------------


def reserved(domain: str) -> bool:
    """Whether ``domain`` is a reserved example domain or a subdomain of one."""
    found = domain.lower().rstrip(".")
    return any(found == chosen or found.endswith("." + chosen) for chosen in RESERVED_DOMAINS)


def bad_emails(text: str) -> list[str]:
    """The email addresses in ``text`` whose domain isn't reserved."""
    return [match.group(0) for match in EMAIL.finditer(text) if not reserved(match.group(1))]


def bad_urls(text: str) -> list[str]:
    """The URLs in ``text`` whose host isn't reserved."""
    return [match.group(0) for match in URL.finditer(text) if not reserved(match.group(1))]


def bad_domains(text: str, *, test_names: bool = False) -> list[str]:
    """The bare domain names in ``text`` that aren't reserved (an email's domain and local part
    are checked as an email); ``.test`` names too unless ``test_names``."""
    return [
        match.group(1)
        for match in BARE_DOMAIN.finditer(text)
        if not reserved(match.group(1))
        and not (test_names and match.group(1).lower().endswith(TEST_TLD))
    ]


def fictional(digits: str) -> bool:
    """Whether the digits of a phone number are a fictional UK or US number."""
    uk = "0" + digits[2:] if digits.startswith("44") else digits
    if any(pattern.fullmatch(uk) for pattern in FICTIONAL_UK):
        return True
    us = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    return FICTIONAL_US.fullmatch(us) is not None


def bad_phones(text: str) -> list[str]:
    """The runs of 7 or more digits in ``text`` that aren't a date or a fictional number."""
    found = []
    for match in PHONE.finditer(text):
        run = match.group(0).strip()
        digits = re.sub(r"\D", "", run)
        if len(digits) < MIN_PHONE_DIGITS or any(pattern.fullmatch(run) for pattern in DATES):
            continue
        if not fictional(digits):
            found.append(run)
    return found


def strings(value: object) -> Iterator[str]:
    """Every string of a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def every_file() -> list[Path]:
    return sorted(path for path in AI_SETS.rglob("*") if path.is_file())


def texts_of(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    try:
        return list(strings(json.loads(raw)))
    except ValueError:
        return [raw]


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("Write to jane@acme.com today", ["jane@acme.com"]),
        ("Write to jane@example.com.evil.io", ["jane@example.com.evil.io"]),
        ("Write to jane@notexample.com", ["jane@notexample.com"]),
        ("Write to jane@example.com or tom@mail.example.co.uk", []),
    ],
)
def test_the_email_guard_catches_a_real_address(text: str, bad: list[str]) -> None:
    assert bad_emails(text) == bad


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("See https://acme.com/team", ["https://acme.com"]),
        ("See www.acme.co.uk", ["www.acme.co.uk"]),
        ("See http://example.com.acme.io/x", ["http://example.com.acme.io"]),
        ("See https://www.example.co.uk/x and https://cal.example.com/furqaan", []),
    ],
)
def test_the_url_guard_catches_a_real_host(text: str, bad: list[str]) -> None:
    assert bad_urls(text) == bad


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("Visit acme-law.com for more", ["acme-law.com"]),
        ("Visit brightwell.co.uk for more", ["brightwell.co.uk"]),
        (
            "Visit acme.de, hale.ca, shop.ie or brightwell.lawyer",
            ["acme.de", "hale.ca", "shop.ie", "brightwell.lawyer"],
        ),
        ("Visit marshcole.test", ["marshcole.test"]),
        ("Visit brightwell.example.co.uk or hale.example.com", []),
        ("Write to jane.doe@example.com", []),
    ],
)
def test_the_domain_guard_catches_a_real_domain(text: str, bad: list[str]) -> None:
    assert bad_domains(text) == bad


def test_only_the_extraction_fixtures_may_use_test_names() -> None:
    assert bad_domains("Visit marshcole.test or acme.de", test_names=True) == ["acme.de"]


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("Call 020 7123 4567", ["020 7123 4567"]),
        ("Call (415) 555-1234", ["(415) 555-1234"]),
        ("Call +44 7911 123456", ["+44 7911 123456"]),
        ("Call 020/7123/4567", ["020/7123/4567"]),
        ("Call 020\u00a07123\u00a04567", ["020\u00a07123\u00a04567"]),
        ("Call 020\u00a07946\u00a00123 or 0161/496/0000", []),
        ("Call 0161 496 0000, 01632 960123, 020 7946 0999 or 07700 900456", []),
        ("Call +44 20 7946 0123 or +44 7700 900123", []),
        ("Call (415) 555-0142, 212-555-0199 or +1 646 555 0100", []),
        ("Back on 2026-11-23, or 23/11/2026", []),
        ("Order 12345 for 2,500 units, Opinion 512", []),
    ],
)
def test_the_phone_guard_catches_a_real_number(text: str, bad: list[str]) -> None:
    assert bad_phones(text) == bad


def test_no_case_file_holds_a_real_email_url_or_phone_number() -> None:
    problems = []
    for path in every_file():
        name = path.relative_to(AI_SETS).as_posix()
        for text in texts_of(path):
            problems += [f"{name}: email {value}" for value in bad_emails(text)]
            problems += [f"{name}: URL {value}" for value in bad_urls(text)]
            problems += [f"{name}: phone {value}" for value in bad_phones(text)]
            extraction = name.startswith(TestSet.EXTRACTION.value + "/")
            problems += [
                f"{name}: domain {value}" for value in bad_domains(text, test_names=extraction)
            ]

    assert problems == []


# The files ---------------------------------------------------------------------------------------


def load(test_set: TestSet) -> list[TestCase]:
    folder = AI_SETS / test_set.value
    cases = []
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if test_set is TestSet.EXTRACTION:
            cases.append(extraction_case(path.name, data))
        else:
            cases.append(set_case(test_set, path.name, data))
    return cases


@pytest.fixture(scope="module")
def sets() -> dict[TestSet, list[TestCase]]:
    return {test_set: load(test_set) for test_set in TestSet}


@pytest.mark.parametrize("test_set", list(TestSet))
def test_every_shipped_set_parses_and_is_big_enough(
    sets: dict[TestSet, list[TestCase]], test_set: TestSet
) -> None:
    cases = sets[test_set]
    keys = [case.case_key for case in cases]

    assert len(cases) >= max(SHIPPED_SIZES[test_set], MIN_CASES[test_set])
    assert len(set(keys)) == len(keys)
    assert not [path for path in (AI_SETS / test_set.value).iterdir() if path.suffix != ".json"]


def test_the_ai_sets_folder_holds_only_the_four_sets() -> None:
    assert sorted(path.name for path in AI_SETS.iterdir()) == sorted(s.value for s in TestSet)


# The seeds ---------------------------------------------------------------------------------------


def seed(name: str) -> Any:
    return seed_yaml.parse((SEED / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def signal_types() -> dict[str, dict[str, dict[str, Any]]]:
    """Segment key -> Signal type key -> its seed entry."""
    return {
        segment["key"]: {entry["key"]: entry for entry in segment["signal_types"]}
        for segment in seed("segments.yaml")["segments"]
    }


@pytest.fixture(scope="module")
def market_codes() -> list[str]:
    return [market["code"] for market in seed("markets.yaml")["markets"]]


@pytest.fixture(scope="module")
def seed_facts() -> dict[str, dict[str, Any]]:
    return {fact["fact_id"]: fact for fact in seed("company_facts.yaml")["company_facts"]}


def seeded(sets: dict[TestSet, list[TestCase]]) -> Iterator[TestCase]:
    for test_set in (TestSet.GROUNDING, TestSet.DRAFTING_SMOKE):
        yield from sets[test_set]


def test_every_segment_market_and_signal_type_is_seeded(
    sets: dict[TestSet, list[TestCase]],
    signal_types: dict[str, dict[str, dict[str, Any]]],
    market_codes: list[str],
) -> None:
    problems = []
    for case in seeded(sets):
        where = f"{case.test_set.value}/{case.case_key}"
        segment = case.input["segment"]
        if segment not in signal_types:
            problems.append(f"{where}: Segment {segment} isn't seeded")
            continue
        if case.input["market"] not in market_codes:
            problems.append(f"{where}: Market {case.input['market']} isn't seeded")
        for signal in case.input["signals"]:
            entry = signal_types[segment].get(signal["signal_type"])
            if entry is None:
                problems.append(f"{where}: {signal['id']} has no seeded type in {segment}")
                continue
            if signal["tier"] != entry["tier"]:
                problems.append(f"{where}: {signal['id']} isn't Tier {entry['tier']}")
            if (signal["observed_on"] is not None) != (entry["class"] == "event"):
                problems.append(f"{where}: {signal['id']} is an {entry['class']}: date it so")

    assert problems == []


def test_every_fixture_fact_is_a_seeded_fact_allowed_for_the_case(
    sets: dict[TestSet, list[TestCase]], seed_facts: dict[str, dict[str, Any]]
) -> None:
    problems = []
    for case in seeded(sets):
        where = f"{case.test_set.value}/{case.case_key}"
        for fact in case.input["facts"]:
            entry = seed_facts.get(fact["fact_id"])
            if entry is None or fact["version"] != 1 or fact["wording"] != entry["wording"]:
                problems.append(f"{where}: {fact['fact_id']} isn't seed version 1, word for word")
            elif case.input["segment"] not in entry["segments"]:
                problems.append(f"{where}: {fact['fact_id']} isn't allowed for the Segment")
            elif case.input["market"] not in entry["markets"]:
                problems.append(f"{where}: {fact['fact_id']} isn't allowed for the Market")

    assert problems == []


def test_a_drafting_case_expects_its_strongest_event(
    sets: dict[TestSet, list[TestCase]],
) -> None:
    problems = []
    for case in sets[TestSet.DRAFTING_SMOKE]:
        events = [signal for signal in case.input["signals"] if is_event(signal)]
        ranked = sorted(
            events, key=lambda s: (s["tier"], -date.fromisoformat(s["observed_on"]).toordinal())
        )
        strongest = ranked[0]
        tied = [
            s
            for s in ranked
            if (s["tier"], s["observed_on"]) == (strongest["tier"], strongest["observed_on"])
        ]
        if len(tied) > 1 or case.expected["cited_signal_id"] != strongest["id"]:
            problems.append(f"{case.case_key}: expect the one strongest Event, {strongest['id']}")

    assert problems == []


def test_the_drafting_set_doesnt_always_expect_the_first_signal_listed(
    sets: dict[TestSet, list[TestCase]],
) -> None:
    not_first = [
        case
        for case in sets[TestSet.DRAFTING_SMOKE]
        if case.expected["cited_signal_id"] != case.input["signals"][0]["id"]
    ]

    assert len(not_first) >= MIN_NOT_FIRST


# Coverage ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("test_set", NEW_SETS)
def test_each_set_covers_what_the_gate_measures(
    sets: dict[TestSet, list[TestCase]],
    signal_types: dict[str, dict[str, dict[str, Any]]],
    market_codes: list[str],
    test_set: TestSet,
) -> None:
    gaps = coverage_gaps(
        test_set, sets[test_set], segments=sorted(signal_types), markets=market_codes
    )

    assert gaps == ()
