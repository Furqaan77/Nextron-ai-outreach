"""The Reply, Grounding and drafting-smoke AI Test Sets (FR-73, FR-21, FR-40, AD-12, AD-18,
NFR-26, NFR-27, Story 4.6). Standard library only.

Each set's cases are synthetic files under ``tests/ai_sets/<set>/``, one case per file, loaded
at every ``serve`` start with Epic 2's extraction fixtures (``nextron.main.load_test_cases``).
This module holds the vocabularies the cases are labelled with, the parser that validates each
file, and the coverage rules that name what a set is missing.

**The vocabularies.**

- Reply: ``nextron.domain.reply_classes.ReplyClass`` and ``ObjectionType`` (decision 2).
- Grounding: ``GroundingPattern``, the 15 banned patterns a case can plant (Sales Playbook §4:
  ``deceptive_subject``, ``re_fwd_first_email``, ``invented_mutual_connection``,
  ``competitor_claim``, ``unapproved_client_claim``, ``unsourced_statistic``,
  ``pressure_tactic``; FR-21: ``fake_familiarity``, ``false_urgency``, ``attachment``,
  ``link_in_step_1``, ``two_calls_to_action``, ``just_following_up``, ``repeats_earlier_step``,
  ``unhedged_absence_claim``), and ``ClaimCode``, the 4 planted unsupported claims
  (``unsupported_lead_claim``, ``unsupported_nextron_claim``, ``regulation_wrong_market``,
  ``unsupported_regulation_claim``). ``GROUNDING_RULE_IDS`` are Story 4.13's 14 deterministic
  rule ids; a case names the ones expected to fire (decision 4).
- ``OPT_OUT_WORDINGS``: "no", "no thanks, remove me", "stop", "unsubscribe", "take me off your
  list" and "please don't email me again" (the AC's list). ``opt_out_key(text)`` is how the
  coverage rule compares a Reply with them: case-folded, punctuation dropped, whitespace
  collapsed.

**The case file.** ``set_case(test_set, name, data) -> TestCase`` reads the JSON ``data`` of
the file ``name`` of ``test_set`` (``reply``, ``grounding`` or ``drafting_smoke``; extraction
files are ``nextron.domain.ai_test_cases.extraction_case``'s). It raises ``InvalidTestCases``
with ``where`` ``<set>/<file>`` and a reason ``<path> <what is wrong>``, never quoting a case's
text. The file is an object with exactly ``id``, ``set``, ``route``, ``input``, ``expected`` and
``covers``:

- ``id`` is the file's stem, fullmatches ``CASE_KEY_PATTERN`` and never starts with
  ``override-`` (``OVERRIDE_PREFIX``, the Operator's override cases);
- ``set`` is the folder's set and ``route`` the set's case route (``CASE_ROUTES``):
  ``classify_reply``, ``grounding_check`` or ``draft_sequence``;
- every text is a non-empty string that encodes as UTF-8; every date is ISO ``YYYY-MM-DD``;
- every block and Evidence item has a ``source`` and ``id`` that ``UntrustedBlock`` accepts
  (``nextron.domain.ai_test_cases.check_block_fields``); the ids of a case's blocks, Evidence
  and Signals are unique within it and never ``signal_types``; an id a case refers to (a
  claim's or Signal's ``evidence_id``, a ``cited_signal_id``) must be one of its ids, so a JSON
  list or object there is refused with the file named;
- ``covers`` is a list of distinct tags: each derived tag of the case (below) must be there, and
  every other tag must be an optional tag of the set whose condition the case meets. A tag of
  the derived kinds that doesn't match the case (``class:question`` on an Interested Reply, say)
  is refused.

**Reply cases** (``classify_reply``).

- ``input`` is ``{received_on, blocks}``: 1 to 3 blocks of ``{id, source, text}``, each
  ``source`` ``reply`` or ``sent_email`` (the email being answered), exactly one a ``reply``.
- ``expected`` has exactly ``reply_class``; ``objection_type`` (set if and only if the class is
  ``objection``); ``return_date`` (only ``out_of_office`` may set it); ``recontact_date`` (only
  ``not_now``), each date on or after ``received_on``; and ``referral`` (set if and only if the
  class is ``referral``: ``{name, role, email, referrer_may_be_named}``, ``role`` and ``email``
  ``null`` or text, the email an address, the last a ``bool``).
- Derived tags: ``class:<class>``; ``objection:<type>``; ``return_date`` and ``recontact_date``
  when set; ``referral:may_name`` or ``referral:may_not_name``.
- Optional tags: ``opt_out_wording``, ``opt_out_in_auto_reply``, ``firm_wide``, ``hostile`` and
  ``injection:opt_out_with_instructions`` (an Unsubscribe Reply only); ``near_opt_out`` (a Reply
  of any other class whose words look like an opt-out: a negative control, so a keyword
  classifier can't score perfectly); ``injection:dictated_class``.

**Grounding cases** (``grounding_check``): one Draft revision to check.

- ``input`` is ``{segment, market, step, draft, claims, cited_signal_id, signals, evidence,
  facts, earlier_steps, settings}``: ``market`` ``us`` or ``uk``; ``step`` 1 to 4; ``draft``
  ``{subject, body}``; a claim ``{text, evidence_id, fact}`` (``text`` a substring of the
  subject or body, ``evidence_id`` ``null`` or an Evidence id, ``fact`` ``null`` or ``{fact_id,
  version}``); a Signal ``{id, signal_type, tier (1-3), evidence_id, observed_on}``
  (``observed_on`` ``null`` or a date); Evidence ``{id, source, kind (quote|absence), text}``; a
  fact ``{fact_id, version, wording}``; an earlier Step ``{step, subject, body, claims,
  cited_signal_id}`` with a ``step`` before the case's (each once), its claims checked against
  its own text; ``cited_signal_id`` ``null`` or a Signal id; ``settings`` holding some of
  ``sender_signature``, ``booking_link`` and ``privacy_notice_url`` as text.
- ``expected`` is ``{kind, findings, rules}``: ``kind`` a
  ``nextron.domain.ai_test_gate.GroundingCase``; ``findings`` ``{code, span}`` with each
  ``span`` a substring of the subject or body; ``rules`` distinct ``GROUNDING_RULE_IDS``. A
  ``clean_control`` has no finding and no rule; a ``banned_pattern`` has at least one finding,
  every code a ``GroundingPattern``; a ``planted_claim`` at least one, every code a
  ``ClaimCode``.
- Derived tags: ``pattern:<code>`` per banned pattern; ``claim:lead``, ``claim:nextron`` or
  ``claim:regulation`` per planted claim (``CLAIM_TAGS``); ``control:clean``; ``segment:<s>``,
  ``market:<m>`` and ``step:<n>``.
- Optional tags, each checked against the Draft's subject and body (``VARIANT_RULES``):
  ``variant:re`` and ``variant:fwd`` (the subject starts "Re:" or "Fwd:", in any case),
  ``variant:multiplier`` (a "4-8x"-style number), ``variant:last_chance`` ("last chance"),
  ``variant:fake_deadline`` (a ``pressure_tactic`` finding), ``variant:law_firm`` ("law
  firm"), ``variant:distributor`` ("distribut"), ``variant:aba_to_uk`` (a ``uk`` case that
  mentions "512") and ``variant:sra_to_us`` (a ``us`` case naming the SRA notice,
  ``nextron.domain.segments.mentions_sra_notice``); the injection tags
  ``injection:grader_instruction`` (some Evidence text names the grader or the grounding check,
  ``GRADER_PATTERN``) and ``injection:hidden_instructions``.

**Drafting-smoke cases** (``draft_sequence``): one Lead's Step 1 (decision 5).

- ``input`` is ``{segment, market, lead_name, contact {name, role}, angle_note, signals,
  evidence, facts}`` (``angle_note`` ``null`` or text), Signals, Evidence and facts shaped as in
  a Grounding case, with at least one Evidence item, one fact and one Event (a Tier 1 or 2
  Signal with an ``observed_on``).
- ``expected`` is ``{cited_signal_id}``: an Event among the case's Signals.
- Derived tags: ``segment:<s>`` and ``market:<m>``. Optional: ``injection:hidden_instructions``
  and ``injection:grader_instruction`` (checked as in a Grounding case).

**Coverage.** ``coverage_gaps(test_set, cases, *, segments=(), markets=()) -> tuple[str,
...]`` names, readably and in a fixed order, what the set's ``cases`` (those of ``test_set``
and its case route; ``TypeError`` for an item that isn't a ``TestCase``) are missing; empty
when nothing is:

- Reply: ``MIN_CASES`` (50) cases; every ``class:*`` and ``objection:*`` tag; at least 10
  ``opt_out_wording`` cases with distinct Reply texts, among which each ``OPT_OUT_WORDINGS``
  entry appears (by ``opt_out_key``; gap ``opt-out wording "stop"``), and at least one
  all-lowercase text, one with a capital, one with punctuation and one without; 2
  ``return_date`` cases; a ``recontact_date``, ``opt_out_in_auto_reply``, ``firm_wide`` and
  ``hostile`` case each; 2 ``near_opt_out`` cases; both referral tags; both Reply injection
  tags.
- Grounding: 30 cases; every ``pattern:*``; the variants ``re``, ``fwd``, ``multiplier``,
  ``last_chance``, ``fake_deadline``, ``law_firm``, ``distributor``, ``aba_to_uk`` and
  ``sra_to_us``; the three
  ``claim:*`` tags; 2 ``control:clean`` cases; both Grounding injection tags.
- Drafting smoke: 10 cases; a case for every pair of ``segments`` and ``markets`` (gap
  ``segment:<s> with market:<m>``; both are required, ``ValueError`` when either is empty);
  both drafting injection tags.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import PurePath
from types import MappingProxyType
from typing import Any, Final, NoReturn

from nextron.domain.ai import BLOCK_ID_PATTERN
from nextron.domain.ai_test_cases import (
    CASE_KEY_PATTERN,
    COVER_PATTERN,
    OVERRIDE_PREFIX,
    SIGNAL_TYPES,
    InvalidTestCases,
    TestCase,
    check_block_fields,
)
from nextron.domain.ai_test_gate import MIN_CASES, GroundingCase, TestSet
from nextron.domain.company_facts import FACT_ID_PATTERN
from nextron.domain.reply_classes import ObjectionType, ReplyClass
from nextron.domain.segments import mentions_sra_notice

CLASSIFY_REPLY: Final = "classify_reply"
GROUNDING_CHECK: Final = "grounding_check"
DRAFT_SEQUENCE: Final = "draft_sequence"
CASE_ROUTES: Final[Mapping[TestSet, str]] = MappingProxyType(
    {
        TestSet.REPLY: CLASSIFY_REPLY,
        TestSet.GROUNDING: GROUNDING_CHECK,
        TestSet.DRAFTING_SMOKE: DRAFT_SEQUENCE,
    }
)
ENVELOPE_FIELDS: Final = frozenset({"id", "set", "route", "input", "expected", "covers"})
DATE_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")  # a Segment or Signal type key
EMAIL_PATTERN: Final = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
GRADER_PATTERN: Final = re.compile(r"\bgrader\b|\bgrounding check", re.IGNORECASE)
MULTIPLIER_PATTERN: Final = re.compile(
    r"\b\d+(?:\.\d+)?(?:\s*(?:[-–—]|to)\s*\d+(?:\.\d+)?)?\s?[x×](?![a-z])",
    re.IGNORECASE,
)
REGULATION_512: Final = re.compile(r"\b512\b")
MARKETS: Final = ("us", "uk")
FIRST_STEP: Final = 1
LAST_STEP: Final = 4
TOP_TIER: Final = 1
LAST_TIER: Final = 3
LAST_EVENT_TIER: Final = 2
MAX_REPLY_BLOCKS: Final = 3
REPLY_SOURCE: Final = "reply"
SENT_EMAIL_SOURCE: Final = "sent_email"
SETTING_KEYS: Final = ("sender_signature", "booking_link", "privacy_notice_url")
MIN_OPT_OUT_TEXTS: Final = 10
MIN_RETURN_DATES: Final = 2
MIN_CLEAN_CONTROLS: Final = 2

OPT_OUT_WORDINGS: Final = (
    "no",
    "no thanks, remove me",
    "stop",
    "unsubscribe",
    "take me off your list",
    "please don't email me again",
)


class GroundingPattern(StrEnum):
    """A banned pattern a Grounding case plants (Sales Playbook §4 and FR-21)."""

    DECEPTIVE_SUBJECT = "deceptive_subject"
    RE_FWD_FIRST_EMAIL = "re_fwd_first_email"
    INVENTED_MUTUAL_CONNECTION = "invented_mutual_connection"
    COMPETITOR_CLAIM = "competitor_claim"
    UNAPPROVED_CLIENT_CLAIM = "unapproved_client_claim"
    UNSOURCED_STATISTIC = "unsourced_statistic"
    PRESSURE_TACTIC = "pressure_tactic"
    FAKE_FAMILIARITY = "fake_familiarity"
    FALSE_URGENCY = "false_urgency"
    ATTACHMENT = "attachment"
    LINK_IN_STEP_1 = "link_in_step_1"
    TWO_CALLS_TO_ACTION = "two_calls_to_action"
    JUST_FOLLOWING_UP = "just_following_up"
    REPEATS_EARLIER_STEP = "repeats_earlier_step"
    UNHEDGED_ABSENCE_CLAIM = "unhedged_absence_claim"


class ClaimCode(StrEnum):
    """An unsupported claim a Grounding case plants (FR-21)."""

    UNSUPPORTED_LEAD_CLAIM = "unsupported_lead_claim"
    UNSUPPORTED_NEXTRON_CLAIM = "unsupported_nextron_claim"
    REGULATION_WRONG_MARKET = "regulation_wrong_market"
    UNSUPPORTED_REGULATION_CLAIM = "unsupported_regulation_claim"


class EvidenceKind(StrEnum):
    """What an Evidence item of a case holds: a quote, or an observed absence."""

    QUOTE = "quote"
    ABSENCE = "absence"


CLAIM_TAGS: Final[Mapping[ClaimCode, str]] = MappingProxyType(
    {
        ClaimCode.UNSUPPORTED_LEAD_CLAIM: "claim:lead",
        ClaimCode.UNSUPPORTED_NEXTRON_CLAIM: "claim:nextron",
        ClaimCode.REGULATION_WRONG_MARKET: "claim:regulation",
        ClaimCode.UNSUPPORTED_REGULATION_CLAIM: "claim:regulation",
    }
)

# Story 4.13's deterministic rule ids (its AC lists 14).
GROUNDING_RULE_IDS: Final = (
    "word_limit",
    "subject_rule",
    "link_in_step1",
    "unapproved_contact_detail",
    "banned_phrase",
    "attachment",
    "price_or_audit_in_step",
    "multiple_ctas",
    "citation_outside_set",
    "unhedged_absence",
    "signal_count",
    "wrong_signal",
    "score_only_cited",
    "repeated_angle",
)

# The tags --------------------------------------------------------------------------------------

OPT_OUT_WORDING: Final = "opt_out_wording"
OPT_OUT_IN_AUTO_REPLY: Final = "opt_out_in_auto_reply"
FIRM_WIDE: Final = "firm_wide"
HOSTILE: Final = "hostile"
RETURN_DATE: Final = "return_date"
RECONTACT_DATE: Final = "recontact_date"
REFERRAL_MAY_NAME: Final = "referral:may_name"
REFERRAL_MAY_NOT_NAME: Final = "referral:may_not_name"
INJECTION_OPT_OUT: Final = "injection:opt_out_with_instructions"
INJECTION_DICTATED_CLASS: Final = "injection:dictated_class"
INJECTION_GRADER: Final = "injection:grader_instruction"
INJECTION_HIDDEN: Final = "injection:hidden_instructions"
CONTROL_CLEAN: Final = "control:clean"
UNSUBSCRIBE_ONLY: Final = (OPT_OUT_WORDING, OPT_OUT_IN_AUTO_REPLY, FIRM_WIDE, HOSTILE)
NEAR_OPT_OUT: Final = "near_opt_out"  # opt-out-like words in a Reply of another class
MIN_NEAR_OPT_OUTS: Final = 2
REQUIRED_VARIANTS: Final = (
    "re",
    "fwd",
    "multiplier",
    "last_chance",
    "fake_deadline",
    "law_firm",
    "distributor",
    "aba_to_uk",
    "sra_to_us",
)


def class_tag(reply_class: ReplyClass) -> str:
    """``class:<class>``."""
    return f"class:{reply_class.value}"


def objection_tag(objection: ObjectionType) -> str:
    """``objection:<type>``."""
    return f"objection:{objection.value}"


def pattern_tag(pattern: GroundingPattern) -> str:
    """``pattern:<code>``."""
    return f"pattern:{pattern.value}"


def variant_tag(name: str) -> str:
    """``variant:<name>``."""
    return f"variant:{name}"


def opt_out_key(text: str) -> str:
    """``text`` case-folded, with punctuation dropped and whitespace collapsed: how a Reply is
    compared with ``OPT_OUT_WORDINGS``."""
    kept = "".join(ch for ch in text.casefold() if ch.isalnum() or ch.isspace())
    return " ".join(kept.split())


def _has_punctuation(text: str) -> bool:
    return any(not ch.isalnum() and not ch.isspace() for ch in text)


# Reading a file --------------------------------------------------------------------------------


class _Reader:
    """Typed reads of one case file; every failure is an ``InvalidTestCases`` naming the file
    and the path."""

    def __init__(self, where: str) -> None:
        self.where = where

    def bad(self, path: str, reason: str) -> NoReturn:
        raise InvalidTestCases(self.where, f"{path} {reason}")

    def obj(self, value: object, path: str, names: Iterable[str]) -> dict[str, Any]:
        wanted = frozenset(names)
        if not isinstance(value, dict) or set(value) != wanted:
            self.bad(path, f"must be an object with exactly {sorted(wanted)}")
        return value

    def text(self, value: object, path: str) -> str:
        if not isinstance(value, str) or not value.strip():
            self.bad(path, "must be a non-empty string")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            self.bad(path, "must encode as UTF-8")
        return value

    def optional_text(self, value: object, path: str) -> str | None:
        return None if value is None else self.text(value, path)

    def pattern(self, value: object, path: str, pattern: re.Pattern[str]) -> str:
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            self.bad(path, f"must match {pattern.pattern}")
        return value

    def integer(self, value: object, path: str, low: int, high: int | None = None) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            self.bad(path, "must be a whole number")
        if value < low or (high is not None and value > high):
            self.bad(path, f"must be from {low}" + ("" if high is None else f" to {high}"))
        return value

    def flag(self, value: object, path: str) -> bool:
        if not isinstance(value, bool):
            self.bad(path, "must be true or false")
        return value

    def day(self, value: object, path: str) -> date:
        if not isinstance(value, str) or DATE_PATTERN.fullmatch(value) is None:
            self.bad(path, "must be a date written YYYY-MM-DD")
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.bad(path, "must be a date written YYYY-MM-DD")

    def optional_day(self, value: object, path: str) -> date | None:
        return None if value is None else self.day(value, path)

    def items(self, value: object, path: str) -> list[Any]:
        if not isinstance(value, list):
            self.bad(path, "must be a list")
        return value

    def choice[E: StrEnum](self, value: object, path: str, enum: type[E]) -> E:
        try:
            if not isinstance(value, str):
                raise ValueError
            return enum(value)
        except ValueError:
            self.bad(path, f"must be one of {[member.value for member in enum]}")


class _Ids:
    """The ids of one case's blocks, Evidence and Signals: unique, never ``signal_types``."""

    def __init__(self, reader: _Reader) -> None:
        self._reader = reader
        self._seen: set[str] = set()

    def add(self, value: str, path: str) -> None:
        if value == SIGNAL_TYPES or value in self._seen:
            self._reader.bad(path, f"must be unique within the case and never {SIGNAL_TYPES}")
        self._seen.add(value)


@dataclass(slots=True)
class _Parsed:
    """What a set's parser read: the case's ``input`` and ``expected``, its derived tags, and
    its set's optional tags, each ``None`` (allowed here) or why the case can't carry it."""

    input: dict[str, Any]
    expected: dict[str, Any]
    derived: list[str]
    optional: dict[str, str | None] = field(default_factory=dict)


def _block(reader: _Reader, ids: _Ids, value: object, path: str) -> dict[str, str]:
    found = reader.obj(value, path, ("id", "source", "text"))
    block_id = reader.text(found["id"], f"{path}.id")
    source = reader.text(found["source"], f"{path}.source")
    text = reader.text(found["text"], f"{path}.text")
    check_block_fields(source, block_id, text, reader.where, path)
    ids.add(block_id, f"{path}.id")
    return {"id": block_id, "source": source, "text": text}


# Reply cases -----------------------------------------------------------------------------------


def _referral(reader: _Reader, value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    path = "expected.referral"
    found = reader.obj(value, path, ("name", "role", "email", "referrer_may_be_named"))
    reader.text(found["name"], f"{path}.name")
    reader.optional_text(found["role"], f"{path}.role")
    email = reader.optional_text(found["email"], f"{path}.email")
    if email is not None and EMAIL_PATTERN.fullmatch(email) is None:
        reader.bad(f"{path}.email", "must be null or an email address")
    reader.flag(found["referrer_may_be_named"], f"{path}.referrer_may_be_named")
    return found


def _dated(
    reader: _Reader,
    value: object,
    path: str,
    received_on: date,
    reply_class: ReplyClass,
    owner: ReplyClass,
) -> date | None:
    found = reader.optional_day(value, path)
    if found is not None and reply_class is not owner:
        reader.bad(path, f"may be set only for the class {owner.value}")
    if found is not None and found < received_on:
        reader.bad(path, "must be on or after input.received_on")
    return found


def _reply(reader: _Reader, value: object, wanted: object) -> _Parsed:
    found = reader.obj(value, "input", ("received_on", "blocks"))
    received_on = reader.day(found["received_on"], "input.received_on")
    listed = reader.items(found["blocks"], "input.blocks")
    if not 1 <= len(listed) <= MAX_REPLY_BLOCKS:
        reader.bad("input.blocks", f"must hold 1 to {MAX_REPLY_BLOCKS} blocks")
    ids = _Ids(reader)
    blocks = [_block(reader, ids, item, f"input.blocks[{n}]") for n, item in enumerate(listed)]
    for index, block in enumerate(blocks):
        if block["source"] not in (REPLY_SOURCE, SENT_EMAIL_SOURCE):
            reader.bad(f"input.blocks[{index}].source", "must be reply or sent_email")
    if sum(1 for block in blocks if block["source"] == REPLY_SOURCE) != 1:
        reader.bad("input.blocks", "must hold exactly one reply block")
    expected = reader.obj(
        wanted,
        "expected",
        ("reply_class", "objection_type", "return_date", "recontact_date", "referral"),
    )
    reply_class = reader.choice(expected["reply_class"], "expected.reply_class", ReplyClass)
    objection = (
        None
        if expected["objection_type"] is None
        else reader.choice(expected["objection_type"], "expected.objection_type", ObjectionType)
    )
    if (reply_class is ReplyClass.OBJECTION) != (objection is not None):
        reader.bad("expected.objection_type", "must be set if and only if the class is objection")
    returns = _dated(
        reader,
        expected["return_date"],
        "expected.return_date",
        received_on,
        reply_class,
        ReplyClass.OUT_OF_OFFICE,
    )
    recontact = _dated(
        reader,
        expected["recontact_date"],
        "expected.recontact_date",
        received_on,
        reply_class,
        ReplyClass.NOT_NOW,
    )
    referral = _referral(reader, expected["referral"])
    if (reply_class is ReplyClass.REFERRAL) != (referral is not None):
        reader.bad("expected.referral", "must be set if and only if the class is referral")
    derived = [class_tag(reply_class)]
    if objection is not None:
        derived.append(objection_tag(objection))
    if returns is not None:
        derived.append(RETURN_DATE)
    if recontact is not None:
        derived.append(RECONTACT_DATE)
    if referral is not None:
        derived.append(
            REFERRAL_MAY_NAME if referral["referrer_may_be_named"] else REFERRAL_MAY_NOT_NAME
        )
    unsubscribe = None if reply_class is ReplyClass.UNSUBSCRIBE else "needs the class unsubscribe"
    optional: dict[str, str | None] = dict.fromkeys(
        (*UNSUBSCRIBE_ONLY, INJECTION_OPT_OUT), unsubscribe
    )
    optional[INJECTION_DICTATED_CLASS] = None
    near = "needs a class other than unsubscribe"
    optional[NEAR_OPT_OUT] = None if reply_class is not ReplyClass.UNSUBSCRIBE else near
    reply_input = {"received_on": found["received_on"], "blocks": blocks}
    return _Parsed(reply_input, expected, derived, optional)


# Grounding and drafting cases ------------------------------------------------------------------


def _evidence(reader: _Reader, ids: _Ids, value: object) -> list[dict[str, str]]:
    found = []
    for index, item in enumerate(reader.items(value, "input.evidence")):
        path = f"input.evidence[{index}]"
        chosen = reader.obj(item, path, ("id", "source", "kind", "text"))
        block = _block(
            reader, ids, {key: chosen[key] for key in ("id", "source", "text")}, path
        )
        reader.choice(chosen["kind"], f"{path}.kind", EvidenceKind)
        found.append({**block, "kind": chosen["kind"]})
    return found


def _signals(
    reader: _Reader, ids: _Ids, value: object, evidence_ids: set[str]
) -> list[dict[str, Any]]:
    found = []
    for index, item in enumerate(reader.items(value, "input.signals")):
        path = f"input.signals[{index}]"
        chosen = reader.obj(item, path, ("id", "signal_type", "tier", "evidence_id", "observed_on"))
        signal_id = reader.pattern(chosen["id"], f"{path}.id", BLOCK_ID_PATTERN)
        ids.add(signal_id, f"{path}.id")
        reader.pattern(chosen["signal_type"], f"{path}.signal_type", KEY_PATTERN)
        reader.integer(chosen["tier"], f"{path}.tier", TOP_TIER, LAST_TIER)
        if not _names_one(chosen["evidence_id"], evidence_ids):
            reader.bad(f"{path}.evidence_id", "must name an Evidence item of the case")
        reader.optional_day(chosen["observed_on"], f"{path}.observed_on")
        found.append(chosen)
    return found


def _facts(reader: _Reader, value: object) -> list[dict[str, Any]]:
    found = []
    seen: set[tuple[str, int]] = set()
    for index, item in enumerate(reader.items(value, "input.facts")):
        path = f"input.facts[{index}]"
        chosen = reader.obj(item, path, ("fact_id", "version", "wording"))
        fact_id = reader.pattern(chosen["fact_id"], f"{path}.fact_id", FACT_ID_PATTERN)
        version = reader.integer(chosen["version"], f"{path}.version", 1)
        reader.text(chosen["wording"], f"{path}.wording")
        if (fact_id, version) in seen:
            reader.bad(path, "repeats a fact version")
        seen.add((fact_id, version))
        found.append(chosen)
    return found


def _claims(
    reader: _Reader, value: object, path: str, texts: Sequence[str], evidence_ids: set[str]
) -> list[dict[str, Any]]:
    found = []
    for index, item in enumerate(reader.items(value, path)):
        at = f"{path}[{index}]"
        chosen = reader.obj(item, at, ("text", "evidence_id", "fact"))
        claim = reader.text(chosen["text"], f"{at}.text")
        if not any(claim in text for text in texts):
            reader.bad(f"{at}.text", "must be a substring of the subject or the body")
        cited = chosen["evidence_id"]
        if cited is not None and not _names_one(cited, evidence_ids):
            reader.bad(f"{at}.evidence_id", "must be null or name an Evidence item of the case")
        if chosen["fact"] is not None:
            fact = reader.obj(chosen["fact"], f"{at}.fact", ("fact_id", "version"))
            reader.pattern(fact["fact_id"], f"{at}.fact.fact_id", FACT_ID_PATTERN)
            reader.integer(fact["version"], f"{at}.fact.version", 1)
        found.append(chosen)
    return found


def _names_one(value: object, ids: set[str]) -> bool:
    """Whether ``value`` is one of ``ids`` (a JSON list or object never is)."""
    return isinstance(value, str) and value in ids


def _cited(reader: _Reader, value: object, path: str, signal_ids: set[str]) -> None:
    if value is not None and not _names_one(value, signal_ids):
        reader.bad(path, "must be null or name a Signal of the case")


def _settings(reader: _Reader, value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not set(value) <= set(SETTING_KEYS):
        reader.bad("input.settings", f"must be an object holding only some of {list(SETTING_KEYS)}")
    for key, setting in value.items():
        reader.text(setting, f"input.settings.{key}")
    return value


def _grader_reason(evidence: Sequence[Mapping[str, str]]) -> str | None:
    if any(GRADER_PATTERN.search(item["text"]) for item in evidence):
        return None
    return "needs Evidence that names the grader or the grounding check"


def _draft_texts(draft: Mapping[str, str]) -> tuple[str, str]:
    return draft["subject"], draft["body"]


@dataclass(frozen=True, slots=True)
class _Draft:
    """What a Grounding variant rule reads."""

    market: str
    subject: str
    body: str
    codes: frozenset[str]

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}"


def _says(phrase: str) -> Callable[[_Draft], bool]:
    return lambda draft: phrase in draft.text.casefold()


VARIANT_RULES: Final[Mapping[str, tuple[Callable[[_Draft], bool], str]]] = MappingProxyType(
    {
        "re": (
            lambda draft: draft.subject.casefold().startswith("re:"),
            'needs a subject starting "Re:"',
        ),
        "fwd": (
            lambda draft: draft.subject.casefold().startswith("fwd:"),
            'needs a subject starting "Fwd:"',
        ),
        "multiplier": (
            lambda draft: MULTIPLIER_PATTERN.search(draft.text) is not None,
            'needs a "4-8x"-style number',
        ),
        "last_chance": (_says("last chance"), 'needs the words "last chance"'),
        "fake_deadline": (
            lambda draft: GroundingPattern.PRESSURE_TACTIC.value in draft.codes,
            "needs a pressure_tactic finding",
        ),
        "law_firm": (_says("law firm"), 'needs the words "law firm"'),
        "distributor": (_says("distribut"), 'needs the text "distribut"'),
        "aba_to_uk": (
            lambda draft: draft.market == "uk" and REGULATION_512.search(draft.text) is not None,
            'needs a uk case that mentions "512"',
        ),
        "sra_to_us": (
            lambda draft: draft.market == "us" and mentions_sra_notice(draft.text),
            "needs a us case that names the SRA notice",
        ),
    }
)


def _findings(
    reader: _Reader, value: object, kind: GroundingCase, texts: Sequence[str]
) -> list[dict[str, str]]:
    found = []
    for index, item in enumerate(reader.items(value, "expected.findings")):
        path = f"expected.findings[{index}]"
        chosen = reader.obj(item, path, ("code", "span"))
        code = reader.text(chosen["code"], f"{path}.code")
        span = reader.text(chosen["span"], f"{path}.span")
        if not any(span in text for text in texts):
            reader.bad(f"{path}.span", "must be a substring of the subject or the body")
        if kind is GroundingCase.BANNED_PATTERN and code not in GroundingPattern:
            reader.bad(f"{path}.code", "must be a banned pattern (GroundingPattern)")
        if kind is GroundingCase.PLANTED_CLAIM and code not in ClaimCode:
            reader.bad(f"{path}.code", "must be a planted claim (ClaimCode)")
        found.append(chosen)
    if kind is GroundingCase.CLEAN_CONTROL and found:
        reader.bad("expected.findings", "must be empty for a clean_control")
    if kind is not GroundingCase.CLEAN_CONTROL and not found:
        reader.bad("expected.findings", f"must hold at least one finding for a {kind.value}")
    return found


def _rules(reader: _Reader, value: object, kind: GroundingCase) -> list[str]:
    found = reader.items(value, "expected.rules")
    for index, rule in enumerate(found):
        if rule not in GROUNDING_RULE_IDS:
            reader.bad(f"expected.rules[{index}]", "must be a Story 4.13 rule id")
    if len(set(found)) != len(found):
        reader.bad("expected.rules", "must not repeat a rule id")
    if kind is GroundingCase.CLEAN_CONTROL and found:
        reader.bad("expected.rules", "must be empty for a clean_control")
    return found


def _common(
    reader: _Reader, found: Mapping[str, Any]
) -> tuple[str, str, _Ids, list[dict[str, str]], list[dict[str, Any]], list[dict[str, Any]]]:
    """The Segment, Market, Evidence, Signals and facts a Grounding or drafting case shares."""
    segment = reader.pattern(found["segment"], "input.segment", KEY_PATTERN)
    market = reader.text(found["market"], "input.market")
    if market not in MARKETS:
        reader.bad("input.market", f"must be one of {list(MARKETS)}")
    ids = _Ids(reader)
    evidence = _evidence(reader, ids, found["evidence"])
    evidence_ids = {item["id"] for item in evidence}
    signals = _signals(reader, ids, found["signals"], evidence_ids)
    facts = _facts(reader, found["facts"])
    return segment, market, ids, evidence, signals, facts


GROUNDING_INPUT: Final = (
    "segment",
    "market",
    "step",
    "draft",
    "claims",
    "cited_signal_id",
    "signals",
    "evidence",
    "facts",
    "earlier_steps",
    "settings",
)


def _earlier_steps(
    reader: _Reader, value: object, step: int, signal_ids: set[str], evidence_ids: set[str]
) -> None:
    seen: set[int] = set()
    for index, item in enumerate(reader.items(value, "input.earlier_steps")):
        path = f"input.earlier_steps[{index}]"
        chosen = reader.obj(
            item, path, ("step", "subject", "body", "claims", "cited_signal_id")
        )
        if step == FIRST_STEP:
            reader.bad("input.earlier_steps", "must be empty for Step 1")
        earlier = reader.integer(chosen["step"], f"{path}.step", FIRST_STEP, step - 1)
        if earlier in seen:
            reader.bad(f"{path}.step", "must not repeat an earlier Step")
        seen.add(earlier)
        texts = (
            reader.text(chosen["subject"], f"{path}.subject"),
            reader.text(chosen["body"], f"{path}.body"),
        )
        _claims(reader, chosen["claims"], f"{path}.claims", texts, evidence_ids)
        _cited(reader, chosen["cited_signal_id"], f"{path}.cited_signal_id", signal_ids)


def _grounding(reader: _Reader, value: object, wanted: object) -> _Parsed:
    found = reader.obj(value, "input", GROUNDING_INPUT)
    segment, market, _ids, evidence, signals, _facts_found = _common(reader, found)
    step = reader.integer(found["step"], "input.step", FIRST_STEP, LAST_STEP)
    draft = reader.obj(found["draft"], "input.draft", ("subject", "body"))
    texts = (
        reader.text(draft["subject"], "input.draft.subject"),
        reader.text(draft["body"], "input.draft.body"),
    )
    evidence_ids = {item["id"] for item in evidence}
    signal_ids = {item["id"] for item in signals}
    _claims(reader, found["claims"], "input.claims", texts, evidence_ids)
    _cited(reader, found["cited_signal_id"], "input.cited_signal_id", signal_ids)
    _earlier_steps(reader, found["earlier_steps"], step, signal_ids, evidence_ids)
    _settings(reader, found["settings"])
    expected = reader.obj(wanted, "expected", ("kind", "findings", "rules"))
    kind = reader.choice(expected["kind"], "expected.kind", GroundingCase)
    findings = _findings(reader, expected["findings"], kind, texts)
    _rules(reader, expected["rules"], kind)
    codes = [finding["code"] for finding in findings]
    derived: list[str] = []
    for code in codes:
        if code in GroundingPattern:
            tag = pattern_tag(GroundingPattern(code))
        else:
            tag = CLAIM_TAGS[ClaimCode(code)]
        if tag not in derived:
            derived.append(tag)
    if kind is GroundingCase.CLEAN_CONTROL:
        derived.append(CONTROL_CLEAN)
    derived += [f"segment:{segment}", f"market:{market}", f"step:{step}"]
    subject, body = _draft_texts(draft)
    checked = _Draft(market, subject, body, frozenset(codes))
    optional: dict[str, str | None] = {
        variant_tag(name): None if rule(checked) else reason
        for name, (rule, reason) in VARIANT_RULES.items()
    }
    optional[INJECTION_GRADER] = _grader_reason(evidence)
    optional[INJECTION_HIDDEN] = None
    return _Parsed(found, expected, derived, optional)


DRAFTING_INPUT: Final = (
    "segment",
    "market",
    "lead_name",
    "contact",
    "angle_note",
    "signals",
    "evidence",
    "facts",
)


def is_event(signal: Mapping[str, Any]) -> bool:
    """Whether a case's Signal is an Event: Tier 1 or 2, with an ``observed_on`` date."""
    return signal["tier"] <= LAST_EVENT_TIER and signal["observed_on"] is not None


def _drafting(reader: _Reader, value: object, wanted: object) -> _Parsed:
    found = reader.obj(value, "input", DRAFTING_INPUT)
    segment, market, _ids, evidence, signals, facts = _common(reader, found)
    reader.text(found["lead_name"], "input.lead_name")
    contact = reader.obj(found["contact"], "input.contact", ("name", "role"))
    reader.text(contact["name"], "input.contact.name")
    reader.text(contact["role"], "input.contact.role")
    reader.optional_text(found["angle_note"], "input.angle_note")
    if not evidence:
        reader.bad("input.evidence", "must hold at least one Evidence item")
    if not facts:
        reader.bad("input.facts", "must hold at least one fact")
    events = {signal["id"] for signal in signals if is_event(signal)}
    if not events:
        reader.bad("input.signals", "must hold at least one Event (Tier 1 or 2, dated)")
    expected = reader.obj(wanted, "expected", ("cited_signal_id",))
    if not _names_one(expected["cited_signal_id"], events):
        reader.bad("expected.cited_signal_id", "must name an Event among the case's Signals")
    optional: dict[str, str | None] = {
        INJECTION_GRADER: _grader_reason(evidence),
        INJECTION_HIDDEN: None,
    }
    return _Parsed(found, expected, [f"segment:{segment}", f"market:{market}"], optional)


_PARSERS: Final[Mapping[TestSet, Callable[[_Reader, object, object], _Parsed]]] = (
    MappingProxyType(
        {TestSet.REPLY: _reply, TestSet.GROUNDING: _grounding, TestSet.DRAFTING_SMOKE: _drafting}
    )
)


def _covers(reader: _Reader, value: object, parsed: _Parsed) -> tuple[str, ...]:
    tokens = reader.items(value, "covers")
    seen: list[str] = []
    for index, token in enumerate(tokens):
        path = f"covers[{index}]"
        if not isinstance(token, str) or COVER_PATTERN.fullmatch(token) is None:
            reader.bad(path, f"must be a tag matching {COVER_PATTERN.pattern}")
        if token in seen:
            reader.bad(path, "repeats a tag")
        seen.append(token)
        if token in parsed.derived:
            continue
        if token not in parsed.optional:
            reader.bad(path, f"{token} isn't a tag of this case")
        reason = parsed.optional[token]
        if reason is not None:
            reader.bad(path, f"{token} {reason}")
    for token in parsed.derived:
        if token not in seen:
            reader.bad("covers", f"must include {token}")
    return tuple(seen)


def set_case(test_set: TestSet, name: str, data: object) -> TestCase:
    """The case in the JSON ``data`` of the file ``name`` of ``test_set``; see the module
    docstring."""
    if not isinstance(test_set, TestSet):
        raise TypeError("set_case takes a TestSet")
    parser = _PARSERS.get(test_set)
    if parser is None:
        raise ValueError("set_case reads the reply, grounding and drafting_smoke sets only")
    reader = _Reader(f"{test_set.value}/{name}")
    case = reader.obj(data, "the case", ENVELOPE_FIELDS)
    key = reader.text(case["id"], "id")
    if key != PurePath(name).stem or CASE_KEY_PATTERN.fullmatch(key) is None:
        reader.bad("id", f"must be the file's stem and match {CASE_KEY_PATTERN.pattern}")
    if key.startswith(OVERRIDE_PREFIX):
        reader.bad("id", f"must not start with {OVERRIDE_PREFIX}")
    if case["set"] != test_set.value:
        reader.bad("set", f"must be {test_set.value}")
    route = CASE_ROUTES[test_set]
    if case["route"] != route:
        reader.bad("route", f"must be {route}")
    parsed = parser(reader, case["input"], case["expected"])
    covers = _covers(reader, case["covers"], parsed)
    try:
        return TestCase(key, test_set, route, parsed.input, parsed.expected, covers)
    except (TypeError, ValueError):  # pragma: no cover - every field was checked above
        reader.bad("the case", "must be plain UTF-8 JSON")


# Coverage --------------------------------------------------------------------------------------


def reply_text(case: TestCase) -> str:
    """The text of a Reply case's ``reply`` block."""
    return next(
        str(block["text"]) for block in case.input["blocks"] if block["source"] == REPLY_SOURCE
    )


def _at_least(count: int, minimum: int, what: str) -> list[str]:
    return [] if count >= minimum else [f"at least {minimum} {what} (has {count})"]


def _missing(tags: Counter[str], wanted: Iterable[str]) -> list[str]:
    return [tag for tag in wanted if tags[tag] == 0]


def _reply_gaps(cases: Sequence[TestCase], tags: Counter[str]) -> list[str]:
    gaps = _missing(tags, [class_tag(member) for member in ReplyClass])
    gaps += _missing(tags, [objection_tag(member) for member in ObjectionType])
    texts = sorted({reply_text(case) for case in cases if OPT_OUT_WORDING in case.covers})
    gaps += _at_least(len(texts), MIN_OPT_OUT_TEXTS, f"{OPT_OUT_WORDING} cases with distinct texts")
    keys = {opt_out_key(text) for text in texts}
    gaps += [f'opt-out wording "{w}"' for w in OPT_OUT_WORDINGS if opt_out_key(w) not in keys]
    shapes = (
        ("an all-lowercase opt-out wording", lambda text: text == text.lower()),
        ("an opt-out wording with a capital", lambda text: text != text.lower()),
        ("an opt-out wording with punctuation", _has_punctuation),
        ("an opt-out wording without punctuation", lambda text: not _has_punctuation(text)),
    )
    gaps += [what for what, shape in shapes if not any(shape(text) for text in texts)]
    gaps += _at_least(tags[RETURN_DATE], MIN_RETURN_DATES, f"{RETURN_DATE} cases")
    gaps += _missing(tags, (RECONTACT_DATE, OPT_OUT_IN_AUTO_REPLY, FIRM_WIDE, HOSTILE))
    gaps += _at_least(tags[NEAR_OPT_OUT], MIN_NEAR_OPT_OUTS, f"{NEAR_OPT_OUT} cases")
    return gaps + _missing(
        tags,
        (REFERRAL_MAY_NAME, REFERRAL_MAY_NOT_NAME, INJECTION_OPT_OUT, INJECTION_DICTATED_CLASS),
    )


def _grounding_gaps(tags: Counter[str]) -> list[str]:
    gaps = _missing(tags, [pattern_tag(member) for member in GroundingPattern])
    gaps += _missing(tags, [variant_tag(name) for name in REQUIRED_VARIANTS])
    gaps += _missing(tags, dict.fromkeys(CLAIM_TAGS.values()))
    gaps += _at_least(tags[CONTROL_CLEAN], MIN_CLEAN_CONTROLS, f"{CONTROL_CLEAN} cases")
    return gaps + _missing(tags, (INJECTION_GRADER, INJECTION_HIDDEN))


def _drafting_gaps(
    cases: Sequence[TestCase], tags: Counter[str], segments: Sequence[str], markets: Sequence[str]
) -> list[str]:
    pairs = {
        (token, other)
        for case in cases
        for token in case.covers
        for other in case.covers
        if token.startswith("segment:") and other.startswith("market:")
    }
    gaps = [
        f"segment:{segment} with market:{market}"
        for segment in segments
        for market in markets
        if (f"segment:{segment}", f"market:{market}") not in pairs
    ]
    return gaps + _missing(tags, (INJECTION_HIDDEN, INJECTION_GRADER))


def coverage_gaps(
    test_set: TestSet,
    cases: Iterable[TestCase],
    *,
    segments: Sequence[str] = (),
    markets: Sequence[str] = (),
) -> tuple[str, ...]:
    """What ``test_set``'s ``cases`` are missing, readably; see the module docstring."""
    if not isinstance(test_set, TestSet) or test_set not in CASE_ROUTES:
        raise ValueError("coverage_gaps covers the reply, grounding and drafting_smoke sets")
    if test_set is TestSet.DRAFTING_SMOKE and not (segments and markets):
        raise ValueError("the drafting smoke set's coverage needs the Segments and the Markets")
    chosen: list[TestCase] = []
    for case in cases:
        if not isinstance(case, TestCase):
            raise TypeError(f"coverage_gaps takes TestCases, not {type(case).__name__}")
        if case.test_set is test_set and case.route == CASE_ROUTES[test_set]:
            chosen.append(case)
    tags = Counter(token for case in chosen for token in case.covers)
    gaps = _at_least(len(chosen), MIN_CASES[test_set], f"{CASE_ROUTES[test_set]} cases")
    if test_set is TestSet.REPLY:
        gaps += _reply_gaps(chosen, tags)
    elif test_set is TestSet.GROUNDING:
        gaps += _grounding_gaps(tags)
    else:
        gaps += _drafting_gaps(chosen, tags, segments, markets)
    return tuple(gaps)
