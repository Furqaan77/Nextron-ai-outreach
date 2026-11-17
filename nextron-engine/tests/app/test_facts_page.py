"""The Approved Company Facts page (FR-72, AD-13, UX-DR3, UX-DR4, UX-DR24; Story 4.3) against the
spec's page rows: ``GET /facts`` and the three POSTs through ``Dashboard(units=units)`` on a
temporary database at head, seeded with the shipped files. It checks the panel after the first
sync, the notices, a refused form keeping its values with each reason beside its field, the
earlier versions, ``safe_link`` output, ``Cache-Control: no-store``, and the CSRF and malformed
refusals (nothing written)."""

import re
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from nextron import main
from nextron.app.ops.api import Seeds
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.markets import validate_markets
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard
from tests.fakes.clock import DEFAULT_NOW, FrozenClock

FACT_IDS = (
    "aba_formal_opinion_512",
    "audit_offer",
    "legal_ai",
    "mp_online_digital",
    "nextron_team",
    "property_3d_sites",
    "sra_ai_notice",
    "wish_wheels",
)
NOTHING_SAVED = "Nothing was saved. Correct the fields marked below, then save again."


def read(name: str) -> object:
    return seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def seeded(units: UnitOfWorkFactory) -> UnitOfWorkFactory:
    with units.open():
        Seeds(FrozenClock()).sync_seed(
            validate_segments(read(main.SEGMENTS_FILE_NAME)),
            validate_markets(read(main.MARKETS_FILE_NAME)),
            validate_sequences(read(main.SEQUENCES_FILE_NAME)),
            validate_company_facts(read(main.COMPANY_FACTS_FILE_NAME)),
        )
    return units


@pytest.fixture
def dashboard(seeded: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=seeded, clock=FrozenClock(DEFAULT_NOW))


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


@pytest.fixture
def ids(seeded: UnitOfWorkFactory) -> dict[str, str]:
    with seeded.open(read_only=True):
        found = {seeded_row.key: seeded_row.id for seeded_row in setup_queries.segments()}
        found.update({row.code: row.id for row in setup_queries.markets()})
    return found


def versions(units: UnitOfWorkFactory, fact_id: str) -> list[tuple[int, bool]]:
    """``(version, retracted)`` of each stored version of ``fact_id``, oldest first."""
    with units.open(read_only=True):
        found = playbook_queries.fact(fact_id)
    if found is None:
        return []
    return [(v.version, v.retracted_at is not None) for v in reversed(found.versions)]


def count_rows(units: UnitOfWorkFactory) -> int:
    with units.open(read_only=True):
        return sum(len(found.versions) for found in playbook_queries.company_facts())


def post(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    path: str,
    fields: list[tuple[str, str]],
    *,
    token: bool = True,
) -> Reply:
    cookie, csrf = session
    return dashboard.post(
        path,
        cookie=cookie,
        token=csrf if token else None,
        headers=[(b"content-type", FORM)],
        body=urlencode(fields).encode("ascii"),
    )


def content_fields(ids: dict[str, str], **changes: Any) -> list[tuple[str, str]]:
    """A valid statistic's form fields (both Segments, the US Market), with ``changes``; a
    ``segments`` or ``markets`` change is a list of keys or codes."""
    values: dict[str, Any] = {
        "type": "statistic",
        "wording": "Firms that use private AI keep client data on their own servers.",
        "source_url": "https://example.com/survey",
        "segments": ["law_private_ai", "ops_automation"],
        "markets": ["us"],
        "metric": "",
        "metric_confirmed_at": "",
        "scope_note": "",
        "last_checked_on": "2026-11-15",
    }
    values.update(changes)
    fields = [(name, values[name]) for name in ("type", "wording", "source_url")]
    fields += [("segment", ids[key]) for key in values["segments"]]
    fields += [("market", ids[code]) for code in values["markets"]]
    fields += [
        (name, values[name])
        for name in ("metric", "metric_confirmed_at", "scope_note", "last_checked_on")
    ]
    return fields


def add_fields(
    ids: dict[str, str], fact_id: str = "stat_x", **changes: Any
) -> list[tuple[str, str]]:
    return [("fact_id", fact_id), *content_fields(ids, **changes)]


def legal_ai_fields(
    ids: dict[str, str], based_on: str = "1", **changes: Any
) -> list[tuple[str, str]]:
    values: dict[str, Any] = {
        "type": "case_study",
        "wording": "Built an AI legal assistant platform for research and document help for a "
        "legal-tech venture (Legal AI).",
        "source_url": "https://nextronai.com/",
        "segments": ["law_private_ai", "ops_automation"],
        "markets": ["us", "uk"],
        "scope_note": "The client was a legal-tech venture, not a law firm.",
        "last_checked_on": "2026-11-14",
        **changes,
    }
    return [("fact_id", "legal_ai"), ("based_on", based_on), *content_fields(ids, **values)]


def section(html: str, fact_id: str) -> str:
    """The ``<section>`` of ``fact_id`` in the panel."""
    match = re.search(
        rf'<section class="fact" aria-labelledby="fact-{fact_id}-heading">(.*?)</section>',
        html,
        re.DOTALL,
    )
    assert match is not None, fact_id
    return match.group(1)


def texts(html: str) -> str:
    """The page's text with its tags dropped and its whitespace squeezed."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


# The page ----------------------------------------------------------------------------------------


def test_the_page_lists_the_eight_seeded_facts(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = dashboard.get("/facts", cookie=session[0])

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    html = reply.text
    assert "<title>Nextron: Approved Company Facts</title>" in html
    assert re.findall(r'<h3 id="fact-([a-z0-9_]+)-heading">', html) == list(FACT_IDS)
    assert "<h2>Facts (8)</h2>" in html
    legal = texts(section(html, "legal_ai"))
    assert (
        "Wording Built an AI legal assistant platform for research and document help for a "
        "legal-tech venture (Legal AI). Type Case study Source https://nextronai.com/ Segments "
        "Law-PrivateAI, Ops-Automation Markets United Kingdom, United States Metric none Last "
        "checked 2026-11-14 Version 1, made by seed on 2026-11-16 09:00 UTC"
    ) in legal
    assert (
        '<a href="https://nextronai.com/" rel="noopener noreferrer">https://nextronai.com/</a>'
        in section(html, "legal_ai")
    )
    aba = texts(section(html, "aba_formal_opinion_512"))
    assert "Type Regulation" in aba and "Segments Law-PrivateAI Markets United States" in aba
    sra = texts(section(html, "sra_ai_notice"))
    assert "Segments Law-PrivateAI Markets United Kingdom Metric" in sra
    assert "Earlier versions" not in html
    assert html.count('hx-post="/facts/retract"') == 8


def test_each_fact_has_a_prefilled_edit_form_and_the_add_form_defaults_today(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str]
) -> None:
    html = dashboard.get("/facts", cookie=session[0]).text
    legal = section(html, "legal_ai")

    assert '<details class="fact-edit">' in legal
    assert '<input type="hidden" name="fact_id" value="legal_ai">' in legal
    assert '<input type="hidden" name="based_on" value="1">' in legal
    assert '<option value="case_study" selected>Case study</option>' in legal
    source = 'id="fact-edit-legal_ai-source_url" name="source_url" value="https://nextronai.com/"'
    assert source in legal
    for key in ("law_private_ai", "ops_automation", "us", "uk"):
        assert re.search(rf'value="{ids[key]}" id="[^"]+" checked>', legal), key
    aba = section(html, "aba_formal_opinion_512")
    assert re.search(rf'value="{ids["uk"]}" id="[^"]+">', aba)  # not ticked
    add = html[html.index('<details class="fact-add">') : html.index("<h2>Facts")]
    assert 'id="fact-new-last_checked_on" name="last_checked_on" value="2026-11-16"' in add
    assert '<option value="" selected>Choose a type</option>' in add
    assert '" checked' not in add  # no Segment or Market is ticked
    for form in re.findall(r"<form\b[^>]*>", html):
        assert 'method="post"' in form and 'hx-target="#facts-panel"' in form
        assert 'hx-swap="outerHTML"' in form


def test_the_settings_page_links_to_the_facts_page_and_the_nav_is_unchanged(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    settings = dashboard.get("/settings", cookie=session[0]).text
    facts = dashboard.get("/facts", cookie=session[0]).text

    assert (
        'Approved Company Facts are on the <a href="/facts">Approved Company Facts page</a>.'
        in settings
    )
    nav = facts[facts.index('<nav aria-label="Sections">') : facts.index("</nav>")]
    assert "/facts" not in nav and "Facts" not in nav


# Add, edit and retract ---------------------------------------------------------------------------


def test_add_saves_version_1_and_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], seeded: UnitOfWorkFactory
) -> None:
    reply = post(dashboard, session, "/facts/add", add_fields(ids))

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    html = reply.text
    assert html.startswith('<div id="facts-panel">')
    assert '<p class="notice" role="status">Saved stat_x as version 1.</p>' in html
    assert "Version 1, made by operator on 2026-11-16 09:00 UTC" in texts(section(html, "stat_x"))
    assert versions(seeded, "stat_x") == [(1, False)]
    assert '<details class="fact-add">' in html  # closed again, its fields cleared
    assert "Firms that use private AI" not in html[: html.index("<h2>Facts")]


def test_edit_saves_a_new_version_and_lists_the_earlier_one(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], seeded: UnitOfWorkFactory
) -> None:
    fields = legal_ai_fields(ids, wording="Built a legal research assistant (Legal AI).")

    reply = post(dashboard, session, "/facts/edit", fields)

    assert reply.status == 200
    html = reply.text
    assert '<p class="notice" role="status">Saved legal_ai as version 2.</p>' in html
    legal = section(html, "legal_ai")
    assert '<input type="hidden" name="based_on" value="2">' in legal
    assert "<summary>Earlier versions (1)</summary>" in legal
    shown = texts(legal)
    assert shown.index("Built a legal research assistant (Legal AI).") < shown.index(
        "Earlier versions"
    ) < shown.index("for research and document help")
    assert "Version 2, made by operator on 2026-11-16 09:00 UTC" in shown
    assert versions(seeded, "legal_ai") == [(1, False), (2, False)]


def test_an_edit_that_changes_nothing_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], seeded: UnitOfWorkFactory
) -> None:
    reply = post(dashboard, session, "/facts/edit", legal_ai_fields(ids))

    assert reply.status == 200
    assert (
        '<p class="notice" role="status">Nothing changed, so no new version was saved.</p>'
        in reply.text
    )
    assert versions(seeded, "legal_ai") == [(1, False)]


def test_retract_marks_the_fact_retracted_and_drafts_can_no_longer_cite_it(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], seeded: UnitOfWorkFactory
) -> None:
    post(dashboard, session, "/facts/edit", legal_ai_fields(ids, wording="Built it (Legal AI)."))

    reply = post(dashboard, session, "/facts/retract", [("fact_id", "legal_ai")])

    assert reply.status == 200
    html = reply.text
    assert (
        '<p class="notice" role="status">Retracted legal_ai; Drafts can no longer cite it.</p>'
        in html
    )
    legal = section(html, "legal_ai")
    assert '<span class="fact-retracted">Retracted</span>' in legal
    assert 'hx-post="/facts/retract"' not in legal
    assert 'hx-post="/facts/edit"' in legal  # it keeps its Edit form
    assert legal.count("; retracted 2026-11-16 09:00 UTC") == 2  # every version
    assert versions(seeded, "legal_ai") == [(1, True), (2, True)]
    with seeded.open(read_only=True):
        allowed = playbook_queries.allowed_facts(ids["law_private_ai"], ids["us"])
    assert "legal_ai" not in [found.fact_id for found in allowed]

    again = post(dashboard, session, "/facts/retract", [("fact_id", "legal_ai")])

    assert '<p class="notice" role="status">legal_ai was already retracted.</p>' in again.text


def test_editing_a_retracted_fact_restores_it(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], seeded: UnitOfWorkFactory
) -> None:
    post(dashboard, session, "/facts/retract", [("fact_id", "legal_ai")])

    reply = post(dashboard, session, "/facts/edit", legal_ai_fields(ids))

    assert "Saved legal_ai as version 2." in reply.text
    assert "fact-retracted" not in section(reply.text, "legal_ai")
    assert versions(seeded, "legal_ai") == [(1, True), (2, False)]


# Refused forms -----------------------------------------------------------------------------------


def field_error(html: str, prefix: str, name: str) -> str:
    match = re.search(
        rf'<p class="field-error" id="{prefix}-{name}-error">([^<]*)</p>', html
    )
    assert match is not None, name
    return unescape(match.group(1))


@pytest.mark.parametrize(
    ("name", "changes", "field", "reason"),
    [
        (
            "a regulation with 2 Markets",
            {"type": "regulation", "wording": "A regulation.", "markets": ["us", "uk"]},
            "markets",
            "A regulatory fact applies to one Market",
        ),
        (
            "a metric without its date",
            {"metric": "40% faster intake"},
            "metric",
            "Add a metric only once the client confirms it",
        ),
        (
            "a javascript: URL",
            {"source_url": "javascript:alert(1)"},
            "source_url",
            "Use an http or https link",
        ),
        (
            "ABA 512 on UK",
            {
                "type": "regulation",
                "wording": "ABA Formal Opinion 512 asks for informed consent.",
                "markets": ["uk"],
            },
            "markets",
            "ABA Formal Opinion 512 applies only to a US Market",
        ),
        ("no Segment", {"segments": []}, "segments", "Choose at least one Segment"),
        ("a future date", {"last_checked_on": "2026-11-18"}, "last_checked_on",
         "can't be in the future"),
    ],
)
def test_a_refused_edit_keeps_its_values_and_names_each_reason(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
    name: str,
    changes: dict[str, Any],
    field: str,
    reason: str,
) -> None:
    fields = legal_ai_fields(ids, **changes)

    reply = post(dashboard, session, "/facts/edit", fields)

    assert reply.status == 200, name
    html = reply.text
    assert f'<p class="notice notice-refused" role="alert">{NOTHING_SAVED}</p>' in html
    legal = section(html, "legal_ai")
    assert '<details class="fact-edit" open>' in legal
    assert field_error(legal, "fact-edit-legal_ai", field) == reason
    assert 'aria-invalid="true"' in legal
    for key, value in changes.items():
        if isinstance(value, str) and value:
            assert f"{value}" in unescape(legal), key
    assert versions(seeded, "legal_ai") == [(1, False)]
    assert "role=\"status\"" not in html


def test_a_refused_edit_ticks_the_submitted_checkboxes(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str]
) -> None:
    fields = legal_ai_fields(ids, type="regulation", segments=["ops_automation"],
                             markets=["us", "uk"])

    legal = section(post(dashboard, session, "/facts/edit", fields).text, "legal_ai")

    assert re.search(rf'value="{ids["ops_automation"]}" id="[^"]+" checked', legal)
    assert not re.search(rf'value="{ids["law_private_ai"]}" id="[^"]+" checked', legal)
    assert '<option value="regulation" selected>Regulation</option>' in legal
    markets_error = 'aria-describedby="fact-edit-legal_ai-markets-error" aria-invalid="true"'
    assert legal.count(markets_error) == 2  # each Market checkbox


def test_a_stale_edit_is_refused_in_the_notice(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], seeded: UnitOfWorkFactory
) -> None:
    post(dashboard, session, "/facts/edit", legal_ai_fields(ids, wording="Edited elsewhere."))

    reply = post(dashboard, session, "/facts/edit", legal_ai_fields(ids, wording="Mine."))

    assert reply.status == 200
    assert (
        '<p class="notice notice-refused" role="alert">Nothing was saved. This fact changed since '
        "the page loaded; reload the page and edit again.</p>"
    ) in reply.text
    assert versions(seeded, "legal_ai") == [(1, False), (2, False)]


def test_an_edit_of_an_unknown_fact_is_refused_in_the_notice(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], seeded: UnitOfWorkFactory
) -> None:
    fields = [("fact_id", "no_such_fact"), ("based_on", "1"), *content_fields(ids)]
    before = count_rows(seeded)

    reply = post(dashboard, session, "/facts/edit", fields)

    assert reply.status == 200
    assert "Nothing was saved. No fact has this id." in reply.text
    assert count_rows(seeded) == before


def test_a_taken_fact_id_on_add_is_refused_beside_its_field(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], seeded: UnitOfWorkFactory
) -> None:
    before = count_rows(seeded)

    reply = post(dashboard, session, "/facts/add", add_fields(ids, fact_id="legal_ai",
                                                               metric="A result"))

    assert reply.status == 200
    html = reply.text
    add = html[html.index('<details class="fact-add"') : html.index("<h2>Facts")]
    assert '<details class="fact-add" open>' in add
    assert field_error(add, "fact-new", "fact_id") == "This fact id is already used"
    assert field_error(add, "fact-new", "metric") == "Add a metric only once the client confirms it"
    assert 'id="fact-new-fact_id" name="fact_id" aria-describedby="fact-new-fact_id-error" ' \
        'aria-invalid="true" value="legal_ai"' in add
    assert 'value="A result"' in add
    assert "Firms that use private AI keep client data on their own servers." in add
    assert count_rows(seeded) == before


def test_a_retract_of_an_unknown_fact_is_refused_in_the_notice(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = post(dashboard, session, "/facts/retract", [("fact_id", "no_such_fact")])

    assert reply.status == 200
    assert (
        '<p class="notice notice-refused" role="alert">Nothing was retracted. No fact has this '
        "id.</p>"
    ) in reply.text


# Untrusted text renders inert -------------------------------------------------------------------


def test_stored_text_is_escaped(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str]
) -> None:
    wording = '<script>alert(1)</script> "quoted" & <b hx-post="/x">bold</b>'

    reply = post(dashboard, session, "/facts/add", add_fields(ids, wording=wording))

    assert "Saved stat_x as version 1." in reply.text
    assert "<script>" not in reply.text and '<b hx-post="/x">' not in reply.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in reply.text


def test_a_refused_url_is_shown_back_as_text_never_as_a_link(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str]
) -> None:
    fields = add_fields(ids, source_url="javascript:alert(1)")

    reply = post(dashboard, session, "/facts/add", fields)

    assert 'href="javascript:' not in reply.text
    assert 'value="javascript:alert(1)"' in reply.text


# Refused requests -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "fields"),
    [
        ("/facts/add", [("extra", "1")]),
        ("/facts/add", [("segment", "law_private_ai")]),
        ("/facts/add", [("market", "not-a-ulid")]),
        ("/facts/add", [("wording", "twice")]),
        ("/facts/add", [("based_on", "1")]),
        ("/facts/edit", [("based_on", "x")]),
        ("/facts/retract", [("segment", "01ARZ3NDEKTSV4RRFFQ69G5FAV")]),
        ("/facts/retract", [("fact_id", "legal_ai")]),
    ],
    ids=["extra", "segment-key", "market", "repeated", "add-based-on", "edit-based-on",
         "retract-segment", "retract-repeated"],
)
def test_a_malformed_form_is_refused_with_400_and_writes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
    path: str,
    fields: list[tuple[str, str]],
) -> None:
    base = {
        "/facts/add": add_fields(ids),
        "/facts/edit": [
            (name, value) for name, value in legal_ai_fields(ids) if name != "based_on"
        ],
        "/facts/retract": [("fact_id", "legal_ai")],
    }[path]
    before = count_rows(seeded)

    reply = post(dashboard, session, path, [*base, *fields])

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert count_rows(seeded) == before
    assert versions(seeded, "legal_ai") == [(1, False)]


@pytest.mark.parametrize("based_on", ["0", "-1", "01", "1.0", "1234567890", ""])
def test_a_based_on_that_isnt_a_positive_whole_number_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], based_on: str
) -> None:
    reply = post(dashboard, session, "/facts/edit", legal_ai_fields(ids, based_on=based_on))

    assert reply.status == 400


@pytest.mark.parametrize("missing", ["fact_id", "type", "last_checked_on", "scope_note"])
def test_a_missing_field_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str], missing: str
) -> None:
    fields = [(name, value) for name, value in add_fields(ids) if name != missing]

    assert post(dashboard, session, "/facts/add", fields).status == 400


@pytest.mark.parametrize("path", ["/facts/add", "/facts/edit", "/facts/retract"])
def test_a_post_without_the_csrf_token_is_refused_with_403(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
    path: str,
) -> None:
    fields = {
        "/facts/add": add_fields(ids),
        "/facts/edit": legal_ai_fields(ids, wording="Changed."),
        "/facts/retract": [("fact_id", "legal_ai")],
    }[path]
    before = count_rows(seeded)

    reply = post(dashboard, session, path, fields, token=False)

    assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert count_rows(seeded) == before
    assert versions(seeded, "legal_ai") == [(1, False)]


def test_the_page_without_a_session_is_locked(dashboard: Dashboard) -> None:
    assert dashboard.get("/facts").status == 401


def test_the_page_renders_with_no_facts_yet(units: UnitOfWorkFactory, tmp_path: Path) -> None:
    dashboard = Dashboard(units=units, clock=FrozenClock(DEFAULT_NOW))
    cookie = dashboard.open_session()

    html = dashboard.get("/facts", cookie=cookie).text

    assert "<h2>Facts (0)</h2>" in html
    assert "No facts yet." in html
