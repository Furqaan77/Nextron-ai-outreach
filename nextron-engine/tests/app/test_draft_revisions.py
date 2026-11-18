"""A Draft's text as immutable revisions bound to a hash (FR-20, FR-23, AD-6, AD-17; Story 4.12),
on the world of ``tests.app.test_enroll`` (a temporary database at head, ``FrozenClock``, the real
subscriber registry, the shipped seeds, Law-PrivateAI x US active) with the footer settings
saved:

- **Edit.** ``revise`` appends an ``operator_edit`` revision with the previous traceability and
  a new hash, moves ``current_rev`` and returns the new rev; an empty body still ends with the
  full footer; every refusal writes nothing.
- **Footer change.** ``FooterChanged`` (a footer setting saved, or a seed sync changing a
  Market's template) gives each ``ready`` Draft with text a ``footer_change`` revision; an
  ``approved`` one is untouched; a repeat is a no-op; a Draft that can't be composed is logged
  and skipped.
- **The table.** The triggers refuse a ``DELETE``, a replacing ``INSERT`` and any ``UPDATE`` but
  the erasure tombstone; the CHECKs; ``current_rev`` can only name an existing revision; the
  repository has no update or delete but ``tombstone``.
- **Erasure.** Erasing the Contact tombstones every revision's text, keeping rows and hashes.
- **The Lead page.** The Drafts section shows each Draft, the composed message escaped inside
  ``hx-disable``, the footer notice, the Playbook version and the revision history.
"""

import inspect
import logging
import sqlite3
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.drafting import api as drafting_api
from nextron.app.drafting.api import Drafting, Traceability
from nextron.app.queries import drafting as drafting_queries
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.repos.drafting import revisions as revisions_repo
from nextron.db.uow import current
from nextron.domain import render
from nextron.domain.draft import RevisionOrigin
from nextron.domain.markets import InvalidMarkets, validate_markets
from nextron.domain.notifications import FooterChanged
from tests.app.test_enroll import World, build_world, queue_on, shipped
from tests.app.test_draft_slots import enrolled
from tests.dashboard import Dashboard
from tests.fakes.clock import FrozenClock
from tests.unit.test_leads_page import Tree

SUBJECT = "A question about intake"
BODY = "Hi Zelda,\n\nA short note about your intake."
FOOTER_SETTINGS = {
    "sender_signature": "Furqaan Ahmed",
    "legal_name": "Nextron AI Ltd",
    "postal_address": "1 High Street, London",
}
TRACE = Traceability(
    evidence_ids=("01JD3QZ8X5V9K2M7N4P6R8T0AE",),
    fact_versions=(("offer.audit", 2),),
    cited_signal_id=None,
    route_tuple_id=None,
    playbook_sha256="a" * 64,
    playbook_version="2026.11",
    prompt_version="v3",
    instruction=None,
)
REVISION_COLUMNS = (
    "rev, origin, subject, body, evidence_ids, fact_versions, playbook_sha256, "
    "playbook_version, prompt_version, instruction, text_hash, footer_snapshot_id, "
    "cited_signal_id, route_tuple_id"
)
STAMP = "2026-11-18T09:00:00.000000Z"


def real_trace(world: World, lead_id: str) -> Traceability:
    """``TRACE`` citing a stored Signal of the Lead and a stored route tuple."""
    signal_id, tuple_id = new_id(), new_id()
    [(tuples,)] = world.sql("SELECT COUNT(*) FROM ai_route_tuples")
    version = f"v{tuples + 1}"  # one tuple per call: the tuple columns are unique
    world.sql(  # plain sqlite3, foreign keys off: the tuple's prompt snapshot isn't needed
        "INSERT INTO signals (id, lead_id, signal_type, signal_class, tier, score_only, "
        "event_date, first_seen_at, retracted_at) VALUES (?, ?, 'hiring', 'attribute', 1, 0, "
        "NULL, ?, NULL)",
        (signal_id, lead_id, STAMP),
    )
    world.sql(
        "INSERT INTO ai_route_tuples (id, route, model, effort, prompt_version, prompt_sha256, "
        "playbook_sha256, schema_version, created_at) VALUES (?, 'draft_step', "
        "'claude-opus-5-5', 'high', ?, ?, NULL, 1, ?)",
        (tuple_id, version, "d" * 64, STAMP),
    )
    return replace(TRACE, cited_signal_id=signal_id, route_tuple_id=tuple_id)


def append(
    world: World,
    draft_id: str,
    origin: RevisionOrigin,
    trace: Traceability,
    subject: str = SUBJECT,
    body: str = BODY,
) -> int:
    """A revision written through drafting's private writer."""
    drafting = Drafting(world.clock)

    def write() -> int:
        view = drafting_queries.draft(draft_id)
        assert view is not None
        return drafting._append(view, subject, body, origin, trace, world.clock.now())

    return world.transact(write)


@pytest.fixture
def world(db_engine: Engine) -> World:
    found = build_world(db_engine)
    with found.units.open():
        Setup(found.clock).save(FOOTER_SETTINGS)
    return found


def drafted(world: World, steps: tuple[int, ...] = (1, 2)) -> tuple[str, list[str]]:
    """A new enrolled Contact with revision 1 on each Draft: (contact, draft ids by Step)."""
    lead_id, contact_id, enrollment_id = enrolled(world, steps=steps)
    trace = real_trace(world, lead_id)
    ids = ids_of(world, enrollment_id)
    for draft_id in ids:
        assert append(world, draft_id, RevisionOrigin.GENERATED, trace) == 1
    return contact_id, ids


def ids_of(world: World, enrollment_id: str) -> list[str]:
    rows = world.sql(
        "SELECT id FROM drafts WHERE enrollment_id = ? ORDER BY step_no", (enrollment_id,)
    )
    return [str(row[0]) for row in rows]


def move_to_uk(world: World, contact_id: str) -> None:
    """Make the Contact a UK Contact (an enrolled UK Contact can't be built yet, Story 2.12)."""
    world.sql(
        "UPDATE contacts SET market_id = (SELECT id FROM markets WHERE code = 'uk') WHERE id = ?",
        (contact_id,),
    )


def revisions(world: World, draft_id: str) -> list[tuple[Any, ...]]:
    return world.sql(
        f"SELECT {REVISION_COLUMNS} FROM draft_revisions WHERE draft_id = ? ORDER BY rev",
        (draft_id,),
    )


def current_rev(world: World, draft_id: str) -> int | None:
    found = world.sql("SELECT current_rev FROM drafts WHERE id = ?", (draft_id,))[0][0]
    return None if found is None else int(found)


def footer_text(world: World, snapshot_id: str) -> str:
    return str(world.sql("SELECT text FROM footer_snapshots WHERE id = ?", (snapshot_id,))[0][0])


def expected_hash(world: World, draft_id: str, subject: str, body: str, snapshot: str) -> str:
    [(email, inbox)] = world.sql(
        "SELECT c.email, e.sending_inbox_id FROM drafts d JOIN enrollments e "
        "ON e.id = d.enrollment_id JOIN contacts c ON c.id = e.contact_id WHERE d.id = ?",
        (draft_id,),
    )
    composed = body + "\n\n" + footer_text(world, snapshot)
    return render.text_hash(email, inbox, subject, composed)


def revise(world: World, draft_id: str, subject: object = SUBJECT, body: object = BODY) -> int:
    drafting = Drafting(world.clock)
    return world.transact(lambda: drafting.revise(draft_id, subject, body))  # type: ignore[arg-type]


def save_footer(world: World, **values: str) -> None:
    with world.units.open():
        Setup(world.clock).save(values)


def raw(world: World, statement: str, params: tuple[object, ...] = ()) -> None:
    connection = sqlite3.connect(world.db_file, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(statement, params)
    finally:
        connection.close()


# Revision 1 ---------------------------------------------------------------------------------------


def test_revision_1_holds_the_text_its_footer_and_its_hash(world: World) -> None:
    _, [step_1, step_2] = drafted(world)

    [row] = revisions(world, step_1)
    rev, origin, subject, body, evidence, facts, sha, version, prompt, instruction = row[:10]
    digest, snap, signal_id, tuple_id = row[10:]
    assert signal_id is not None and tuple_id is not None
    assert (rev, origin, subject, body) == (1, "generated", SUBJECT, BODY)
    assert (evidence, facts) == ('["01JD3QZ8X5V9K2M7N4P6R8T0AE"]', '[["offer.audit", 2]]')
    assert (sha, version, prompt, instruction) == ("a" * 64, "2026.11", "v3", None)
    assert digest == expected_hash(world, step_1, SUBJECT, BODY, snap)
    text = footer_text(world, snap)
    assert text.startswith("Furqaan Ahmed\nNextron AI Ltd\n1 High Street, London\n")
    assert current_rev(world, step_1) == 1
    # one snapshot per distinct footer text
    assert revisions(world, step_2)[0][11] == snap


# Edit ---------------------------------------------------------------------------------------------


def test_revise_appends_an_operator_edit_and_moves_current_rev(world: World) -> None:
    _, [step_1, _] = drafted(world)
    [first] = revisions(world, step_1)
    world.clock.set(world.clock.now() + timedelta(minutes=5))

    rev = revise(world, step_1, "  A better subject  ", "New body")

    assert rev == 2
    assert current_rev(world, step_1) == 2
    _, second = revisions(world, step_1)
    assert second[:4] == (2, "operator_edit", "A better subject", "New body")
    assert second[4:9] == first[4:9]  # the traceability is copied
    assert second[12:] == first[12:]  # the cited Signal and the route tuple too
    assert None not in first[12:]
    assert second[9] is None
    assert second[10] != first[10]
    assert second[10] == expected_hash(world, step_1, "A better subject", "New body", second[11])
    assert revisions(world, step_1)[0] == first  # revision 1 keeps the AI's text
    assert world.sql("SELECT updated_at FROM drafts WHERE id = ?", (step_1,))[0][0].startswith(
        "2026-11-18T10:05"
    )


def test_an_empty_body_still_ends_with_the_full_footer(world: World) -> None:
    _, [step_1, _] = drafted(world)

    revise(world, step_1, SUBJECT, "")

    _, second = revisions(world, step_1)
    assert second[3] == ""
    assert second[10] == expected_hash(world, step_1, SUBJECT, "", second[11])
    view = world.read(lambda: drafting_queries.revision(step_1, 2))
    snapshot = world.read(lambda: drafting_queries.footer_snapshot(second[11]))
    assert view is not None and snapshot is not None
    composed = render.compose(view, snapshot, "zelda@firm.example")
    assert composed.body == "\n\n" + snapshot.text


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ((7, SUBJECT, BODY), TypeError),
        (("{draft}", None, BODY), TypeError),
        (("{draft}", SUBJECT, b"body"), TypeError),
        (("{draft}", "   ", BODY), ValueError),
        (("{draft}", "x" * 201, BODY), ValueError),
        (("{draft}", "Line one\r\nBcc: x@y.example", BODY), ValueError),
        (("{draft}", "Line one two", BODY), ValueError),
        (("{draft}", "Bell\x07 in the subject", BODY), ValueError),  # a control character
        (("{draft}", "Tab\tinside", BODY), ValueError),
        (("{draft}", SUBJECT, "x" * 10_001), ValueError),
        (("01JD3QZ8X5V9K2M7N4P6R8T0AB", SUBJECT, BODY), LookupError),
        (("not-a-ulid", SUBJECT, BODY), LookupError),
        (("{empty}", SUBJECT, BODY), ValueError),  # no revision yet
        (("{approved}", SUBJECT, BODY), ValueError),
    ],
)
def test_a_refused_edit_writes_nothing(
    world: World, args: tuple[object, object, object], error: type[Exception]
) -> None:
    _, [step_1, step_2] = drafted(world)
    _, _, other_enrollment = enrolled(world, steps=(1,))
    [(empty,)] = world.sql("SELECT id FROM drafts WHERE enrollment_id = ?", (other_enrollment,))
    world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (step_2,))
    names = {"{draft}": step_1, "{empty}": empty, "{approved}": step_2}
    draft_id = names.get(args[0], args[0]) if isinstance(args[0], str) else args[0]
    before = world.counts()

    with pytest.raises(error):
        revise(world, draft_id, args[1], args[2])  # type: ignore[arg-type]

    assert world.counts() == before
    assert current_rev(world, step_1) == 1


def test_the_longest_subject_and_body_are_accepted(world: World) -> None:
    _, [step_1, _] = drafted(world)

    assert revise(world, step_1, "s" * 200, "b" * 10_000) == 2


# Footer change ------------------------------------------------------------------------------------


def test_a_footer_change_recomposes_ready_drafts_only(world: World) -> None:
    _, [ready, approved] = drafted(world)
    world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (approved,))
    _, _, untexted = enrolled(world, steps=(1,))
    [first] = revisions(world, ready)

    save_footer(world, legal_name="Nextron AI Limited")

    _, second = revisions(world, ready)
    assert second[:4] == (2, "footer_change", SUBJECT, BODY)
    assert second[4:10] == first[4:10]
    assert second[12:] == first[12:] and None not in second[12:]
    assert second[11] != first[11]  # a new footer snapshot
    assert "Nextron AI Limited" in footer_text(world, second[11])
    assert second[10] == expected_hash(world, ready, SUBJECT, BODY, second[11])
    assert second[10] != first[10]
    assert current_rev(world, ready) == 2
    assert len(revisions(world, approved)) == 1  # approved: untouched
    assert world.sql(
        "SELECT current_rev FROM drafts WHERE enrollment_id = ?", (untexted,)
    ) == [(None,)]


def test_a_repeated_footer_change_is_a_no_op(world: World) -> None:
    _, [ready, _] = drafted(world)
    save_footer(world, legal_name="Nextron AI Limited")
    [(market_id,)] = world.sql("SELECT id FROM markets WHERE code = 'us'")
    before = world.counts()

    world.transact(lambda: current().publish(FooterChanged(market_id=market_id)))

    assert world.counts() == before
    assert current_rev(world, ready) == 2


def test_another_markets_footer_change_leaves_the_draft_alone(world: World) -> None:
    _, [ready, _] = drafted(world)
    [(uk,)] = world.sql("SELECT id FROM markets WHERE code = 'uk'")
    save_footer(world, legal_name="Nextron AI Limited")  # recomposes the US Draft once
    before = world.counts()

    world.transact(lambda: current().publish(FooterChanged(market_id=uk)))

    assert world.counts() == before


def test_a_draft_that_cant_be_composed_is_logged_and_skipped(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, [ready, _] = drafted(world)
    monkeypatch.setattr(drafting_api.setup_queries, "market_by_id", lambda market_id: None)
    before = world.counts()

    with caplog.at_level(logging.WARNING, logger=drafting_api.__name__):
        save_footer(world, legal_name="Nextron AI Limited")

    assert current_rev(world, ready) == 1
    assert world.counts()["draft_revisions"] == before["draft_revisions"]
    skipped = [r for r in caplog.records if r.getMessage() == drafting_api.RECOMPOSE_SKIPPED]
    assert len(skipped) == 2
    assert {getattr(r, "error_type") for r in skipped} == {"LookupError"}


def test_a_seed_sync_that_changes_a_footer_template_recomposes(world: World) -> None:
    _, [ready, _] = drafted(world)
    markets = shipped(main.MARKETS_FILE_NAME, validate_markets)
    changed = tuple(
        replace(market, footer_template=(*market.footer_template, "Sent from Manchester."))
        if market.code == "us"
        else market
        for market in markets
    )

    with world.units.open():
        Setup(world.clock).sync_markets(changed)

    _, second = revisions(world, ready)
    assert second[1] == "footer_change"
    assert footer_text(world, second[11]).endswith("Sent from Manchester.")
    with world.units.open():  # the same template again: nothing changes
        Setup(world.clock).sync_markets(changed)
    assert len(revisions(world, ready)) == 2


def test_a_seed_sync_that_changes_nothing_in_the_footer_publishes_nothing(world: World) -> None:
    _, [ready, _] = drafted(world)
    markets = shipped(main.MARKETS_FILE_NAME, validate_markets)
    changed = tuple(
        replace(market, name=market.name + " (renamed)") if market.code == "us" else market
        for market in markets
    )

    with world.units.open():
        Setup(world.clock).sync_markets(changed)

    assert len(revisions(world, ready)) == 1


def test_a_seed_sync_that_turns_the_privacy_line_on_recomposes(world: World) -> None:
    _, [ready, _] = drafted(world)
    markets = shipped(main.MARKETS_FILE_NAME, validate_markets)
    # validation ties the flag to the template's {privacy_line}, so both change together
    changed = tuple(
        replace(
            market,
            privacy_line_required=True,
            footer_template=(*market.footer_template, "{privacy_line}"),
        )
        if market.code == "us"
        else market
        for market in markets
    )

    with world.units.open():
        Setup(world.clock).sync_markets(changed)

    _, second = revisions(world, ready)
    assert second[1] == "footer_change"
    assert footer_text(world, second[11]).endswith(
        "Your details came from your firm's website."
    )


# UK -----------------------------------------------------------------------------------------------


def uk_drafted(world: World) -> tuple[str, str]:
    """A US-enrolled Contact moved to the UK Market, before its Draft gets text: (contact,
    Step 1 Draft)."""
    lead_id, contact_id, enrollment_id = enrolled(world, steps=(1,))
    move_to_uk(world, contact_id)
    [draft_id] = ids_of(world, enrollment_id)
    return contact_id, draft_id


def test_a_uk_footer_names_the_earliest_contact_source(world: World) -> None:
    contact_id, draft_id = uk_drafted(world)
    later = "2027-01-01T00:00:00.000000Z"
    world.sql(  # a later contact record from another source: not the one named
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        "address, retrieved_at) VALUES (?, ?, 'contact', 'csv', 'A later list', NULL, ?)",
        (new_id(), contact_id, later),
    )

    append(world, draft_id, RevisionOrigin.GENERATED, TRACE)

    [row] = revisions(world, draft_id)
    text = footer_text(world, row[11])
    assert "Your details came from your firm's website." in text
    assert "a business list" not in text


def no_phrase(world: World, contact_id: str) -> None:
    """Every Provenance source of the Contact becomes one without a privacy-line phrase."""
    world.sql(
        "UPDATE provenance_records SET source_kind = 'operator' WHERE contact_id = ? "
        "AND source_kind <> 'eligibility_basis'",
        (contact_id,),
    )


def test_a_uk_source_without_a_phrase_fails_closed(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    contact_id, [ready, _] = drafted(world)  # US text first
    move_to_uk(world, contact_id)
    no_phrase(world, contact_id)
    before = world.counts()

    with pytest.raises(ValueError, match=render.NO_PHRASE):
        revise(world, ready, "Edited", "Edited body")
    assert world.counts() == before

    with caplog.at_level(logging.WARNING, logger=drafting_api.__name__):
        save_footer(world, legal_name="Nextron AI Limited")

    assert current_rev(world, ready) == 1
    skipped = [r for r in caplog.records if r.getMessage() == drafting_api.RECOMPOSE_SKIPPED]
    assert {getattr(r, "error_type") for r in skipped} == {"ValueError"}
    assert any(getattr(r, "draft_id") == ready for r in skipped)
    [(saved,)] = world.sql("SELECT value FROM settings WHERE key = 'legal_name'")
    assert "Nextron AI Limited" in str(saved)  # the save still committed


def test_unreadable_markets_are_skipped_not_rolled_back(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, [ready, _] = drafted(world)

    def unreadable(market_id: str) -> None:
        raise InvalidMarkets("markets[us]", "no longer validates")

    monkeypatch.setattr(drafting_api.setup_queries, "market_by_id", unreadable)

    with caplog.at_level(logging.WARNING, logger=drafting_api.__name__):
        save_footer(world, legal_name="Nextron AI Limited")

    assert current_rev(world, ready) == 1
    skipped = [r for r in caplog.records if r.getMessage() == drafting_api.RECOMPOSE_SKIPPED]
    assert {getattr(r, "error_type") for r in skipped} == {"InvalidMarkets"}
    [(saved,)] = world.sql("SELECT value FROM settings WHERE key = 'legal_name'")
    assert "Nextron AI Limited" in str(saved)


# The table ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM draft_revisions WHERE draft_id = ?",
        "UPDATE draft_revisions SET text_hash = '" + "b" * 64 + "' WHERE draft_id = ?",
        "UPDATE draft_revisions SET subject = 'Changed' WHERE draft_id = ?",
        "UPDATE draft_revisions SET body = 'Changed' WHERE draft_id = ?",
        "UPDATE draft_revisions SET rev = 5 WHERE draft_id = ?",
        "UPDATE draft_revisions SET evidence_ids = '[]' WHERE draft_id = ?",
        "UPDATE draft_revisions SET created_at = '2027-01-01T00:00:00.000000Z' "
        "WHERE draft_id = ?",
        "INSERT OR REPLACE INTO draft_revisions SELECT * FROM draft_revisions WHERE draft_id = ?",
        "UPDATE draft_revisions SET instruction = 'Shorter' WHERE draft_id = ?",
        "UPDATE draft_revisions SET footer_snapshot_id = '01JD3QZ8X5V9K2M7N4P6R8T0AG' "
        "WHERE draft_id = ?",
        "UPDATE draft_revisions SET origin = 'regenerated' WHERE draft_id = ?",
        "UPDATE draft_revisions SET draft_id = '01JD3QZ8X5V9K2M7N4P6R8T0AH' WHERE draft_id = ?",
        # a plain duplicate of an existing (draft_id, rev), under a new id
        "INSERT INTO draft_revisions (id, draft_id, rev, subject, body, footer_snapshot_id, "
        "evidence_ids, fact_versions, origin, text_hash, created_at) SELECT "
        "'01JD3QZ8X5V9K2M7N4P6R8T0AJ', draft_id, rev, subject, body, footer_snapshot_id, "
        "evidence_ids, fact_versions, origin, text_hash, created_at FROM draft_revisions "
        "WHERE draft_id = ?",
    ],
)
def test_a_revision_is_immutable(world: World, statement: str) -> None:
    _, [step_1, _] = drafted(world)
    before = revisions(world, step_1)

    with pytest.raises(sqlite3.IntegrityError, match="a Draft revision is immutable"):
        raw(world, statement, (step_1,))

    assert revisions(world, step_1) == before


def test_the_erasure_tombstone_is_the_one_update_allowed(world: World) -> None:
    _, [step_1, _] = drafted(world)

    raw(
        world,
        "UPDATE draft_revisions SET subject = '[erased]', body = '[erased]' WHERE draft_id = ?",
        (step_1,),
    )

    assert revisions(world, step_1)[0][2:4] == ("[erased]", "[erased]")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):  # never refilled
        raw(world, "UPDATE draft_revisions SET subject = 'Back' WHERE draft_id = ?", (step_1,))


def insert_revision(world: World, target: str, **changes: object) -> None:
    [(snapshot,)] = world.sql("SELECT footer_snapshot_id FROM draft_revisions LIMIT 1")
    values: dict[str, object] = {
        "id": "01JD3QZ8X5V9K2M7N4P6R8T0AF",
        "draft_id": target,
        "rev": 2,
        "subject": "s",
        "body": "b",
        "footer_snapshot_id": snapshot,
        "evidence_ids": "[]",
        "fact_versions": "[]",
        "origin": "operator_edit",
        "instruction": None,
        "text_hash": "c" * 64,
        "playbook_sha256": None,
        "created_at": "2026-11-18T10:00:00.000000Z",
    }
    values.update(changes)
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    raw(world, f"INSERT INTO draft_revisions ({columns}) VALUES ({marks})", tuple(values.values()))


@pytest.mark.parametrize(
    ("changes", "check"),
    [
        ({"rev": 1, "origin": "generated", "draft_id": "{other}"}, None),
        ({"rev": 1, "origin": "operator_edit", "draft_id": "{other}"}, "first"),
        ({"origin": "generated"}, "first"),
        ({"rev": 0, "origin": "operator_edit"}, "rev"),
        ({"origin": "pasted"}, "origin"),
        ({"text_hash": "C" * 64}, "text_hash"),
        ({"text_hash": "c" * 63}, "text_hash"),
        ({"playbook_sha256": "x" * 64}, "playbook_sha256"),
        ({"instruction": "shorter"}, "instruction"),
        ({"origin": "regenerated", "instruction": "x" * 201}, "instruction"),
        ({"origin": "regenerated", "instruction": "x" * 200}, None),
        ({"evidence_ids": "{}"}, "evidence_ids"),
        ({"fact_versions": "not json"}, "fact_versions"),
    ],
)
def test_the_checks(world: World, changes: dict[str, object], check: str | None) -> None:
    _, [step_1, step_2] = drafted(world)
    _, _, other = enrolled(world, steps=(1,))
    [(other_draft,)] = world.sql("SELECT id FROM drafts WHERE enrollment_id = ?", (other,))
    chosen = {
        key: other_draft if value == "{other}" else value for key, value in changes.items()
    }
    target = str(chosen.pop("draft_id", step_1))

    if check is None:
        insert_revision(world, target, **chosen)
    else:
        with pytest.raises(sqlite3.IntegrityError, match=f"ck_draft_revisions_{check}"):
            insert_revision(world, target, **chosen)


def test_current_rev_names_only_an_existing_revision(world: World) -> None:
    _, [step_1, step_2] = drafted(world)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        raw(world, "UPDATE drafts SET current_rev = 2 WHERE id = ?", (step_1,))
    [(rev_of_2,)] = world.sql("SELECT rev FROM draft_revisions WHERE draft_id = ?", (step_2,))
    assert rev_of_2 == 1
    raw(world, "UPDATE drafts SET current_rev = NULL WHERE id = ?", (step_1,))  # names none
    assert current_rev(world, step_1) is None


def test_the_repository_has_no_update_or_delete_but_tombstone() -> None:
    names = {
        name
        for name, member in inspect.getmembers(revisions_repo, inspect.isfunction)
        if member.__module__ == revisions_repo.__name__ and not name.startswith("__")
    }

    # for_export: Story 3.15's subject access export, a read (added at the merge of Story 4.12)
    assert names == {
        "insert",
        "get",
        "of_draft",
        "of_drafts",
        "tombstone",
        "for_export",
        "_stored",
    }
    source = inspect.getsource(revisions_repo)
    assert "delete" not in source.lower().replace("no update or delete", "")


# Erasure ------------------------------------------------------------------------------------------


def test_erasing_the_contact_tombstones_its_revisions_text(world: World) -> None:
    contact_id, [step_1, step_2] = drafted(world)
    revise(world, step_1, "Edited", "Edited body")
    regenerated = replace(TRACE, instruction="Make it shorter for Zelda")
    assert append(world, step_2, RevisionOrigin.REGENERATED, regenerated) == 2
    assert revisions(world, step_2)[1][9] == "Make it shorter for Zelda"
    other_contact, [other_1, _] = drafted(world)
    before_hashes = [row[10] for row in revisions(world, step_1)]
    erasures = Erasures(world.clock, Compliance(world.clock), queue=queue_on(world.clock))

    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))

    for draft_id in (step_1, step_2):
        for row in revisions(world, draft_id):
            assert row[2:4] == ("[erased]", "[erased]")
    assert [row[9] for row in revisions(world, step_1)] == [None, None]  # NULL stays NULL
    assert [row[9] for row in revisions(world, step_2)] == [None, "[erased]"]
    assert [row[10] for row in revisions(world, step_1)] == before_hashes
    assert len(revisions(world, step_1)) == 2
    erased_rows = world.sql("SELECT * FROM draft_revisions ORDER BY id")
    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))
    assert world.sql("SELECT * FROM draft_revisions ORDER BY id") == erased_rows  # no change
    assert revisions(world, other_1)[0][2:4] == (SUBJECT, BODY)  # another Contact's: kept
    dump = "\n".join(str(row) for row in world.sql("SELECT * FROM draft_revisions"))
    assert "Edited body" not in dump
    assert other_contact != contact_id


def test_drafting_declares_the_revisions_tombstoned() -> None:
    assert drafting_api.ERASURE["draft_revisions"].coverage == "tombstoned"


# The Lead page ------------------------------------------------------------------------------------


def lead_page(world: World, lead_id: str) -> Tree:
    dashboard = Dashboard(units=world.units, clock=FrozenClock(world.clock.now()))
    cookie = dashboard.open_session()
    reply = dashboard.get(f"/leads/{lead_id}", cookie=cookie)
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return Tree(reply.text)


def test_the_lead_page_shows_each_draft_and_its_history(world: World) -> None:
    lead_id, contact_id, enrollment_id = enrolled(world, steps=(1, 2, 3))
    ids = [
        str(row[0])
        for row in world.sql(
            "SELECT id FROM drafts WHERE enrollment_id = ? ORDER BY step_no", (enrollment_id,)
        )
    ]
    drafting = Drafting(world.clock)
    hostile = "<script>alert(1)</script> & <img src=x>"

    def first() -> int:
        view = drafting_queries.draft(ids[0])
        assert view is not None
        body = "Body line\n" + hostile
        return drafting._append(
            view, hostile, body, RevisionOrigin.GENERATED, TRACE, world.clock.now()
        )

    world.transact(first)
    revise(world, ids[0], "Edited subject", "Body line\n" + hostile)
    [(email,)] = world.sql("SELECT email FROM contacts WHERE id = ?", (contact_id,))

    page = lead_page(world, lead_id)

    section = page.find("section", class_="lead-drafts")
    assert "hx-disable" in section.attrs
    first_draft, second, third = section.find_all("article", class_="draft")
    assert first_draft.find("h3").text == "Sequence Step: Step 1"
    facts = [node.text for node in first_draft.find_all("dd")]
    assert facts[0] == "Ready"
    assert facts[1].startswith("2026-11-18 10:00 UTC")
    assert facts[2] == "2026.11"
    assert first_draft.find("p", class_="draft-to").text == f"To: {email}"
    assert first_draft.find("p", class_="draft-subject").text == "Subject: Edited subject"
    body = first_draft.find("pre", class_="draft-body")
    raw_body = "".join(child for child in body.children if isinstance(child, str))
    assert raw_body.startswith("Body line\n" + hostile + "\n\nFurqaan Ahmed\n")
    assert not section.find_all("script") and not section.find_all("img")
    history = first_draft.find("table", class_="revision-table")
    rows = history.find_all("tr", class_="revision-row")
    hashes = [row[10] for row in revisions(world, ids[0])]
    assert [_cells(row) for row in rows] == [
        ["1", "Generated", "2026-11-18 10:00 UTC", hashes[0][:12]],
        ["2", "Edited by you", "2026-11-18 10:00 UTC", hashes[1][:12]],
    ]
    assert second.find("p", class_="draft-no-text").text == "No text yet"
    assert [node.text for node in second.find_all("dd")][1] == "after Step 1 is sent"
    assert [node.text for node in third.find_all("dd")][1] == "after Step 2 is sent"
    assert not page.find_all("p", class_="draft-blocked")
    assert all(page.disabled("<script>"))


def _cells(row: Any) -> list[str]:
    return [child.text for child in row.children if not isinstance(child, str)]


def test_the_lead_page_says_when_the_footer_blocks_sending(world: World) -> None:
    lead_id, _, enrollment_id = enrolled(world, steps=(1,))
    [(draft_id,)] = world.sql("SELECT id FROM drafts WHERE enrollment_id = ?", (enrollment_id,))
    world.sql("DELETE FROM settings WHERE key = 'postal_address'")

    page = lead_page(world, lead_id)

    [notice] = page.find_all("p", class_="draft-blocked")
    assert notice.text == "Sending blocked: footer incomplete (Postal address)"
    assert notice.attrs.get("role") == "alert"
    assert draft_id


def test_a_lead_without_drafts_says_so(world: World) -> None:
    lead_id, _ = world.contact()

    page = lead_page(world, lead_id)

    assert page.find("section", class_="lead-drafts").text == "Drafts No Drafts yet."
