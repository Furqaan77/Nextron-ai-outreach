"""Every Draft revision checked against the deterministic grounding rules (FR-4, FR-21, AD-18;
Story 4.13), on the world of ``tests.app.test_draft_revisions`` (Law-PrivateAI x US, the footer
settings saved):

- each revision written (generated, Operator edit, footer change) gets exactly one
  ``grounding_results`` row in the same unit of work, with the current ``RULES_VERSION``:
  ``fail`` with its flags when any rule flags, else ``pending`` with ``[]``;
- the I/O matrix: a clean edit, a rule break, a Settings link, Step 1 and Step 2 limits, an edit
  with no claims, a generated revision with claims, a footer change, the erasure;
- a context that can't be built writes nothing; the table's checks and keys.
"""

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.drafting import api as drafting_api
from nextron.app.drafting.api import Traceability
from nextron.app.playbook.api import CompanyFacts, check_seed_facts, latest_allowed
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.ai import Urgency
from nextron.domain.draft import RevisionOrigin
from nextron.domain.grounding_rules import RULES_VERSION, Claim, GroundingStatus
from tests.app.test_draft_revisions import (
    BODY,
    SUBJECT,
    TRACE,
    append,
    drafted,
    ids_of,
    raw,
    revise,
    save_footer,
    world,  # noqa: F401  (the fixture)
)
from tests.app.test_draft_slots import enrolled
from tests.app.test_enroll import World, queue_on, shipped

PRIVACY = "https://www.example.com/nextron/privacy"
RESULT_COLUMNS = "rev, status, flags, rules_version, route_tuple_id, served_model, checked_at"


def results(world: World, draft_id: str) -> list[tuple[Any, ...]]:
    return world.sql(
        f"SELECT {RESULT_COLUMNS} FROM grounding_results WHERE draft_id = ? ORDER BY rev",
        (draft_id,),
    )


def latest(world: World, draft_id: str) -> tuple[str, list[dict[str, str]]]:
    """The current revision's status and flags."""
    *_, row = results(world, draft_id)
    return row[1], json.loads(row[2])


def pairs(flags: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(flag["rule"], flag["span"]) for flag in flags]


def words(count: int) -> str:
    return "Hi " + " ".join(["word"] * (count - 1))


# Every revision -----------------------------------------------------------------------------------


def test_every_revision_has_one_result_with_the_rules_version(world: World) -> None:
    _, [step_1, step_2] = drafted(world)
    revise(world, step_1, "Edited subject", "Hi Zelda,\n\nAn edited note.")
    save_footer(world, legal_name="Nextron AI Limited")

    for draft_id, revs in ((step_1, [1, 2, 3]), (step_2, [1, 2])):
        rows = results(world, draft_id)
        assert [row[0] for row in rows] == revs
        assert {row[1:6] for row in rows} == {("pending", "[]", RULES_VERSION, None, None)}
    [(count,)] = world.sql("SELECT COUNT(*) FROM grounding_results")
    [(revisions,)] = world.sql("SELECT COUNT(*) FROM draft_revisions")
    assert count == revisions
    [(checked, created)] = world.sql(
        "SELECT g.checked_at, r.created_at FROM grounding_results g JOIN draft_revisions r "
        "ON r.draft_id = g.draft_id AND r.rev = g.rev WHERE g.draft_id = ? AND g.rev = 2",
        (step_1,),
    )
    assert checked == created  # written with the revision, in the same unit


def test_the_query_reads_a_result(world: World) -> None:
    _, [step_1, _] = drafted(world)
    revise(world, step_1, SUBJECT, BODY + " Circling back.")

    found = world.read(lambda: drafting_queries.grounding_result(step_1, 2))
    assert found is not None
    assert (found.rev, found.status, found.rules_version) == (2, GroundingStatus.FAIL, 1)
    assert [(flag.rule, flag.span) for flag in found.flags] == [("banned_phrase", "Circling back")]
    assert world.read(lambda: drafting_queries.grounding_result(step_1, 9)) is None
    assert world.read(lambda: drafting_queries.grounding_result("not-a-ulid", 1)) is None


# The I/O matrix -----------------------------------------------------------------------------------


def test_a_clean_edit_is_pending(world: World) -> None:
    _, [step_1, _] = drafted(world)
    assert revise(world, step_1, "your intake role", "Hi Zelda,\n\nWorth a look?") == 2
    assert latest(world, step_1) == ("pending", [])


def test_a_rule_break_fails_with_its_spans(world: World) -> None:
    _, [_, step_2] = drafted(world)
    body = "Hi Zelda,\n\nCircling back on my note. Details: https://other.example.com/x"
    revise(world, step_2, SUBJECT, body)

    status, flags = latest(world, step_2)
    assert status == "fail"
    assert pairs(flags) == [
        ("unapproved_contact_detail", "https://other.example.com/x"),
        ("banned_phrase", "Circling back"),
    ]
    assert all(0 < len(flag["explanation"]) <= 200 for flag in flags)


def test_a_settings_link_is_approved_but_still_a_step_1_link(world: World) -> None:
    save_footer(world, privacy_notice_url=PRIVACY)
    _, [step_1, step_2] = drafted(world)
    body = f"Hi Zelda,\n\nOur privacy notice: {PRIVACY}"
    revise(world, step_1, SUBJECT, body)
    revise(world, step_2, SUBJECT, body)

    status, flags = latest(world, step_1)
    assert (status, pairs(flags)) == ("fail", [("link_in_step1", PRIVACY)])
    assert latest(world, step_2) == ("pending", [])


def test_a_signature_phone_and_url_are_approved(world: World) -> None:
    save_footer(
        world,
        sender_signature="Mac, Nextron AI | +44 20 7946 0318 | https://www.example.com/nextron",
    )
    _, [_, step_2] = drafted(world)
    body = "Hi Zelda,\n\nCall me on +44 20 7946 0318 or see www.example.com/nextron/ for more."
    revise(world, step_2, SUBJECT, body)
    assert latest(world, step_2) == ("pending", [])


@pytest.mark.parametrize(
    ("subject", "body", "rule"),
    [
        (SUBJECT, words(80), "word_limit"),
        ("one two three four five six seven", BODY, "subject_rule"),
        ("RE: x", BODY, "subject_rule"),
    ],
)
def test_step_1_limits(world: World, subject: str, body: str, rule: str) -> None:
    _, [step_1, _] = drafted(world)
    revise(world, step_1, subject, body)
    status, flags = latest(world, step_1)
    assert (status, [flag["rule"] for flag in flags]) == ("fail", [rule])


def test_step_1_allows_79_words_and_step_2_allows_70(world: World) -> None:
    _, [step_1, step_2] = drafted(world)
    revise(world, step_1, SUBJECT, words(79))
    revise(world, step_2, SUBJECT, words(70))
    assert latest(world, step_1) == ("pending", [])
    assert latest(world, step_2) == ("pending", [])
    revise(world, step_2, SUBJECT, words(71))
    assert [flag["rule"] for flag in latest(world, step_2)[1]] == ["word_limit"]


def test_an_edit_without_claims_runs_no_claim_rule(world: World) -> None:
    # Step 1 citing a Signal that isn't the strongest live Event, then citing nothing: with
    # claims, wrong_signal or signal_count would flag them; an edit carries no claims array
    _, [step_1, _] = drafted(world)
    revise(world, step_1, SUBJECT, BODY)
    assert latest(world, step_1) == ("pending", [])
    uncited = replace(TRACE, cited_signal_id=None)
    assert append(world, step_1, RevisionOrigin.OPERATOR_EDIT, uncited) == 3
    assert latest(world, step_1) == ("pending", [])


def seed_facts(world: World) -> None:
    """The shipped Approved Company Facts (``build_world`` syncs the setup seeds only)."""
    facts = shipped(main.COMPANY_FACTS_FILE_NAME, validate_company_facts)
    segments = {row[0] for row in world.sql("SELECT key FROM segments")}
    countries = {"us": "US", "uk": "GB"}
    markets = {row[0]: countries[row[0]] for row in world.sql("SELECT code FROM markets")}
    now = world.clock.now()
    checked = check_seed_facts(facts, segments, markets, latest_allowed(now))
    world.transact(lambda: CompanyFacts(world.clock).seed_facts(checked, now))


def lead_with_signals(world: World, lead_id: str) -> tuple[str, str, str]:
    """A live Tier 1 hiring Event and its quote, plus a contact-form-only Absence: (event,
    quote, absence); the shipped facts are seeded with them."""
    seed_facts(world)
    now = world.clock.now()

    def evidence(kind: str) -> str:
        return signals_repo.insert_evidence(
            lead_id=lead_id,
            evidence_type=kind,
            quote="We are hiring an intake coordinator." if kind == "quote" else None,
            sought=None if kind == "quote" else "chat widget",
            number=None,
            fact=None,
            value=None,
            pages_checked=("https://firm.example.com/careers",),
            source="website",
            source_url="https://firm.example.com/careers",
            retrieved_at=now,
            extracted_by="ai:extract_signals@v1",
            person_contact_id=None,
            at=now,
        )

    def write() -> tuple[str, str, str]:
        quote, absence = evidence("quote"), evidence("absence")
        event = signals_repo.insert_signal(
            lead_id=lead_id,
            signal_type="law_hiring_legal_ops",
            signal_class="event",
            tier=1,
            score_only=False,
            event_date=(now - timedelta(days=5)).date(),
            first_seen_at=now - timedelta(days=5),
            evidence_ids=[quote],
        )
        signals_repo.insert_signal(
            lead_id=lead_id,
            signal_type="law_contact_form_only",
            signal_class="attribute",
            tier=3,
            score_only=True,
            event_date=None,
            first_seen_at=now,
            evidence_ids=[absence],
        )
        return event, quote, absence

    return world.transact(write)


GENERATED_BODY = (
    "Hi Zelda,\n\nSaw the firm is hiring an intake coordinator. More intake means more client "
    "detail in more tools.\n\nWe built an AI legal assistant platform.\n\nWorth a look?"
)
HIRING = "the firm is hiring an intake coordinator"
PROOF = "We built an AI legal assistant platform"


def generated(
    world: World, claims: tuple[Claim, ...], *, retract_absence: bool = False, **changes: Any
) -> str:
    """A Step 1 Draft generated with ``claims``, citing the Lead's Event; its id. With
    ``retract_absence`` the Absence Evidence is no longer live when it's generated."""
    lead_id, _, enrollment_id = enrolled(world, steps=(1,))
    event, quote, absence = lead_with_signals(world, lead_id)
    if retract_absence:
        world.transact(lambda: signals_repo.retract_evidence(absence, world.clock.now()))
    trace = Traceability(
        evidence_ids=(quote, absence),
        fact_versions=(("legal_ai", 1),),
        cited_signal_id=event,
        route_tuple_id=None,
        playbook_sha256=None,
        playbook_version=None,
        prompt_version=None,
        instruction=None,
    )
    trace = replace(trace, **changes)
    [draft_id] = ids_of(world, enrollment_id)
    named = {"quote": quote, "absence": absence}
    real = tuple(
        replace(claim, evidence_id=named.get(claim.evidence_id or "", claim.evidence_id))
        for claim in claims
    )
    drafting = drafting_api.Drafting(world.clock, queue_on(world.clock))

    def write() -> int:
        view = drafting_queries.draft(draft_id)
        assert view is not None
        return drafting._append(
            view,
            "your intake coordinator role",
            GENERATED_BODY,
            RevisionOrigin.GENERATED,
            trace,
            world.clock.now(),
            claims=real,
            urgency=Urgency.SOON,
        )

    assert world.transact(write) == 1
    return draft_id


def test_a_generated_revision_with_good_claims_is_pending(world: World) -> None:
    claims = (Claim(HIRING, "quote", None), Claim(PROOF, None, ("legal_ai", 1)))
    draft_id = generated(world, claims)
    assert latest(world, draft_id) == ("pending", [])


def test_a_generated_claim_outside_the_set_fails(world: World) -> None:
    outside = "01JD3QZ8X5V9K2M7N4P6R8T0AE"
    claims = (Claim(HIRING, outside, None), Claim(PROOF, None, ("legal_ai", 1)))
    draft_id = generated(world, claims)
    status, flags = latest(world, draft_id)
    assert (status, pairs(flags)) == ("fail", [("citation_outside_set", HIRING)])


def test_a_generated_claim_on_a_fact_not_allowed_for_the_market_fails(world: World) -> None:
    claims = (Claim(HIRING, "quote", None), Claim(PROOF, None, ("sra_ai_notice", 1)))
    draft_id = generated(world, claims, fact_versions=(("sra_ai_notice", 1),))
    assert pairs(latest(world, draft_id)[1]) == [("citation_outside_set", PROOF)]


def test_a_generated_claim_on_the_score_only_absence_fails(world: World) -> None:
    claims = (Claim(HIRING, "absence", None), Claim(PROOF, None, ("legal_ai", 1)))
    draft_id = generated(world, claims)
    assert [flag["rule"] for flag in latest(world, draft_id)[1]] == [
        "unhedged_absence",
        "signal_count",
        "score_only_cited",
    ]


def test_a_claim_on_absence_evidence_that_is_no_longer_live_is_still_hedge_checked(
    world: World,
) -> None:
    claims = (Claim(HIRING, "absence", None), Claim(PROOF, None, ("legal_ai", 1)))
    draft_id = generated(world, claims, retract_absence=True)
    rules = [flag["rule"] for flag in latest(world, draft_id)[1]]
    assert "unhedged_absence" in rules


def test_a_generated_step_1_citing_no_event_fails(world: World) -> None:
    draft_id = generated(world, (Claim(PROOF, None, ("legal_ai", 1)),), cited_signal_id=None)
    assert pairs(latest(world, draft_id)[1]) == [("signal_count", "")]


def test_a_follow_up_repeating_step_1s_fact_fails(world: World) -> None:
    lead_id, _, enrollment_id = enrolled(world, steps=(1, 2))
    _, quote, _ = lead_with_signals(world, lead_id)
    [step_1, step_2] = ids_of(world, enrollment_id)
    trace = replace(TRACE, evidence_ids=(quote,), fact_versions=(("legal_ai", 1),))
    assert append(world, step_1, RevisionOrigin.GENERATED, trace) == 1
    drafting = drafting_api.Drafting(world.clock, queue_on(world.clock))

    def write() -> int:
        view = drafting_queries.draft(step_2)
        assert view is not None
        return drafting._append(
            view,
            SUBJECT,
            f"Hi Zelda,\n\n{PROOF}.\n\nIs this on your radar?",
            RevisionOrigin.GENERATED,
            trace,
            world.clock.now(),
            claims=(Claim(PROOF, None, ("legal_ai", 1)),),
            urgency=Urgency.SOON,
        )

    world.transact(write)
    assert pairs(latest(world, step_2)[1]) == [("repeated_angle", PROOF)]


def test_a_follow_up_citing_step_1s_signal_fails(world: World) -> None:
    lead_id, _, enrollment_id = enrolled(world, steps=(1, 2))
    event, quote, _ = lead_with_signals(world, lead_id)
    [step_1, step_2] = ids_of(world, enrollment_id)
    trace = replace(TRACE, evidence_ids=(quote,), fact_versions=(), cited_signal_id=event)
    assert append(world, step_1, RevisionOrigin.GENERATED, trace) == 1
    drafting = drafting_api.Drafting(world.clock, queue_on(world.clock))

    def write() -> int:
        view = drafting_queries.draft(step_2)
        assert view is not None
        return drafting._append(
            view,
            SUBJECT,
            f"Hi Zelda,\n\nSaw {HIRING}.\n\nIs this on your radar?",
            RevisionOrigin.GENERATED,
            trace,
            world.clock.now(),
            claims=(Claim(HIRING, quote, None),),
            urgency=Urgency.SOON,
        )

    world.transact(write)
    assert pairs(latest(world, step_2)[1]) == [("repeated_angle", HIRING)]


def test_a_footer_change_gives_the_new_revision_its_own_result(world: World) -> None:
    _, [step_1, _] = drafted(world)
    revise(world, step_1, SUBJECT, BODY + " Circling back.")
    save_footer(world, legal_name="Nextron AI Limited")

    rows = results(world, step_1)
    assert [(row[0], row[1]) for row in rows] == [(1, "pending"), (2, "fail"), (3, "fail")]
    assert rows[2][2] == rows[1][2]  # the same text, the same flags


def test_erasure_turns_every_non_empty_span_into_erased_and_keeps_the_rows(world: World) -> None:
    contact_id, [step_1, _] = drafted(world)
    revise(world, step_1, "RE: Zelda's intake", BODY + " Circling back, Zelda.")
    revise(world, step_1, SUBJECT, words(80))  # rev 3: a whole-revision flag, span ""
    other, [other_1, _] = drafted(world)
    revise(world, other_1, SUBJECT, BODY + " Circling back.")
    before = results(world, step_1)
    erasures = Erasures(world.clock, Compliance(world.clock), queue=queue_on(world.clock))

    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))

    after = results(world, step_1)
    assert [row[:2] for row in after] == [row[:2] for row in before]  # rows and status kept
    [(_, _, erased, *_)] = [row for row in after if row[0] == 2]
    flags = json.loads(erased)
    kept = json.loads(before[1][2])
    assert [(flag["rule"], flag["explanation"]) for flag in flags] == [
        (flag["rule"], flag["explanation"]) for flag in kept
    ]
    assert {flag["span"] for flag in flags} == {"[erased]"}
    [(_, _, whole, *_)] = [row for row in after if row[0] == 3]
    assert pairs(json.loads(whole)) == [("word_limit", "")]  # an empty span stays empty
    stored = world.sql("SELECT * FROM grounding_results WHERE draft_id = ?", (step_1,))
    dump = "\n".join(str(row) for row in stored)
    assert "Zelda" not in dump and "Circling" not in dump
    erased_rows = world.sql("SELECT * FROM grounding_results ORDER BY id")
    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))
    assert world.sql("SELECT * FROM grounding_results ORDER BY id") == erased_rows
    assert pairs(latest(world, other_1)[1]) == [("banned_phrase", "Circling back")]
    assert other != contact_id


def test_drafting_declares_the_results_tombstoned_and_exports_them(world: World) -> None:
    assert drafting_api.ERASURE["grounding_results"].coverage == "tombstoned"
    contact_id, ids = drafted(world)
    drafted(world)  # another Contact's results are not exported

    exported = world.read(lambda: drafting_queries.export_pii(contact_id))

    rows = exported["grounding_results"]
    assert sorted((row["draft_id"], row["rev"]) for row in rows) == sorted(
        (draft_id, 1) for draft_id in ids
    )
    assert set(rows[0]) == {
        "id",
        "draft_id",
        "rev",
        "status",
        "flags",
        "rules_version",
        "route_tuple_id",
        "served_model",
        "checked_at",
        "ai_checked_at",  # Story 4.14
    }
    assert world.read(lambda: drafting_queries.export_pii("nope")) == {
        "draft_revisions": (),
        "grounding_results": (),
    }


# Nothing written when the context fails ---------------------------------------------------------


def test_an_unknown_sequence_means_nothing_is_written(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, [step_1, _] = drafted(world)
    before = world.counts()
    monkeypatch.setattr(setup_queries, "sequence_by_id", lambda _sequence_id: None)

    with pytest.raises(LookupError, match="the Draft's Sequence is unknown"):
        revise(world, step_1, SUBJECT, "Hi Zelda,\n\nAnother edit.")

    assert world.counts() == before


def test_no_brief_means_nothing_is_written(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    _, [_, step_2] = drafted(world)
    before = world.counts()
    found = setup_queries.sequence_by_id

    def one_step(sequence_id: str) -> Any:
        sequence = found(sequence_id)
        assert sequence is not None
        steps = sequence.definition.steps[:1]
        return replace(sequence, definition=replace(sequence.definition, steps=steps))

    monkeypatch.setattr(setup_queries, "sequence_by_id", one_step)

    with pytest.raises(LookupError, match="the Draft's Step has no brief"):
        revise(world, step_2, SUBJECT, "Hi Zelda,\n\nAnother edit.")

    assert world.counts() == before


def test_an_unknown_campaign_means_nothing_is_written(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, [step_1, _] = drafted(world)
    before = world.counts()
    monkeypatch.setattr(setup_queries, "campaign", lambda _campaign_id: None)

    with pytest.raises(LookupError, match="Campaign"):
        revise(world, step_1, SUBJECT, "Hi Zelda,\n\nAnother edit.")

    assert world.counts() == before


# The table ----------------------------------------------------------------------------------------


def test_the_table_keeps_one_result_per_existing_revision_and_its_checks(world: World) -> None:
    _, [step_1, _] = drafted(world)
    stamp = "2026-11-18T09:00:00.000000Z"
    insert = (
        "INSERT INTO grounding_results (id, draft_id, rev, status, flags, rules_version, "
        "checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    bad = [
        (("01JD3QZ8X5V9K2M7N4P6R8T0G1", step_1, 1, "pending", "[]", 1, stamp), "UNIQUE"),
        (("01JD3QZ8X5V9K2M7N4P6R8T0G2", step_1, 9, "pending", "[]", 1, stamp), "FOREIGN KEY"),
    ]
    for params, message in bad:
        with pytest.raises(sqlite3.IntegrityError, match=message):
            raw(world, insert, params)
    flag = '[{"rule": "word_limit", "span": "", "explanation": "x"}]'
    update = (
        "UPDATE grounding_results SET status = ?, flags = ?, rules_version = ? "
        "WHERE draft_id = ? AND rev = 1"
    )
    for status, flags, version, check in [
        ("passed", "[]", 1, "ck_grounding_results_status"),
        ("fail", "[]", 1, "ck_grounding_results_fail"),
        ("pending", flag, 1, "ck_grounding_results_fail"),
        ("pending", "{}", 1, "ck_grounding_results_flags"),
        ("pending", "[]", 0, "ck_grounding_results_rules_version"),
    ]:
        with pytest.raises(sqlite3.IntegrityError, match=check):
            raw(world, update, (status, flags, version, step_1))
