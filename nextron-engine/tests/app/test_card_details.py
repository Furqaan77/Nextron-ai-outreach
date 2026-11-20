"""The Evidence, facts and grounding result behind every Approval Queue card (FR-26, AD-6, AD-7,
AD-13; Story 5.3), on the world of ``tests.app.test_due_queue`` (``tests.app.test_draft_revisions``:
a temporary database at head, ``FrozenClock`` at 2026-11-18 10:00 UTC, the shipped seeds,
Law-PrivateAI x US active, the footer settings saved).

The spec's I/O matrix, through ``queries.approval`` and ``GET /queue``: the Evidence rows (Quote,
Absence, Count, missing, superseded, retracted; links only for ``http``/``https``), the fact
versions (the cited version's wording, missing, retracted), earlier sent Steps, the grounding
result (``fail`` highlights and notices, ``pending`` or no row, ``pass``), the ``generic`` and
``stale`` badges, the ``shown_hash`` binding (equal to the hash ``Sending.enqueue`` checks for a
Step 1, a follow-up and a UK card), the ``?draft=`` target, escaping, and a page load that writes
nothing and reads the details of the focused card only.
"""

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from nextron.app.compliance.api import Compliance
from nextron.app.queries import approval as approval_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries.approval import (
    EVIDENCE_LIVE,
    EVIDENCE_RETRACTED,
    EVIDENCE_SUPERSEDED,
    CardDetails,
    QueueCard,
)
from nextron.app.sending.api import Sending
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.sending import outbound_messages as outbound_repo
from nextron.domain import render
from nextron.domain.company_facts import InvalidCompanyFacts
from nextron.domain.draft import RevisionOrigin
from nextron.domain.grounding_rules import GroundingStatus
from nextron.web import queue_page
from nextron.web.queue_page import Segment
from tests.app.test_draft_revisions import TRACE, append, move_to_uk, world
from tests.app.test_due_queue import (
    ELIGIBLE,
    NOW,
    build,
    dashboard_on,
    dump,
    queue_page_of,
    responsive,
    set_due,
    slot,
)
from tests.app.test_enroll import World
from tests.app.test_outbound_messages import StandInApproval
from tests.fakes.token_codec import FakeTokenCodec
from tests.unit.test_leads_page import Node, Tree

__all__ = ["world"]  # the fixture

SAFE_URL = "https://firm.example.com/about"
RULE = "word_limit"
STAMP = "2026-11-18T09:00:00.000000Z"


# Helpers ------------------------------------------------------------------------------------------


def text_slot(
    world: World, steps: tuple[int, ...] = (1,), **trace: Any
) -> tuple[str, str, str, list[str]]:
    """``slot`` with revision 1 written through ``TRACE`` changed by ``trace``."""
    lead_id, contact_id, enrollment_id, ids = slot(world, steps, text=False)
    for draft_id in ids:
        append(world, draft_id, RevisionOrigin.GENERATED, replace(TRACE, **trace))
    return lead_id, contact_id, enrollment_id, ids


def evidence(world: World, lead_id: str, kind: str, **fields: Any) -> str:
    row: dict[str, Any] = {
        "quote": None,
        "sought": None,
        "number": None,
        "fact": None,
        "value": None,
        "pages_checked": (SAFE_URL,),
        "source": "website",
        "source_url": SAFE_URL,
        "extracted_by": "rule",
    } | fields
    return world.transact(
        lambda: signals_repo.insert_evidence(
            lead_id=lead_id,
            evidence_type=kind,
            retrieved_at=NOW - timedelta(days=1),
            person_contact_id=None,
            at=NOW - timedelta(days=1),
            **row,
        )
    )


def set_grounding(
    world: World, draft_id: str, status: str, flags: tuple[tuple[str, str, str], ...] = ()
) -> None:
    encoded = json.dumps(
        [{"rule": rule, "span": span, "explanation": text} for rule, span, text in flags]
    )
    # a pass row carries the AI check's tuple, model and time (plain sqlite3: no foreign keys)
    passed = status == "pass"
    world.sql(
        "UPDATE grounding_results SET status = ?, flags = ?, route_tuple_id = ?, "
        "served_model = ?, ai_checked_at = ? WHERE draft_id = ?",
        (
            status,
            encoded,
            new_id() if passed else None,
            "claude-haiku-5-5" if passed else None,
            STAMP if passed else None,
            draft_id,
        ),
    )


def details_of(world: World, draft_id: str) -> tuple[QueueCard, CardDetails]:
    def read() -> tuple[QueueCard, CardDetails]:
        queue = approval_queries.due_queue(world.clock.now(), lambda _: ELIGIBLE)
        [card] = [each for each in queue.cards if each.draft_id == draft_id]
        return card, approval_queries.card_details(card)

    return world.read(read)


def page_of(world: World, query: bytes = b"") -> Tree:
    dashboard = dashboard_on(world)
    return queue_page_of(dashboard, dashboard.open_session(), query)


def raw_text(node: Node) -> str:
    """The node's text exactly as sent (unescaped, whitespace kept)."""
    parts: list[str] = []

    def collect(each: Node) -> None:
        for child in each.children:
            if isinstance(child, Node):
                collect(child)
            else:
                parts.append(child)

    collect(node)
    return "".join(parts)


def hidden(page: Tree, name: str) -> str:
    [found] = [node for node in page.find_all("input") if node.attrs.get("name") == name]
    assert found.attrs["type"] == "hidden"
    return str(found.attrs.get("value") or "")


def inbox_of(world: World, enrollment_id: str) -> str:
    [(inbox,)] = world.sql(
        "SELECT sending_inbox_id FROM enrollments WHERE id = ?", (enrollment_id,)
    )
    return str(inbox)


def sending_on(world: World) -> Sending:
    compliance = Compliance(world.clock, codec=FakeTokenCodec())
    return Sending(world.clock, env="test", compliance=compliance)


def enqueue_with(world: World, draft_id: str, digest: str) -> str:
    """Approve the Draft by plain SQL with ``digest`` as the approval's hash and enqueue it; the
    OM id (``Sending.enqueue`` raises ``HashMismatch`` when ``digest`` isn't its hash)."""
    world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (draft_id,))
    [(rev,)] = world.sql("SELECT current_rev FROM drafts WHERE id = ?", (draft_id,))
    approval = StandInApproval(new_id(), draft_id, int(rev), digest)
    sending = sending_on(world)
    return world.transact(lambda: sending.enqueue(draft_id, approval))


def mark_sent(world: World, om_id: str, at: Any) -> None:
    assert world.transact(lambda: outbound_repo.set_status(om_id, "queued", "sending", at))
    assert world.transact(lambda: outbound_repo.set_status(om_id, "sending", "sent", at))


def links_are_safe(node: Node) -> list[str]:
    hrefs = []
    for link in node.find_all("a"):
        assert link.attrs.get("rel") == "noopener noreferrer"
        href = str(link.attrs["href"])
        assert href.startswith(("http://", "https://"))
        hrefs.append(href)
    return hrefs


# Evidence -----------------------------------------------------------------------------------------


def test_each_evidence_type_through_the_query_and_the_page(world: World) -> None:
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "America/New_York"})
    lead_id, _, _, [first] = slot(world, text=False)
    quote = evidence(
        world,
        lead_id,
        "quote",
        quote="<b>We</b> handle intake.",
        fact="location",
        value="Boston",
        extracted_by="ai:extract_signals@v1",
    )
    absence = evidence(
        world,
        lead_id,
        "absence",
        sought="a pricing page",
        pages_checked=(SAFE_URL, "https://firm.example.com/fees"),
        source_url="javascript:alert(1)",
    )
    count = evidence(
        world,
        lead_id,
        "count",
        number=12,
        sought="lawyers",
        source_url="ftp://firm.example.com/team",
        extracted_by="operator",
    )
    no_source = evidence(world, lead_id, "quote", quote="Plain quote", source_url=None)
    missing = new_id()
    cited = (quote, absence, count, missing, no_source)
    append(world, first, RevisionOrigin.GENERATED, replace(TRACE, evidence_ids=cited))

    _, details = details_of(world, first)

    assert [row.id for row in details.evidence] == list(cited)
    by_id = {row.id: row for row in details.evidence}
    assert by_id[quote].found and by_id[quote].type == "quote"
    assert by_id[quote].quote == "<b>We</b> handle intake."
    assert (by_id[quote].fact, by_id[quote].value) == ("location", "Boston")
    assert by_id[quote].ai_extracted and by_id[quote].state == EVIDENCE_LIVE
    assert by_id[absence].sought == "a pricing page" and not by_id[absence].ai_extracted
    assert by_id[count].number == 12 and not by_id[count].ai_extracted
    assert not by_id[missing].found

    page = page_of(world)

    section = page.find("section", class_="queue-evidence")
    rows = section.find_all("li", class_="evidence-row")
    assert len(rows) == 5
    quote_row, absence_row, count_row, missing_row, plain_row = rows
    assert quote_row.find("span", class_="evidence-type").text == "Quote"
    assert raw_text(quote_row.find("span", class_="evidence-text")) == "<b>We</b> handle intake."
    assert not quote_row.find_all("b")
    assert quote_row.find("span", class_="evidence-detail").text == "location: Boston"
    assert quote_row.find("span", class_="evidence-ai").text == "AI-extracted"
    retrieved = quote_row.find("span", class_="evidence-retrieved").text
    assert retrieved == "2026-11-17 05:00 America/New_York"
    assert links_are_safe(quote_row.find("span", class_="evidence-source")) == [SAFE_URL]
    assert absence_row.find("span", class_="evidence-type").text == "Absence"
    assert absence_row.find("span", class_="evidence-text").text == "a pricing page"
    assert links_are_safe(absence_row.find("ul", class_="pages-checked")) == [
        SAFE_URL,
        "https://firm.example.com/fees",
    ]
    absence_source = absence_row.find("span", class_="evidence-source")
    assert not absence_source.find_all("a") and absence_source.text == "javascript:alert(1)"
    assert not absence_row.find_all("span", class_="evidence-ai")
    assert count_row.find("span", class_="evidence-type").text == "Count"
    assert count_row.find("span", class_="evidence-text").text == "12"
    assert count_row.find("span", class_="evidence-detail").text == "lawyers"
    count_source = count_row.find("span", class_="evidence-source")
    assert not count_source.find_all("a") and count_source.text == "ftp://firm.example.com/team"
    assert not count_row.find_all("span", class_="evidence-ai")
    assert missing_row.text == f"Evidence not found: {missing}"
    assert plain_row.find("span", class_="evidence-source").text == "—"
    links_are_safe(page.find("article", class_="queue-card"))
    assert all(page.disabled("<b>We</b>"))


def test_superseded_and_retracted_evidence_still_shows_labelled(world: World) -> None:
    lead_id, _, enrollment_id, [draft_id] = slot(world, text=False)
    old = evidence(world, lead_id, "quote", quote="Old wording")
    new = evidence(world, lead_id, "quote", quote="New wording")
    gone = evidence(world, lead_id, "quote", quote="Gone wording")
    world.transact(lambda: signals_repo.supersede_evidence(old, new))
    world.transact(lambda: signals_repo.retract_evidence(gone, NOW))
    append(world, draft_id, RevisionOrigin.GENERATED, replace(TRACE, evidence_ids=(old, gone, new)))

    _, details = details_of(world, draft_id)

    assert [row.state for row in details.evidence] == [
        EVIDENCE_SUPERSEDED,
        EVIDENCE_RETRACTED,
        EVIDENCE_LIVE,
    ]
    page = page_of(world)
    rows = page.find("section", class_="queue-evidence").find_all("li", class_="evidence-row")
    assert [
        [node.text for node in row.find_all("span", class_="evidence-state")] for row in rows
    ] == [["superseded"], ["retracted"], []]


# Facts --------------------------------------------------------------------------------------------


def add_fact_version(world: World, fact_id: str, version: int, wording: str) -> None:
    [(segment,)] = world.sql("SELECT id FROM segments ORDER BY id LIMIT 1")
    [(market,)] = world.sql("SELECT id FROM markets ORDER BY id LIMIT 1")
    world.sql(
        "INSERT INTO company_facts (id, fact_id, version, type, wording, source_url, "
        "allowed_segment_ids, allowed_market_ids, metric, metric_confirmed_at, scope_note, "
        "last_checked_on, created_by, created_at, retracted_at) VALUES (?, ?, ?, 'case_study', "
        "?, ?, ?, ?, NULL, NULL, NULL, '2026-11-01', 'seed', ?, NULL)",
        (
            new_id(),
            fact_id,
            version,
            wording,
            SAFE_URL,
            json.dumps([segment]),
            json.dumps([market]),
            STAMP,
        ),
    )


def test_the_cited_fact_version_shows_with_missing_and_retracted_ones(world: World) -> None:
    add_fact_version(world, "probe_fact", 1, "The <b>first</b> wording")
    add_fact_version(world, "probe_fact", 2, "The second wording")
    add_fact_version(world, "gone_fact", 1, "Retracted wording")
    world.sql(
        "UPDATE company_facts SET retracted_at = '2026-11-17T09:00:00.000000Z' "
        "WHERE fact_id = 'gone_fact'"
    )
    cited = (("probe_fact", 1), ("nope_fact", 1), ("probe_fact", 9), ("gone_fact", 1))
    _, _, _, [draft_id] = text_slot(world, fact_versions=cited, playbook_version="2026.<i>11</i>")

    _, details = details_of(world, draft_id)

    assert [(row.fact_id, row.version, row.found) for row in details.facts] == [
        ("probe_fact", 1, True),
        ("nope_fact", 1, False),
        ("probe_fact", 9, False),
        ("gone_fact", 1, True),
    ]
    assert details.facts[0].wording == "The <b>first</b> wording"
    assert not details.facts[0].retracted and details.facts[3].retracted
    assert details.playbook_version == "2026.<i>11</i>"

    page = page_of(world)
    section = page.find("section", class_="queue-facts")
    version = section.find("p", class_="queue-playbook-version")
    assert version.text == "Playbook version: 2026.<i>11</i>"
    rows = section.find_all("li", class_="fact-row")
    assert raw_text(rows[0].find("span", class_="fact-wording")) == "The <b>first</b> wording"
    assert rows[1].text == "Fact not found: nope_fact v1"
    assert rows[2].text == "Fact not found: probe_fact v9"
    assert rows[3].find("span", class_="fact-state").text == "retracted"
    assert not rows[0].find_all("span", class_="fact-state")
    assert not section.find_all("b") and not section.find_all("i")
    links_are_safe(section)


def test_a_fact_that_no_longer_decodes_reads_as_not_found(world: World) -> None:
    _, _, _, [draft_id] = text_slot(world, fact_versions=(("broken_fact", 1),))
    add_fact_version(world, "broken_fact", 1, "")  # an empty wording no longer validates
    with pytest.raises(InvalidCompanyFacts):
        world.read(lambda: playbook_queries.fact("broken_fact"))

    _, details = details_of(world, draft_id)

    assert [(row.fact_id, row.version, row.found) for row in details.facts] == [
        ("broken_fact", 1, False)
    ]
    section = page_of(world).find("section", class_="queue-facts")
    [row] = section.find_all("li", class_="fact-row")
    assert row.text == "Fact not found: broken_fact v1"


def test_a_revision_without_a_playbook_version_shows_a_dash(world: World) -> None:
    text_slot(world, playbook_version=None, fact_versions=())

    page = page_of(world)

    section = page.find("section", class_="queue-facts")
    assert section.find("p", class_="queue-playbook-version").text == "Playbook version: —"


# Earlier Steps ------------------------------------------------------------------------------------


def test_a_follow_up_lists_only_the_earlier_steps_sent(world: World) -> None:
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "America/New_York"})
    _, _, enrollment_id, [step_1, step_2, step_3] = slot(world, (1, 2, 3))
    first_om = enqueue_with(world, step_1, hash_of(world, step_1, enrollment_id))
    mark_sent(world, first_om, NOW - timedelta(days=3))
    world.sql("UPDATE drafts SET status = 'sent' WHERE id = ?", (step_1,))
    enqueue_with(world, step_2, hash_of(world, step_2, enrollment_id))  # stays queued
    set_due(world, step_3, NOW)

    _, details = details_of(world, step_3)

    assert [(each.step_no, each.subject) for each in details.earlier_steps] == [
        (1, "A question about intake")
    ]
    page = page_of(world)
    section = page.find("section", class_="queue-earlier-steps")
    [step] = section.find_all("details", class_="earlier-step")
    summary = step.find("summary")
    assert summary.text == "Step 1: A question about intake sent 2026-11-15 05:00 America/New_York"
    assert raw_text(step.find("pre", class_="earlier-step-body")).startswith("Hi Zelda,\n\n")
    assert not section.find_all("p", class_="earlier-steps-none")


def test_earlier_steps_list_by_step_number_and_skip_later_steps(world: World) -> None:
    _, _, enrollment_id, [step_1, step_2, step_3, step_4] = slot(world, (1, 2, 3, 4))
    # Step 2 is enqueued and sent before Step 1; Step 4 is sent while Step 3 is the card
    second_om = enqueue_with(world, step_2, hash_of(world, step_2, enrollment_id))
    mark_sent(world, second_om, NOW - timedelta(days=3))
    first_om = enqueue_with(world, step_1, hash_of(world, step_1, enrollment_id))
    mark_sent(world, first_om, NOW - timedelta(days=2))
    fourth_om = enqueue_with(world, step_4, hash_of(world, step_4, enrollment_id))
    mark_sent(world, fourth_om, NOW - timedelta(days=1))
    world.sql(
        "UPDATE drafts SET status = 'sent' WHERE id IN (?, ?, ?)", (step_1, step_2, step_4)
    )
    set_due(world, step_3, NOW)

    _, details = details_of(world, step_3)

    assert [each.step_no for each in details.earlier_steps] == [1, 2]
    page = page_of(world, f"draft={step_3}".encode())
    section = page.find("section", class_="queue-earlier-steps")
    summaries = [node.find("summary").text for node in section.find_all("details")]
    assert [text.split(":")[0] for text in summaries] == ["Step 1", "Step 2"]


def test_a_follow_up_with_nothing_sent_says_so_and_a_step_1_has_no_section(world: World) -> None:
    _, _, _, [step_1, step_2] = slot(world, (1, 2))
    world.sql("UPDATE drafts SET status = 'rejected' WHERE id = ?", (step_1,))
    set_due(world, step_2, NOW)

    page = page_of(world)

    section = page.find("section", class_="queue-earlier-steps")
    assert section.find("p", class_="earlier-steps-none").text == "No earlier Step sent yet"
    assert not section.find_all("details")

    _, _, _, [only] = slot(world)
    step_1_page = page_of(world, f"draft={only}".encode())
    assert not step_1_page.find_all("section", class_="queue-earlier-steps")


# Grounding ----------------------------------------------------------------------------------------


def test_a_failing_card_is_red_with_its_spans_marked_and_approval_disabled(world: World) -> None:
    _, _, _, [draft_id] = slot(world)
    set_grounding(
        world,
        draft_id,
        "fail",
        (
            (RULE, "question", "Too <b>long</b>"),
            ("banned_phrase", "short note", "A banned phrase"),
            ("banned_phrase", "short", "Overlaps the span before"),
            ("unsupported_claim", "", "No span"),
            ("unsupported_claim", "<b>absent</b>", "Not in the text"),
            (RULE, "question", "A repeated span"),
        ),
    )

    card, _ = details_of(world, draft_id)
    assert card.grounding_status == GroundingStatus.FAIL
    page = page_of(world)

    article = page.find("article", class_="queue-card")
    assert "grounding-fail" in str(article.attrs["class"]).split()
    subject = article.find("p", class_="draft-subject")
    body = article.find("pre", class_="draft-body")
    assert [node.text for node in subject.find_all("mark")] == ["question"]
    assert [node.text for node in body.find_all("mark")] == ["short note"]
    assert raw_text(subject) == "Subject: A question about intake"
    flags = article.find("ul", class_="grounding-flags").find_all("li", class_="grounding-flag")
    assert [row.find("code").text for row in flags] == [
        RULE,
        "banned_phrase",
        "banned_phrase",
        "unsupported_claim",
        "unsupported_claim",
        RULE,
    ]
    assert raw_text(flags[0]).endswith("Too <b>long</b>")
    assert raw_text(flags[4].find("mark")) == "<b>absent</b>"
    assert not article.find_all("b")
    badges = article.find("p", class_="queue-badges").find_all("span", class_="queue-badge")
    assert [node.text for node in badges] == ["fail"]
    [button] = article.find_all("button")
    assert "disabled" in button.attrs and button.attrs["type"] == "button"
    assert "queue-approve" in str(button.attrs["class"]).split()
    assert [node.text for node in article.find_all("p", class_="queue-approve-notice")] == [
        "Edit this Draft to remove the failing claims before approving"
    ]
    assert not page.find_all("form")


@pytest.mark.parametrize("state", ["pending", "none"])
def test_a_pending_or_missing_result_says_pending_and_disables_approval(
    world: World, state: str
) -> None:
    _, _, _, [draft_id] = slot(world)
    if state == "pending":
        set_grounding(world, draft_id, "pending")
    else:
        world.sql("DELETE FROM grounding_results WHERE draft_id = ?", (draft_id,))

    card, details = details_of(world, draft_id)

    assert card.grounding_status == GroundingStatus.PENDING
    assert (details.grounding is None) == (state == "none")
    article = page_of(world).find("article", class_="queue-card")
    assert "grounding-fail" not in str(article.attrs["class"]).split()
    assert [node.text for node in article.find_all("span", class_="queue-badge")] == ["pending"]
    [button] = article.find_all("button")
    assert "disabled" in button.attrs
    assert [node.text for node in article.find_all("p", class_="queue-approve-notice")] == [
        "Grounding check pending"
    ]
    assert not article.find_all("mark")


def test_a_passing_card_shows_a_pass_badge_and_no_approve_control(world: World) -> None:
    _, _, _, [draft_id] = slot(world)
    set_grounding(world, draft_id, "pass")

    page = page_of(world)

    article = page.find("article", class_="queue-card")
    assert [node.text for node in article.find_all("span", class_="queue-badge")] == ["pass"]
    assert "grounding-pass" in str(article.find("span", class_="queue-badge").attrs["class"])
    assert not page.find_all("button") and not page.find_all("form")
    assert not article.find_all("p", class_="queue-approve-notice")


def test_a_passing_card_with_an_incomplete_footer_cant_be_approved(world: World) -> None:
    _, _, _, [draft_id] = slot(world)
    set_grounding(world, draft_id, "pass")
    world.sql("DELETE FROM settings WHERE key = 'postal_address'")

    page = page_of(world)

    article = page.find("article", class_="queue-card")
    blocked = "Sending blocked: footer incomplete (Postal address)"
    assert article.find("p", class_="draft-blocked").text == blocked
    [button] = article.find_all("button")
    assert "disabled" in button.attrs
    assert button.attrs["aria-describedby"] == "queue-approve-notices"
    notices = article.find("div", id="queue-approve-notices")
    assert [node.text for node in notices.find_all("p", class_="queue-approve-notice")] == [
        blocked
    ]


@pytest.mark.parametrize("state", ["pending", "none"])
def test_a_new_revision_after_a_pass_reads_pending(world: World, state: str) -> None:
    _, _, _, [draft_id] = slot(world)
    set_grounding(world, draft_id, "pass")
    rev = append(
        world, draft_id, RevisionOrigin.REGENERATED, TRACE, subject="A second question"
    )
    assert rev == 2
    if state == "none":
        world.sql("DELETE FROM grounding_results WHERE draft_id = ? AND rev = 2", (draft_id,))

    card, _ = details_of(world, draft_id)

    assert card.grounding_status == GroundingStatus.PENDING
    assert card.rev == 2
    page = page_of(world)
    article = page.find("article", class_="queue-card")
    assert [node.text for node in article.find_all("span", class_="queue-badge")] == ["pending"]
    [button] = article.find_all("button")
    assert "disabled" in button.attrs
    assert [node.text for node in article.find_all("p", class_="queue-approve-notice")] == [
        "Grounding check pending"
    ]
    assert hidden(page, "rev") == "2"
    subject = raw_text(article.find("p", class_="draft-subject")).removeprefix("Subject: ")
    assert subject == "A second question"
    assert hidden(page, "shown_hash") == card.shown_hash != ""


def test_the_generic_and_stale_flags_join_the_badge_row(world: World) -> None:
    _, _, _, [draft_id] = slot(world)
    set_grounding(world, draft_id, "pass")
    world.sql("UPDATE drafts SET generic = 1, stale = 1 WHERE id = ?", (draft_id,))

    card, _ = details_of(world, draft_id)
    page = page_of(world)

    assert card.generic and card.stale
    row = page.find("p", class_="queue-badges")
    assert [node.text for node in row.find_all("span", class_="queue-badge")] == [
        "pass",
        "generic",
        "stale",
    ]


# The binding --------------------------------------------------------------------------------------


def hash_of(world: World, draft_id: str, enrollment_id: str) -> str:
    """The ``shown_hash`` the query gives the Draft's card (set due now to make it a card)."""
    set_due(world, draft_id, NOW)
    card, _ = details_of(world, draft_id)
    assert card.inbox_id == inbox_of(world, enrollment_id)
    return card.shown_hash


def assert_page_binds(world: World, draft_id: str, enrollment_id: str) -> None:
    set_grounding(world, draft_id, "pass")
    page = page_of(world, f"draft={draft_id}".encode())
    article = page.find("article", class_="queue-card")
    assert article.attrs["data-draft"] == draft_id
    to = raw_text(article.find("p", class_="draft-to")).removeprefix("To: ")
    subject = raw_text(article.find("p", class_="draft-subject")).removeprefix("Subject: ")
    body = raw_text(article.find("pre", class_="draft-body"))
    shown = hidden(page, "shown_hash")
    [(rev,)] = world.sql("SELECT current_rev FROM drafts WHERE id = ?", (draft_id,))
    assert hidden(page, "rev") == str(rev)
    assert shown == render.text_hash(to, inbox_of(world, enrollment_id), subject, body)
    assert len(shown) == 64
    # the hash Sending.enqueue checks: an approval bound to it enqueues (no HashMismatch)
    om_id = enqueue_with(world, draft_id, shown)
    [(approved_hash,)] = world.sql(
        "SELECT approved_hash FROM outbound_messages WHERE id = ?", (om_id,)
    )
    assert approved_hash == shown


def test_a_step_1_card_binds_the_hash_enqueue_checks(world: World) -> None:
    _, _, enrollment_id, [draft_id] = slot(world)

    assert_page_binds(world, draft_id, enrollment_id)


def test_a_follow_up_card_binds_the_hash_enqueue_checks(world: World) -> None:
    _, _, enrollment_id, [step_1, step_2] = slot(world, (1, 2))
    world.sql("UPDATE drafts SET status = 'sent' WHERE id = ?", (step_1,))
    set_due(world, step_2, NOW)

    assert_page_binds(world, step_2, enrollment_id)


def test_a_uk_card_binds_the_hash_enqueue_checks(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, contact_id, enrollment_id, [draft_id] = slot(world, text=False)
    move_to_uk(world, contact_id)
    append(world, draft_id, RevisionOrigin.GENERATED, TRACE)
    monkeypatch.setattr(Compliance, "evaluate_eligibility", lambda self, _: ELIGIBLE)

    assert_page_binds(world, draft_id, enrollment_id)
    [(body,)] = world.sql("SELECT body FROM outbound_messages WHERE draft_id = ?", (draft_id,))
    assert "Your details came from your firm's website." in body


def test_a_reply_card_has_no_hash_and_cant_be_approved(world: World) -> None:
    _, _, enrollment_id, [source] = slot(world)
    world.sql("UPDATE drafts SET status = 'sent' WHERE id = ?", (source,))
    reply = responsive(world, enrollment_id, source)

    card, _ = details_of(world, reply)
    page = page_of(world, f"draft={reply}".encode())

    assert card.shown_hash == ""
    assert hidden(page, "shown_hash") == ""
    [button] = page.find_all("button")
    assert "disabled" in button.attrs
    assert [node.text for node in page.find_all("p", class_="queue-approve-notice")] == [
        "Grounding check pending",  # a copied revision has no grounding row
        "This Draft can't be approved yet",
    ]


def test_a_contact_without_an_address_gets_no_hash(world: World) -> None:
    _, contact_id, _, [draft_id] = slot(world)
    world.sql(
        "UPDATE contacts SET email = NULL, address_class = NULL WHERE id = ?", (contact_id,)
    )

    card, _ = details_of(world, draft_id)

    assert card.message is not None and card.message.to_address == ""
    assert card.shown_hash == ""


# The target ---------------------------------------------------------------------------------------


def test_the_draft_parameter_selects_its_card_and_falls_back_to_card(world: World) -> None:
    _, _, _, [first] = slot(world)
    _, _, _, [second] = slot(world)
    _, _, _, [third] = slot(world)
    order = [card.draft_id for card in build(world).cards]
    assert sorted(order) == sorted([first, second, third])

    page = page_of(world, f"draft={order[2]}".encode())

    assert page.find("article", class_="queue-card").attrs["data-draft"] == order[2]
    assert page.find("span", class_="queue-n-of-n").text == "3 of 3"
    assert page.find("a", class_="queue-previous").attrs["href"] == f"/queue?draft={order[1]}"
    assert not page.find_all("a", class_="queue-next")

    fallback = page_of(world, f"draft={new_id()}&card=2".encode())
    assert fallback.find("article", class_="queue-card").attrs["data-draft"] == order[1]
    assert fallback.find("a", class_="queue-next").attrs["href"] == f"/queue?draft={order[2]}"

    junk = page_of(world, b"draft=%3Cb%3E")
    assert junk.find("article", class_="queue-card").attrs["data-draft"] == order[0]


# Read-only and focused ----------------------------------------------------------------------------


def test_a_page_load_writes_nothing_and_reads_only_the_focused_cards_details(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(3):
        lead_id, _, _, [draft_id] = slot(world, text=False)
        cited = evidence(world, lead_id, "quote", quote="Cited")
        append(world, draft_id, RevisionOrigin.GENERATED, replace(TRACE, evidence_ids=(cited,)))
    focused = build(world).cards[1].draft_id
    calls: list[str] = []
    details: list[str] = []
    real_history = research_queries.evidence_history
    real_details = approval_queries.card_details

    def history(lead_id: str) -> Any:
        calls.append(lead_id)
        return real_history(lead_id)

    def card_details(card: QueueCard) -> CardDetails:
        details.append(card.draft_id)
        return real_details(card)

    monkeypatch.setattr(research_queries, "evidence_history", history)
    monkeypatch.setattr(approval_queries, "card_details", card_details)
    before = dump(world)

    page = page_of(world, f"draft={focused}".encode())

    assert dump(world) == before
    assert details == [focused]
    assert len(calls) == 1
    section = page.find("section", class_="queue-evidence")
    assert len(section.find_all("li", class_="evidence-row")) == 1


# The page's helpers -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "body", "spans", "subject_marks", "body_marks"),
    [
        ("Hello there", "Body text", (), [], []),
        ("Hello there", "Body text", ("",), [], []),
        ("Hello there", "Body text", ("there",), ["there"], []),
        ("Hello there", "Body there", ("there",), ["there"], []),  # the first occurrence only
        ("Hello", "one two one", ("one",), [], ["one"]),
        ("Hello", "abcdef", ("bcd", "cde"), [], ["bcd"]),  # overlaps skip the later span
        ("Hello", "abcdef", ("cde", "bcd", "ef"), [], ["cde"]),
        ("Hello", "abcdef", ("bcd", "ef"), [], ["bcd", "ef"]),
        ("Hello", "abc", ("zzz",), [], []),
        ("Hello", "abc", ("abc", "abc"), [], ["abc"]),
    ],
)
def test_highlight(
    subject: str,
    body: str,
    spans: tuple[str, ...],
    subject_marks: list[str],
    body_marks: list[str],
) -> None:
    subject_parts, body_parts = queue_page.highlight(subject, body, spans)

    assert "".join(part.text for part in subject_parts) == subject
    assert "".join(part.text for part in body_parts) == body
    assert [part.text for part in subject_parts if part.marked] == subject_marks
    assert [part.text for part in body_parts if part.marked] == body_marks


def test_highlight_keeps_empty_text_as_one_segment() -> None:
    assert queue_page.highlight("", "", ("x",)) == ((Segment(""),), (Segment(""),))


def test_a_card_row_without_details_keeps_the_cards_grounding_status() -> None:
    card = QueueCard(
        draft_id=new_id(),
        kind=queue_page.DraftKind.STEP,
        step_no=1,
        rev=1,
        due_at=NOW,
        created_at=NOW,
        grounding_status=GroundingStatus.PASS,
        shown_hash="a" * 64,
    )

    row = queue_page.card_row(card)

    assert row.grounding == "pass" and not row.approve_disabled and row.approve_notices == ()
