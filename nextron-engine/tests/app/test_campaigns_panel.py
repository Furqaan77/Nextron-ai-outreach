"""The Settings page's Campaigns section (FR-3, AD-4, AD-13, NFR-3, UX-DR3, UX-DR23; Story 4.2)
through ``Dashboard(units=units)`` on a temporary database at head, seeded with the shipped
files: the panel, every POST row of the spec (activate, pause, resume and "Save the LIA"), the
notices, a refused LIA keeping its submitted values with ``aria-invalid``, the CSRF and
malformed refusals (nothing written) and ``Cache-Control: no-store`` on the GET."""

from html.parser import HTMLParser
from urllib.parse import urlencode

import pytest

from nextron import main
from nextron.app.ops.api import Seeds
from nextron.app.queries import setup as setup_queries
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.campaign import CampaignStatus, validate_campaigns
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard
from tests.fakes.clock import FrozenClock

NAMES = ("Law-PrivateAI × UK", "Law-PrivateAI × US", "Ops-Automation × UK", "Ops-Automation × US")
LIA_FIRST = "Record the legitimate-interests assessment reference and date first"
UNKNOWN = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
FOR_UK = "Nothing was changed for Law-PrivateAI × UK."  # a refusal names its Campaign
FOR_US = "Nothing was changed for Law-PrivateAI × US."


def read(name: str) -> object:
    return seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8"))


def seed(units: UnitOfWorkFactory) -> None:
    with units.open():
        Seeds(FrozenClock()).sync_seed(
            validate_segments(read(main.SEGMENTS_FILE_NAME)),
            validate_markets(read(main.MARKETS_FILE_NAME)),
            validate_sequences(read(main.SEQUENCES_FILE_NAME)),
            validate_company_facts(read(main.COMPANY_FACTS_FILE_NAME)),
            validate_offers(read(main.OFFERS_FILE_NAME)),
            validate_campaigns(read(main.CAMPAIGNS_FILE_NAME)),
        )


@pytest.fixture
def seeded(units: UnitOfWorkFactory) -> UnitOfWorkFactory:
    seed(units)
    return units


@pytest.fixture
def dashboard(seeded: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=seeded)


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


@pytest.fixture
def ids(seeded: UnitOfWorkFactory) -> dict[str, str]:
    with seeded.open(read_only=True):
        return {found.key: found.id for found in setup_queries.campaigns()}


class Panel(HTMLParser):
    """The Campaigns panel's notices, headings, forms (with their inputs) and the details."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.notices: list[tuple[str, str]] = []
        self.headings: list[str] = []
        self.forms: list[dict[str, object]] = []
        self.details: list[str] = []
        self.paragraphs: list[str] = []
        self.buttons: list[str] = []
        self.errors: list[str] = []
        self._capture: str | None = None
        self._role: str | None = None
        self._text: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self.forms.append({"attrs": values, "inputs": []})
        elif tag == "input" and self.forms:
            self.forms[-1]["inputs"].append(values)  # type: ignore[union-attr]
        if tag in {"p", "h3", "dd", "button"}:
            self._capture = tag
            self._role = values.get("role") or values.get("class")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._capture:
            return
        text = " ".join("".join(self._text).split())
        if tag == "h3":
            self.headings.append(text)
        elif tag == "dd":
            self.details.append(text)
        elif tag == "button":
            self.buttons.append(text)
        elif self._role in {"status", "alert"}:
            self.notices.append((self._role, text))
        elif self._role == "field-error":
            self.errors.append(text)
        else:
            self.paragraphs.append(text)
        self._capture = None


def settings(dashboard: Dashboard, cookie: bytes) -> tuple[Reply, Panel]:
    reply = dashboard.get("/settings", cookie=cookie)
    assert reply.status == 200
    start = reply.text.index('<section id="campaigns-panel"')
    return reply, Panel(reply.text[start:])


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


def stored(units: UnitOfWorkFactory, campaign_id: str) -> tuple[str, str | None, str | None]:
    with units.open(read_only=True):
        found = setup_queries.campaign(campaign_id)
    assert found is not None
    lia = found.lia
    return (
        str(found.status),
        None if lia is None else lia.ref,
        None if lia is None else lia.date.isoformat(),
    )


def form_for(panel: Panel, campaign_id: str, action: str) -> dict[str, object]:
    [found] = [
        f
        for f in panel.forms
        if f["attrs"]["hx-post"] == f"/settings/campaigns/{action}"  # type: ignore[index]
        and {"type": "hidden", "name": "campaign", "value": campaign_id}
        in f["inputs"]  # type: ignore[operator]
    ]
    return found


def inputs(form: dict[str, object]) -> dict[str, dict[str, str | None]]:
    return {
        str(found["name"]): found
        for found in form["inputs"]  # type: ignore[attr-defined]
        if found.get("name") not in {"csrf_token", "campaign"}
    }


# The panel ---------------------------------------------------------------------------------------


def test_the_settings_page_shows_each_campaign_draft_with_its_forms(
    dashboard: Dashboard, session: tuple[bytes, str], ids: dict[str, str]
) -> None:
    reply, panel = settings(dashboard, session[0])

    assert reply.header(b"cache-control") == b"no-store"
    assert panel.headings == list(NAMES)  # by key
    assert panel.details[:6] == [
        "draft",
        "Law-PrivateAI",
        "United Kingdom",
        "Reply, then the audit, then a build",
        "Default four-Step Sequence",
        "A (active)",
    ]
    assert panel.paragraphs.count("Legitimate-interests assessment: not recorded") == 2
    assert panel.buttons.count("Activate") == 4
    assert [form["attrs"] for form in panel.forms][0] == {
        "method": "post",
        "hx-post": "/settings/campaigns/activate",
        "hx-target": "#campaigns-panel",
        "hx-swap": "outerHTML",
    }
    uk = inputs(form_for(panel, ids["law_private_ai_uk"], "activate"))
    assert set(uk) == {"lia_ref", "lia_date"}
    assert (uk["lia_ref"]["type"], uk["lia_date"]["type"]) == ("text", "date")
    assert (uk["lia_ref"]["value"], uk["lia_date"]["value"]) == ("", "")
    assert inputs(form_for(panel, ids["law_private_ai_us"], "activate")) == {}
    assert panel.notices == []
    assert "Settings" in reply.text and '<form class="settings"' in reply.text  # still there


def test_without_campaigns_the_panel_says_how_to_load_them(units: UnitOfWorkFactory) -> None:
    dashboard = Dashboard(units=units)

    reply, panel = settings(dashboard, dashboard.open_session())

    assert panel.paragraphs == [
        "No Campaigns yet. Create one on the Campaigns page, or run nextron seed sync to load them."
    ]
    assert panel.forms == []


# Activate ----------------------------------------------------------------------------------------


def test_activate_a_us_campaign(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
) -> None:
    us = ids["law_private_ai_us"]

    reply = post(dashboard, session, "/settings/campaigns/activate", [("campaign", us)])

    assert reply.status == 200
    assert reply.text.lstrip().startswith('<section id="campaigns-panel"')
    panel = Panel(reply.text)
    assert panel.notices == [("status", "Activated Law-PrivateAI × US.")]
    assert inputs(form_for(panel, us, "pause")) == {}
    assert stored(seeded, us) == ("active", None, None)


def test_activate_a_uk_campaign_without_its_lia_is_refused(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
) -> None:
    uk = ids["law_private_ai_uk"]
    fields = [("campaign", uk), ("lia_ref", "LIA-2026-01"), ("lia_date", "")]

    reply = post(dashboard, session, "/settings/campaigns/activate", fields)

    assert reply.status == 200
    panel = Panel(reply.text)
    assert panel.notices == [("alert", f"{FOR_UK} {LIA_FIRST}.")]
    shown = inputs(form_for(panel, uk, "activate"))
    assert shown["lia_ref"]["value"] == "LIA-2026-01"
    assert "aria-invalid" not in shown["lia_ref"]
    assert shown["lia_date"]["aria-invalid"] == "true"
    assert panel.errors == ["Enter a value"]
    assert stored(seeded, uk) == ("draft", None, None)


def test_a_future_lia_date_is_refused_beside_its_field(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
) -> None:
    uk = ids["law_private_ai_uk"]
    fields = [("campaign", uk), ("lia_ref", "LIA-2026-01"), ("lia_date", "2026-11-17")]

    panel = Panel(post(dashboard, session, "/settings/campaigns/activate", fields).text)

    assert panel.notices == [("alert", FOR_UK)]
    shown = inputs(form_for(panel, uk, "activate"))
    assert (shown["lia_ref"]["value"], shown["lia_date"]["value"]) == ("LIA-2026-01", "2026-11-17")
    assert shown["lia_date"]["aria-invalid"] == "true"
    assert shown["lia_date"]["aria-describedby"] == f"campaign-{uk}-activate-lia_date-error"
    assert panel.errors == ["can't be in the future"]
    assert stored(seeded, uk) == ("draft", None, None)


def test_activate_a_uk_campaign_with_its_lia_then_edit_it(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
) -> None:
    uk = ids["law_private_ai_uk"]
    fields = [("campaign", uk), ("lia_ref", "LIA-2026-01"), ("lia_date", "2026-11-16")]

    activated = Panel(post(dashboard, session, "/settings/campaigns/activate", fields).text)

    assert activated.notices == [("status", "Activated Law-PrivateAI × UK.")]
    assert (
        "Legitimate-interests assessment: LIA-2026-01 dated 2026-11-16" in activated.paragraphs
    )
    save = inputs(form_for(activated, uk, "lia"))
    assert (save["lia_ref"]["value"], save["lia_date"]["value"]) == ("LIA-2026-01", "2026-11-16")
    assert "Save the LIA" in activated.buttons and "Pause" in activated.buttons

    edit = [("campaign", uk), ("lia_ref", "LIA-2026-02"), ("lia_date", "2026-11-15")]
    saved = Panel(post(dashboard, session, "/settings/campaigns/lia", edit).text)
    again = Panel(post(dashboard, session, "/settings/campaigns/lia", edit).text)

    assert saved.notices == [
        ("status", "Saved the legitimate-interests assessment for Law-PrivateAI × UK.")
    ]
    assert again.notices == [
        ("status", "Nothing changed: the assessment already has these values.")
    ]
    assert stored(seeded, uk) == ("active", "LIA-2026-02", "2026-11-15")


def test_a_refused_lia_save_keeps_its_values(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
) -> None:
    uk = ids["law_private_ai_uk"]
    fields = [("campaign", uk), ("lia_ref", "LIA-2026-01"), ("lia_date", "2026-11-16")]
    post(dashboard, session, "/settings/campaigns/activate", fields)
    bad = [("campaign", uk), ("lia_ref", "  "), ("lia_date", "2026-02-30")]

    panel = Panel(post(dashboard, session, "/settings/campaigns/lia", bad).text)

    assert panel.notices == [("alert", f"{FOR_UK} {LIA_FIRST}.")]
    shown = inputs(form_for(panel, uk, "lia"))
    assert (shown["lia_ref"]["value"], shown["lia_date"]["value"]) == ("  ", "2026-02-30")
    assert shown["lia_ref"]["aria-invalid"] == shown["lia_date"]["aria-invalid"] == "true"
    assert panel.errors == ["Enter a value", "Use a date written YYYY-MM-DD"]
    assert stored(seeded, uk) == ("active", "LIA-2026-01", "2026-11-16")


# Pause and resume --------------------------------------------------------------------------------


def test_pause_then_resume(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
) -> None:
    us = ids["ops_automation_us"]
    post(dashboard, session, "/settings/campaigns/activate", [("campaign", us)])

    paused = Panel(post(dashboard, session, "/settings/campaigns/pause", [("campaign", us)]).text)
    assert stored(seeded, us)[0] == str(CampaignStatus.PAUSED)
    resumed = Panel(post(dashboard, session, "/settings/campaigns/resume", [("campaign", us)]).text)

    assert paused.notices == [
        (
            "status",
            "Paused Ops-Automation × US: its Steps won't be enrolled or enter the Approval "
            "Queue until you resume it.",
        )
    ]
    assert inputs(form_for(paused, us, "resume")) == {}
    assert resumed.notices == [("status", "Resumed Ops-Automation × US.")]
    assert stored(seeded, us)[0] == "active"


def test_a_paused_uk_campaign_shows_resume_and_its_prefilled_lia_form(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
) -> None:
    uk = ids["law_private_ai_uk"]
    fields = [("campaign", uk), ("lia_ref", "LIA-2026-01"), ("lia_date", "2026-11-16")]
    post(dashboard, session, "/settings/campaigns/activate", fields)

    reply = post(dashboard, session, "/settings/campaigns/pause", [("campaign", uk)])

    panel = Panel(reply.text)
    assert panel.notices[0][1].startswith("Paused Law-PrivateAI × UK:")
    assert inputs(form_for(panel, uk, "resume")) == {}
    save = inputs(form_for(panel, uk, "lia"))
    assert (save["lia_ref"]["value"], save["lia_date"]["value"]) == ("LIA-2026-01", "2026-11-16")
    posts = [str(f["attrs"]["hx-post"]) for f in panel.forms]  # type: ignore[index]
    assert not any(path.endswith("/pause") for path in posts)
    assert stored(seeded, uk) == ("paused", "LIA-2026-01", "2026-11-16")


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/settings/campaigns/pause", "This Campaign is draft, so it can't be paused"),
        ("/settings/campaigns/resume", "This Campaign is draft, so it can't be resumed"),
    ],
)
def test_a_stale_control_is_refused_not_repeated(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
    path: str,
    reason: str,
) -> None:
    us = ids["law_private_ai_us"]

    reply = post(dashboard, session, path, [("campaign", us)])

    assert reply.status == 200
    assert Panel(reply.text).notices == [("alert", f"{FOR_US} {reason}.")]
    assert stored(seeded, us)[0] == "draft"


def test_an_unknown_campaign_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = post(dashboard, session, "/settings/campaigns/activate", [("campaign", UNKNOWN)])

    # an id that names no listed Campaign: the alert names none
    assert Panel(reply.text).notices == [
        ("alert", "Nothing was changed. No Campaign has this id.")
    ]


def test_lia_fields_on_a_us_activation_are_refused(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
) -> None:
    us = ids["law_private_ai_us"]
    fields = [("campaign", us), ("lia_ref", "LIA"), ("lia_date", "2026-11-16")]

    panel = Panel(post(dashboard, session, "/settings/campaigns/activate", fields).text)

    assert panel.notices == [
        (
            "alert",
            f"{FOR_US} Only a UK Campaign records a legitimate-interests assessment.",
        )
    ]
    assert stored(seeded, us) == ("draft", None, None)


# Refused requests --------------------------------------------------------------------------------


def snapshot(units: UnitOfWorkFactory) -> list[tuple[str, str | None, str | None]]:
    with units.open(read_only=True):
        return [
            (str(found.status), None if found.lia is None else found.lia.ref, found.key)
            for found in setup_queries.campaigns()
        ]


@pytest.mark.parametrize(
    "path",
    [
        "/settings/campaigns/activate",
        "/settings/campaigns/pause",
        "/settings/campaigns/resume",
        "/settings/campaigns/lia",
    ],
)
def test_a_post_without_the_csrf_token_is_refused_with_403(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
    path: str,
) -> None:
    before = snapshot(seeded)
    fields = [("campaign", ids["law_private_ai_uk"])]
    if path.endswith("/lia"):
        fields += [("lia_ref", "LIA"), ("lia_date", "2026-11-16")]

    reply = post(dashboard, session, path, fields, token=False)

    assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert snapshot(seeded) == before


@pytest.mark.parametrize(
    ("path", "fields"),
    [
        ("/settings/campaigns/activate", [("campaign", "law_private_ai_us")]),  # not a ULID
        ("/settings/campaigns/activate", []),
        ("/settings/campaigns/activate", [("campaign", "{us}"), ("status", "active")]),
        ("/settings/campaigns/activate", [("campaign", "{us}"), ("campaign", "{us}")]),
        (
            "/settings/campaigns/activate",
            [("campaign", "{uk}"), ("lia_ref", "a"), ("lia_ref", "b")],
        ),
        ("/settings/campaigns/pause", [("campaign", "{us}"), ("lia_ref", "LIA")]),
        ("/settings/campaigns/resume", [("campaign", "{us}"), ("lia_date", "2026-11-16")]),
        ("/settings/campaigns/lia", [("campaign", "{uk}"), ("lia_ref", "LIA")]),
        (
            "/settings/campaigns/lia",
            [("campaign", "{uk}"), ("lia_ref", "LIA"), ("lia_date", "2026-11-16"), ("x", "1")],
        ),
    ],
    ids=[
        "not-a-ulid",
        "no-campaign",
        "extra-field",
        "repeated-campaign",
        "repeated-lia",
        "lia-on-pause",
        "lia-on-resume",
        "missing-date",
        "extra-on-lia",
    ],
)
def test_a_malformed_form_is_refused_with_400_and_writes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    ids: dict[str, str],
    seeded: UnitOfWorkFactory,
    path: str,
    fields: list[tuple[str, str]],
) -> None:
    before = snapshot(seeded)
    named = {"{us}": ids["law_private_ai_us"], "{uk}": ids["law_private_ai_uk"]}
    sent = [(name, named.get(value, value)) for name, value in fields]

    reply = post(dashboard, session, path, sent)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert snapshot(seeded) == before


def test_the_page_needs_a_session(dashboard: Dashboard, ids: dict[str, str]) -> None:
    reply = dashboard.post(
        "/settings/campaigns/activate",
        headers=[(b"content-type", FORM)],
        body=urlencode([("campaign", ids["law_private_ai_us"])]).encode("ascii"),
    )

    assert reply.status in {401, 403}
    assert b"campaigns-panel" not in reply.body


def test_stored_names_are_escaped(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    with seeded.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE campaigns SET name = '<b>Bold</b>' WHERE key = 'law_private_ai_us'"
        )

    reply, panel = settings(dashboard, session[0])

    assert "<b>Bold</b>" not in reply.text and "&lt;b&gt;Bold&lt;/b&gt;" in reply.text
    assert "<b>Bold</b>" in panel.headings


def test_a_stored_row_that_no_longer_decodes_leaves_the_settings_reachable(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    with seeded.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE variants SET step_angles = 'x' WHERE campaign_id = "
            "(SELECT id FROM campaigns WHERE key = 'law_private_ai_us')"
        )

    reply, panel = settings(dashboard, session[0])

    assert reply.status == 200
    assert '<form class="settings"' in reply.text  # the settings form still renders
    assert panel.notices == [
        (
            "alert",
            "The Campaigns can't be shown: campaigns[law_private_ai_us].variants: a stored "
            "Variant's step_angles is not valid JSON",
        )
    ]
    assert panel.forms == [] and panel.headings == []
    assert "No Campaigns yet" not in reply.text
