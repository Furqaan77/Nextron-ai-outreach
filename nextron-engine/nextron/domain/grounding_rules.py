"""The deterministic grounding rules (FR-4, FR-21, FR-23, AD-7, AD-18; Story 4.13): the single
definition of what code checks in every Draft revision before any AI judgement. Standard
library only, plus ``nextron.domain``.

``check(revision, brief, context) -> RulesResult(flags, rules_version, waived)`` is pure. Any flag makes
the revision's grounding ``fail``; with none it stays ``pending`` until the AI check (Story 4.14)
decides (``RulesResult.status``). Rules-only results are never ``pass`` (fail closed).

**Inputs** (frozen, slotted; every list a tuple):

- ``RevisionInput(subject, body, claims, cited_signal_id, evidence_ids, fact_versions)``: the
  revision's text (the body without signature or footer), its ``claims`` (``None`` for a
  revision with no claims array, such as an Operator edit, or a tuple of ``Claim``) and its
  citation set.
- ``Claim(text, evidence_id, fact)``: a claim's text (a substring of the subject or body), the
  Evidence id it cites or ``None``, and the ``(fact_id, version)`` it cites or ``None``.
- ``brief``: the Step's ``nextron.domain.sequences.StepBrief``.
- ``RulesContext(step, contact_details, evidence_kinds, signals, strongest_event_id,
  allowed_facts, earlier_steps)``: the Step number; the Settings values whose URLs, email
  addresses and phone numbers a Draft may hold (AD-18); each Evidence id's kind (``quote``,
  ``absence``, ``count``); the Lead's Signals (``LeadSignal(id, signal_type, signal_class, tier,
  score_only, evidence_ids)``); the strongest live Event's id, computed by the caller with
  ``nextron.domain.signals.strongest_event`` (``None`` when there is none); the ``(fact_id,
  version)`` pairs allowed for the Draft's Segment and Market; and the earlier Steps' current
  revisions (``EarlierStep(step, fact_ids, cited_signal_id)``). ``waived_rules`` (Story 4.19,
  default empty) holds the rules a generic revision (filled from a Segment fallback template,
  FR-22) waives: only ``WAIVABLE_RULES`` (``signal_count``), else ``ValueError``.

**Output.** ``Flag(rule, span, explanation)``: ``rule`` one of ``RULE_IDS``; ``span`` a substring
of the subject or body that the rule matched (``""`` for a rule about the whole revision, or a
claim whose text is in neither); ``explanation`` at most ``MAX_EXPLANATION`` (200) characters,
written by code and never quoting Draft text. Flags come in rule-id order (``RULE_IDS``), then
by position: whole-revision flags first, then the subject, then the body, each by offset.
``RULES_VERSION`` is bumped whenever any rule or list here changes (2 since Story 4.19's
waiver). ``RulesResult.waived`` lists, in ``RULE_IDS`` order, the waived rules that apply to the
revision's Step whether or not it has claims (``signal_count`` on Step 1), so an Operator edit
of a generic Step 1 records the waiver too; a waived rule never flags.

**Text** (the definitions every rule shares):

- a *word* is a run of non-whitespace characters holding at least one letter or digit
  (``count_words``; the one word counter);
- a *phrase* matches case-insensitively, ignoring punctuation, as whole words: both are split
  into runs of letters and digits, which must match in sequence (so "couldn't" is "couldn t");
  overlapping matches report the earliest, then longest;
- a *URL* starts ``http://``, ``https://`` or ``www.``, or is a bare host (``name.tld``, the TLD
  2 to 24 letters) with an optional path; an *email address* is ``local@host.tld``; a *phone
  number* is 7 to 15 digits written with spaces, dots, dashes, brackets or a leading ``+`` (a
  date such as ``2026-11-19``, ``19.11.2026`` or ``15-01-2024``, a year range such as
  ``2019 - 2024``, or a number grouped in thousands such as ``1.250.000`` or ``10 000 000`` is
  not one). Trailing sentence punctuation is not part of a URL. Two details are equal when
  their keys are: an email casefolded, a URL casefolded without its ``http://`` or ``https://``,
  a leading ``www.`` and a trailing ``/``, a phone number its digits;
- a *sentence* runs to ``.``, ``!``, ``?`` or a line break (URLs don't split one); a *question*
  is a sentence ending in ``?`` once any trailing ``!``, quotes and closing brackets are
  dropped (so ``?!`` asks one).

**The 14 rules** (``RULE_IDS``). The first 8 read only the text; the last 6 need claims and run
only when ``claims`` is not ``None``.

1. ``word_limit``: the body has more words than ``max_words(brief.word_limit,
   brief.word_comparison)`` (span ``""``).
2. ``subject_rule``: the subject has more words than the brief's ``subject_rule`` allows (span
   the subject), or, on Step 1, starts ``Re:``, ``Fw:`` or ``Fwd:`` in any case (span the
   prefix).
3. ``link_in_step1``: each URL in a Step 1 body.
4. ``unapproved_contact_detail``: each URL, email address or phone number in the subject or body
   that equals none found in ``contact_details``.
5. ``banned_phrase``: each ``BANNED_PHRASES`` phrase (Playbook §2 and §4, FR-21).
6. ``attachment``: each ``ATTACHMENT_PHRASES`` phrase.
7. ``price_or_audit_in_step``: when ``brief.no_price``, each sentence that names a price
   (``nextron.domain.prices.names_price``); when ``brief.no_audit``, each ``AUDIT_WORDS`` word.
8. ``multiple_ctas``: on Step 1, each question sentence after the body's first.
9. ``citation_outside_set``: a claim citing an Evidence id not in the revision's
   ``evidence_ids``, or a fact version not in its ``fact_versions`` or not in
   ``allowed_facts``.
10. ``unhedged_absence``: a claim citing Absence Evidence (live or not) whose text holds no
    ``HEDGES`` phrase, unless every place the text appears in the subject or body lies in
    sentences that hold one.
11. ``signal_count``: on Step 1, the Signals cited are not exactly one: the cited Signal, plus
    the Signals whose Evidence a claim cites when that Evidence isn't the cited Signal's (span
    ``""``). Waived for a generic revision (``waived_rules``), which cites no Signal by design.
12. ``wrong_signal``: on Step 1, a cited Signal that isn't ``strongest_event_id``, or, with no
    cited Signal, the one Signal the claims cite when it isn't (span ``""``).
13. ``score_only_cited``: each claim citing Evidence of a score-only Signal, and a score-only
    cited Signal that no such claim covers (span ``""``).
14. ``repeated_angle``: on a follow-up (Step 2 or later), each fact id a claim cites that an
    earlier Step's current revision cited, a cited Signal an earlier Step cited (span the first
    claim citing its Evidence, else ``""``), and, once per Signal, a claim citing Evidence of
    another Signal an earlier Step cited.

``check`` raises ``TypeError`` unless given a ``RevisionInput``, a ``StepBrief`` and a
``RulesContext``, and ``ValueError`` when the brief is another Step's.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.domain.prices import names_price
from nextron.domain.segments import SignalClass
from nextron.domain.sequences import StepBrief, max_words

RULE_IDS: Final = (
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
TEXT_RULES: Final = RULE_IDS[:8]
CLAIM_RULES: Final = RULE_IDS[8:]
RULES_VERSION: Final = 2  # Story 4.19: the generic waiver of signal_count
SIGNAL_COUNT: Final = "signal_count"
WAIVABLE_RULES: Final = frozenset({SIGNAL_COUNT})
MAX_EXPLANATION: Final = 200
FIRST_STEP: Final = 1
ABSENCE: Final = "absence"

# Playbook §2 ("Never use") and §4 (pressure tactics), FR-21 ("just following up"), the voice
# rule ("no revolutionary"): each matched as a phrase (see the module docstring).
BANNED_PHRASES: Final = (
    "hope this finds you well",
    "hope this email finds you well",
    "hope this message finds you well",
    "wanted to reach out",
    "just reaching out",
    "just bumping this",
    "bumping this",
    "circling back",
    "circle back",
    "just following up",
    "just wanted to follow up",
    "just checking in",
    "touching base",
    "we are the leading",
    "we're the leading",
    "last chance",
    "final chance",
    "revolutionary",
)
ATTACHMENT_PHRASES: Final = (
    "attach",
    "attached",
    "attaching",
    "attachment",
    "attachments",
    "see the pdf",
    "enclosed pdf",
)
AUDIT_WORDS: Final = ("audit", "audits")
# Hedged wording for an Absence claim (FR-21; the AC's list and its spelled-out forms).
HEDGES: Final = (
    "couldn't see",
    "could not see",
    "couldn't find",
    "could not find",
    "it looks like",
    "looks like",
    "doesn't seem to",
    "does not seem to",
    "doesn't appear to",
    "does not appear to",
    "it seems",
    "seems to",
    "might",
)


class GroundingStatus(StrEnum):
    """A revision's grounding (AD-4): ``pending`` until the AI check decides, ``pass`` or
    ``fail``."""

    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Claim:
    """One claim of a drafting output: its text and what it cites."""

    text: str
    evidence_id: str | None
    fact: tuple[str, int] | None


@dataclass(frozen=True, slots=True)
class RevisionInput:
    """The revision checked: its text (no signature or footer), claims and citation set."""

    subject: str
    body: str
    claims: tuple[Claim, ...] | None
    cited_signal_id: str | None
    evidence_ids: tuple[str, ...]
    fact_versions: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class LeadSignal:
    """One of the Lead's current Signals, with its live Evidence ids."""

    id: str
    signal_type: str
    signal_class: SignalClass
    tier: int
    score_only: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EarlierStep:
    """An earlier Step's current revision: the fact ids it cites and its cited Signal."""

    step: int
    fact_ids: frozenset[str]
    cited_signal_id: str | None


@dataclass(frozen=True, slots=True)
class RulesContext:
    """What the rules check a revision against; see the module docstring."""

    step: int
    contact_details: tuple[str, ...]
    evidence_kinds: Mapping[str, str]
    signals: tuple[LeadSignal, ...]
    strongest_event_id: str | None
    allowed_facts: frozenset[tuple[str, int]]
    earlier_steps: tuple[EarlierStep, ...]
    waived_rules: frozenset[str] = frozenset()  # Story 4.19: a generic revision's waiver


@dataclass(frozen=True, slots=True)
class Flag:
    """One rule break: the rule, the matched span and code's explanation."""

    rule: str
    span: str
    explanation: str

    def __post_init__(self) -> None:
        if self.rule not in RULE_IDS:
            raise ValueError("a flag's rule must be one of RULE_IDS")
        if not 0 < len(self.explanation) <= MAX_EXPLANATION:
            raise ValueError(f"a flag's explanation must be 1 to {MAX_EXPLANATION} characters")


@dataclass(frozen=True, slots=True)
class RulesResult:
    """The flags, in order, and the rules-list version they came from."""

    flags: tuple[Flag, ...]
    rules_version: int
    waived: tuple[str, ...] = ()  # Story 4.19: the waived rules that applied

    @property
    def status(self) -> GroundingStatus:
        """``fail`` with any flag, else ``pending`` (the AI check decides ``pass``)."""
        return GroundingStatus.FAIL if self.flags else GroundingStatus.PENDING


# Text ---------------------------------------------------------------------------------------------

WHOLE: Final = -1  # a flag about the whole revision sorts first
SUBJECT: Final = 0
BODY: Final = 1

_WORD_RUN: Final = re.compile(r"[^\W_]+")
_EMAIL: Final = re.compile(
    r"(?<![\w.+-])[\w.+-]+@(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,24}(?![\w-])"
)
_URL: Final = re.compile(
    r"(?:https?://|www\.)[^\s<>\"']+"
    r"|(?<![\w@./-])(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,24}(?![\w@-])(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)
_PHONE: Final = re.compile(r"(?<![\w+])\+?\(?\d[\d \t().-]{5,}\d(?![\w])")
# Digit runs the phone pattern matches that are not phone numbers: dates (day or month first
# with a 4-digit year, or the year first), year ranges, and numbers grouped in thousands.
_NOT_PHONE: Final = re.compile(
    r"\d{1,2}([./-])\d{1,2}\1\d{4}"
    r"|\d{4}([./-])\d{1,2}\2\d{1,2}"
    r"|(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2}"
    r"|\d{1,3}([.,])\d{3}(?:\3\d{3})+"
    r"|\d{1,2}( )\d{3}(?:\4\d{3})+"
)
_URL_PREFIX: Final = re.compile(r"(?:https?://)?(?:www\.)?", re.IGNORECASE)
_QUESTION_TAIL: Final = "!\"')]}\u201d\u2019"
_URL_TRAILING: Final = ".,;:!?)]}'\""
_FIRST_EMAIL_PREFIX: Final = re.compile(r"\s*(?:re|fwd?)\s*:", re.IGNORECASE)
_SENTENCE: Final = re.compile(r"[^.!?\n]+[.!?]*|[.!?]+")
MIN_PHONE_DIGITS: Final = 7
MAX_PHONE_DIGITS: Final = 15


def count_words(text: str) -> int:
    """The words in ``text``: runs of non-whitespace holding a letter or digit."""
    return sum(1 for token in text.split() if any(character.isalnum() for character in token))


@dataclass(frozen=True, slots=True)
class _Detail:
    start: int
    end: int
    key: str


def _phrase_words(phrase: str) -> tuple[str, ...]:
    return tuple(word.casefold() for word in _WORD_RUN.findall(phrase))


def _phrase_spans(text: str, phrases: Iterable[str]) -> list[tuple[int, int]]:
    """Where any of ``phrases`` matches ``text`` (see the module docstring), earliest first;
    overlapping matches keep the earliest, then longest."""
    runs = [
        (match.group().casefold(), match.start(), match.end())
        for match in _WORD_RUN.finditer(text)
    ]
    words = [run[0] for run in runs]
    wanted = {_phrase_words(phrase) for phrase in phrases} - {()}
    found: list[tuple[int, int]] = []  # (first word, past the last word)
    for first in range(len(words)):
        for phrase in wanted:
            last = first + len(phrase)
            if tuple(words[first:last]) == phrase:
                found.append((first, last))
    found.sort(key=lambda pair: (pair[0], -pair[1]))
    kept: list[tuple[int, int]] = []
    for first, last in found:
        if kept and first < kept[-1][1]:
            continue
        kept.append((first, last))
    return [(runs[first][1], runs[last - 1][2]) for first, last in kept]


def _has_phrase(text: str, phrases: Iterable[str]) -> bool:
    return bool(_phrase_spans(text, phrases))


def _masked(text: str, spans: Iterable[tuple[int, int]]) -> str:
    characters = list(text)
    for start, end in spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def _url_end(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in _URL_TRAILING:
        end -= 1
    return end


def _url_key(value: str) -> str:
    """A URL's key: without its scheme, a leading ``www.`` and a trailing ``/``, casefolded."""
    return _URL_PREFIX.sub("", value, count=1).casefold().rstrip("/")


def _details(text: str) -> tuple[list[_Detail], list[_Detail]]:
    """The URLs, and the email addresses and phone numbers, in ``text``."""
    emails = [
        _Detail(match.start(), match.end(), match.group().casefold())
        for match in _EMAIL.finditer(text)
    ]
    rest = _masked(text, ((email.start, email.end) for email in emails))
    urls = []
    for match in _URL.finditer(rest):
        end = _url_end(rest, match.start(), match.end())
        if end > match.start():
            urls.append(_Detail(match.start(), end, _url_key(text[match.start() : end])))
    rest = _masked(rest, ((url.start, url.end) for url in urls))
    phones = []
    for match in _PHONE.finditer(rest):
        value = match.group()
        digits = "".join(character for character in value if character.isdigit())
        if _NOT_PHONE.fullmatch(value) or not MIN_PHONE_DIGITS <= len(digits) <= MAX_PHONE_DIGITS:
            continue
        phones.append(_Detail(match.start(), match.end(), "tel:" + digits))
    return urls, emails + phones


def _sentences(text: str) -> Iterator[tuple[int, int, bool]]:
    """Each sentence's (start, end, is a question), URLs never splitting one."""
    urls, _ = _details(text)
    shown = text
    for url in urls:  # a URL's own punctuation never ends a sentence
        shown = shown[: url.start] + "u" * (url.end - url.start) + shown[url.end :]
    for match in _SENTENCE.finditer(shown):
        raw = shown[match.start() : match.end()]
        start = match.start() + (len(raw) - len(raw.lstrip()))
        end = match.start() + len(raw.rstrip())
        if start < end and any(character.isalnum() for character in text[start:end]):
            yield start, end, raw.rstrip().rstrip(_QUESTION_TAIL).endswith("?")


# The rules ----------------------------------------------------------------------------------------


class _Flags:
    """The flags found so far, each with its sort position."""

    def __init__(self, revision: RevisionInput) -> None:
        self._revision = revision
        self._found: list[tuple[int, int, int, int, Flag]] = []

    def add(self, rule: str, where: int, start: int, end: int, explanation: str) -> None:
        if where == SUBJECT:
            span = self._revision.subject[start:end]
        elif where == BODY:
            span = self._revision.body[start:end]
        else:
            span = ""
        order = (RULE_IDS.index(rule), where, start, len(self._found))
        self._found.append((*order, Flag(rule, span, explanation)))

    def whole(self, rule: str, explanation: str) -> None:
        self.add(rule, WHOLE, 0, 0, explanation)

    def claim(self, rule: str, claim: Claim, explanation: str) -> None:
        """A flag on ``claim``'s text where it first appears (subject first), else ``""``."""
        if claim.text:
            for where, text in ((SUBJECT, self._revision.subject), (BODY, self._revision.body)):
                start = text.find(claim.text)
                if start != -1:
                    self.add(rule, where, start, start + len(claim.text), explanation)
                    return
        self.whole(rule, explanation)

    def result(self, waived: tuple[str, ...] = ()) -> RulesResult:
        ordered = sorted(self._found, key=lambda entry: entry[:4])
        return RulesResult(tuple(entry[4] for entry in ordered), RULES_VERSION, waived)


def _texts(revision: RevisionInput) -> tuple[tuple[int, str], ...]:
    return ((SUBJECT, revision.subject), (BODY, revision.body))


def _word_limit(revision: RevisionInput, brief: StepBrief, flags: _Flags) -> None:
    limit = max_words(brief.word_limit, brief.word_comparison)
    words = count_words(revision.body)
    if words > limit:
        flags.whole("word_limit", f"The body has {words} words; this Step allows at most {limit}.")


def _subject_rule(
    revision: RevisionInput, brief: StepBrief, context: RulesContext, flags: _Flags
) -> None:
    subject = revision.subject
    if brief.subject_rule is not None:
        limit = max_words(brief.subject_rule.word_limit, brief.subject_rule.word_comparison)
        words = count_words(subject)
        if words > limit:
            start = len(subject) - len(subject.lstrip())
            end = len(subject.rstrip())
            flags.add(
                "subject_rule",
                SUBJECT,
                start,
                end,
                f"The subject has {words} words; this Step allows at most {limit}.",
            )
    if context.step == FIRST_STEP:
        prefix = _FIRST_EMAIL_PREFIX.match(subject)
        if prefix is not None:
            start = prefix.start() + (len(prefix.group()) - len(prefix.group().lstrip()))
            flags.add(
                "subject_rule",
                SUBJECT,
                start,
                prefix.end(),
                "A first email's subject must not start with a reply or forward prefix "
                "(Playbook section 4: deceptive subject).",
            )


def _links_and_details(revision: RevisionInput, context: RulesContext, flags: _Flags) -> None:
    allowed: set[str] = set()
    for value in context.contact_details:
        urls, others = _details(value)
        allowed.update(detail.key for detail in urls + others)
    for where, text in _texts(revision):
        urls, others = _details(text)
        if where == BODY and context.step == FIRST_STEP:
            for url in urls:
                flags.add(
                    "link_in_step1",
                    where,
                    url.start,
                    url.end,
                    "Step 1 holds no link in its body; only the signature and footer may.",
                )
        for detail in urls + others:
            if detail.key not in allowed:
                flags.add(
                    "unapproved_contact_detail",
                    where,
                    detail.start,
                    detail.end,
                    "A URL, email address or phone number that isn't a Settings value "
                    "(signature, booking link or privacy link) (AD-18).",
                )


def _phrases(revision: RevisionInput, flags: _Flags) -> None:
    for where, text in _texts(revision):
        for start, end in _phrase_spans(text, BANNED_PHRASES):
            flags.add(
                "banned_phrase",
                where,
                start,
                end,
                "A phrase the Sales Playbook bans (sections 2 and 4, FR-21).",
            )
        for start, end in _phrase_spans(text, ATTACHMENT_PHRASES):
            flags.add(
                "attachment",
                where,
                start,
                end,
                "Emails carry no attachments, so they never mention one (FR-21).",
            )


def _price_or_audit(revision: RevisionInput, brief: StepBrief, flags: _Flags) -> None:
    for where, text in _texts(revision):
        if brief.no_price:
            for start, end, _ in _sentences(text):
                if names_price(text[start:end]):
                    flags.add(
                        "price_or_audit_in_step",
                        where,
                        start,
                        end,
                        "A Sequence Step names no price (FR-4).",
                    )
        if brief.no_audit:
            for start, end in _phrase_spans(text, AUDIT_WORDS):
                flags.add(
                    "price_or_audit_in_step",
                    where,
                    start,
                    end,
                    "A Sequence Step never offers the audit; it appears only in Reply Drafts "
                    "(FR-4).",
                )


def _multiple_ctas(revision: RevisionInput, context: RulesContext, flags: _Flags) -> None:
    if context.step != FIRST_STEP:
        return
    questions = [(start, end) for start, end, question in _sentences(revision.body) if question]
    for start, end in questions[1:]:
        flags.add(
            "multiple_ctas",
            BODY,
            start,
            end,
            f"Step 1 asks one question; this body asks {len(questions)}.",
        )


def _owners(context: RulesContext, evidence_id: str | None) -> frozenset[str]:
    """The Signals whose Evidence includes ``evidence_id``."""
    if evidence_id is None:
        return frozenset()
    return frozenset(signal.id for signal in context.signals if evidence_id in signal.evidence_ids)


def _hedged(revision: RevisionInput, claim: Claim) -> bool:
    """Whether ``claim`` holds a ``HEDGES`` phrase, or every place its text appears in the subject
    or body lies in sentences that hold one."""
    if _has_phrase(claim.text, HEDGES):
        return True
    if not claim.text:
        return False
    found = False
    for _, text in _texts(revision):
        sentences = list(_sentences(text))
        start = text.find(claim.text)
        while start != -1:
            end = start + len(claim.text)
            around = [(first, last) for first, last, _ in sentences if first < end and start < last]
            first = min((pair[0] for pair in around), default=start)
            last = max((pair[1] for pair in around), default=end)
            if not _has_phrase(text[first:last], HEDGES):
                return False
            found = True
            start = text.find(claim.text, start + 1)
    return found


def _citations(
    revision: RevisionInput, claims: tuple[Claim, ...], context: RulesContext, flags: _Flags
) -> None:
    evidence = set(revision.evidence_ids)
    facts = set(revision.fact_versions)
    for claim in claims:
        if claim.evidence_id is not None and claim.evidence_id not in evidence:
            flags.claim(
                "citation_outside_set",
                claim,
                "The claim cites Evidence outside the revision's Evidence set.",
            )
        if claim.fact is not None:
            if claim.fact not in facts:
                flags.claim(
                    "citation_outside_set",
                    claim,
                    "The claim cites a fact version outside the revision's fact set.",
                )
            elif claim.fact not in context.allowed_facts:
                flags.claim(
                    "citation_outside_set",
                    claim,
                    "The claim cites a fact version not allowed for the Draft's Segment and "
                    "Market.",
                )
        kind = None if claim.evidence_id is None else context.evidence_kinds.get(claim.evidence_id)
        if kind == ABSENCE and not _hedged(revision, claim):
            flags.claim(
                "unhedged_absence",
                claim,
                "An Absence claim must be hedged, such as \"it looks like\" or \"couldn't see\" "
                "(FR-21).",
            )


def _signals(
    revision: RevisionInput, claims: tuple[Claim, ...], context: RulesContext, flags: _Flags
) -> None:
    cited = revision.cited_signal_id
    if context.step == FIRST_STEP:
        signals: set[str] = set() if cited is None else {cited}
        for claim in claims:
            owners = _owners(context, claim.evidence_id)
            if cited not in owners:
                signals.update(owners)
        if len(signals) != 1 and SIGNAL_COUNT not in context.waived_rules:
            flags.whole(
                "signal_count",
                f"Step 1 cites exactly one Signal; this revision cites {len(signals)}.",
            )
        if cited is not None:
            chosen: str | None = cited
        elif len(signals) == 1:
            [chosen] = signals
        else:
            chosen = None
        if chosen is not None and chosen != context.strongest_event_id:
            flags.whole(
                "wrong_signal",
                "Step 1 cites the strongest live Event; the Signal cited isn't it."
                if context.strongest_event_id is not None
                else "Step 1 cites the strongest live Event; the Lead has no live Event.",
            )
    score_only = {signal.id for signal in context.signals if signal.score_only}
    covered: set[str] = set()
    for claim in claims:
        found = _owners(context, claim.evidence_id) & score_only
        if found:
            covered.update(found)
            flags.claim(
                "score_only_cited",
                claim,
                "The claim cites a score-only Signal's Evidence; score-only Attributes are "
                "never cited.",
            )
    if cited in score_only and cited not in covered:
        flags.whole(
            "score_only_cited",
            "The revision cites a score-only Signal; score-only Attributes are never cited.",
        )


def _repeated_angle(
    revision: RevisionInput, claims: tuple[Claim, ...], context: RulesContext, flags: _Flags
) -> None:
    if context.step <= FIRST_STEP:
        return
    earlier = [step for step in context.earlier_steps if step.step < context.step]
    used_facts = {fact_id for step in earlier for fact_id in step.fact_ids}
    used_signals = {step.cited_signal_id for step in earlier} - {None}
    flagged: set[str] = set()
    for claim in claims:
        if claim.fact is None:
            continue
        fact_id = claim.fact[0]
        if fact_id in used_facts and fact_id not in flagged:
            flagged.add(fact_id)
            flags.claim(
                "repeated_angle",
                claim,
                "A follow-up uses an angle not used earlier; an earlier Step already cites "
                "this fact.",
            )
    cited = revision.cited_signal_id
    if cited is not None and cited in used_signals:
        explanation = (
            "A follow-up uses an angle not used earlier; an earlier Step already cites this "
            "Signal."
        )
        claim = next((each for each in claims if cited in _owners(context, each.evidence_id)), None)
        if claim is None:
            flags.whole("repeated_angle", explanation)
        else:
            flags.claim("repeated_angle", claim, explanation)
    repeated = set() if cited is None else {cited}
    for claim in claims:
        again = (_owners(context, claim.evidence_id) & used_signals) - repeated
        if again:
            repeated.update(again)
            flags.claim(
                "repeated_angle",
                claim,
                "A follow-up uses an angle not used earlier; an earlier Step already cites the "
                "Signal this claim's Evidence supports.",
            )


def check(revision: RevisionInput, brief: StepBrief, context: RulesContext) -> RulesResult:
    """Every rule over ``revision``; see the module docstring."""
    if not isinstance(revision, RevisionInput):
        raise TypeError("check takes a RevisionInput")
    if not isinstance(brief, StepBrief):
        raise TypeError("check takes the Step's StepBrief")
    if not isinstance(context, RulesContext):
        raise TypeError("check takes a RulesContext")
    if brief.step != context.step:
        raise ValueError("the brief is not this Step's")
    unknown = set(context.waived_rules) - WAIVABLE_RULES
    if unknown:
        raise ValueError("only signal_count can be waived")
    flags = _Flags(revision)
    _word_limit(revision, brief, flags)
    _subject_rule(revision, brief, context, flags)
    _links_and_details(revision, context, flags)
    _phrases(revision, flags)
    _price_or_audit(revision, brief, flags)
    _multiple_ctas(revision, context, flags)
    claims = revision.claims
    if claims is not None:
        _citations(revision, claims, context, flags)
        _signals(revision, claims, context, flags)
        _repeated_angle(revision, claims, context, flags)
    applies = {SIGNAL_COUNT: context.step == FIRST_STEP}
    return flags.result(
        tuple(rule for rule in RULE_IDS if rule in context.waived_rules and applies.get(rule))
    )
