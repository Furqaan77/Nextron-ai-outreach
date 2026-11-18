"""The LinkedIn account (Story 10.2) on a temporary database at head: ``AssistAccounts``' add and
save (the spec's matrix rows 1 to 4, each refusal storing nothing, one ``events`` row per save),
``queries.pipeline.assist_setup()`` and the ``linkedin_tasks_off`` health issue (row 7), the
table's CHECKs, and the Settings page's LinkedIn section and account panel through
``Dashboard(units=units)``."""

from collections.abc import Mapping
from html.parser import HTMLParser
from urllib.parse import urlencode

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.pipeline.assist_accounts import AssistAccounts
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.linkedin_caps import (
    BAD_STATUS,
    DAILY_OVER_WEEKLY,
    DUPLICATE_LABEL,
    NEGATIVE_CAP,
    NOT_WHOLE_NUMBER,
    ONE_ACCOUNT,
    TASKS_OFF,
    UNKNOWN_ACCOUNT,
    WEEKLY_CAP_TOO_HIGH,
    AccountCaps,
    AccountRefused,
    AccountStatus,
)
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard
from tests.fakes.clock import FrozenClock

BLANK_CAPS: Mapping[str, str] = {
    "tasks_per_weekday": "",
    "daily_invite_cap": "",
    "weekly_invite_cap": "",
}
UNKNOWN = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
CHAT = "123456789"


@pytest.fixture
def accounts() -> AssistAccounts:
    return AssistAccounts(FrozenClock())


def add(
    units: UnitOfWorkFactory,
    accounts: AssistAccounts,
    label: str = "Main",
    caps: Mapping[str, str] = BLANK_CAPS,
) -> str:
    with units.open():
        return accounts.add_account(label, "Furqaan Ahmed", "Founder", caps)


def save(
    units: UnitOfWorkFactory,
    accounts: AssistAccounts,
    account_id: str,
    status: str = "active",
    caps: Mapping[str, str] = BLANK_CAPS,
    label: str = "Main",
) -> None:
    with units.open():
        accounts.save_account(account_id, label, "Furqaan Ahmed", "Founder", status, caps)


def listed(units: UnitOfWorkFactory) -> list[tuple[str, str, AccountCaps]]:
    with units.open(read_only=True):
        return [
            (found.label, str(found.status), found.caps) for found in pipeline_queries.accounts()
        ]


def events(db_engine: Engine) -> list[tuple[object, ...]]:
    with db_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor "
                "FROM events WHERE entity = 'linkedin_accounts' ORDER BY id"
            )
        )
        return [tuple(row) for row in rows]


def set_chat(units: UnitOfWorkFactory, chat: str) -> None:
    with units.open():
        Setup(FrozenClock()).save({"linkedin_owner_chat": chat})


# Row 1: add --------------------------------------------------------------------------------------


def test_an_added_account_is_inactive_with_the_default_caps_and_one_event(
    units: UnitOfWorkFactory, accounts: AssistAccounts, db_engine: Engine
) -> None:
    account_id = add(units, accounts, " Main ")

    assert listed(units) == [("Main", "inactive", AccountCaps(10, 10, 100))]
    with units.open(read_only=True):
        [found] = pipeline_queries.accounts()
    assert (found.id, found.owner_name, found.owner_role) == (
        account_id,
        "Furqaan Ahmed",
        "Founder",
    )
    assert events(db_engine) == [
        ("linkedin_accounts", account_id, None, "inactive", "account_added", None, "operator")
    ]


# Rows 2 and 3: cap refusals store nothing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("caps", "problems"),
    [
        ({"weekly_invite_cap": "101"}, {"weekly_invite_cap": WEEKLY_CAP_TOO_HIGH}),
        (
            {"daily_invite_cap": "20", "weekly_invite_cap": "15"},
            {"daily_invite_cap": DAILY_OVER_WEEKLY},
        ),
        ({"tasks_per_weekday": "-1"}, {"tasks_per_weekday": NEGATIVE_CAP}),
        ({"daily_invite_cap": "-1"}, {"daily_invite_cap": NEGATIVE_CAP}),
        ({"weekly_invite_cap": "-1"}, {"weekly_invite_cap": NEGATIVE_CAP}),
        ({"weekly_invite_cap": "lots"}, {"weekly_invite_cap": NOT_WHOLE_NUMBER}),
    ],
)
def test_a_refused_cap_stores_nothing(
    units: UnitOfWorkFactory,
    accounts: AssistAccounts,
    db_engine: Engine,
    caps: dict[str, str],
    problems: dict[str, str],
) -> None:
    with pytest.raises(AccountRefused) as refused:
        add(units, accounts, caps={**BLANK_CAPS, **caps})

    assert refused.value.problems == problems
    assert listed(units) == []
    assert events(db_engine) == []


def test_a_refused_save_changes_nothing(
    units: UnitOfWorkFactory, accounts: AssistAccounts, db_engine: Engine
) -> None:
    account_id = add(units, accounts)

    with pytest.raises(AccountRefused) as refused:
        save(units, accounts, account_id, caps={**BLANK_CAPS, "weekly_invite_cap": "101"})

    assert refused.value.problems == {"weekly_invite_cap": WEEKLY_CAP_TOO_HIGH}
    assert listed(units) == [("Main", "inactive", AccountCaps(10, 10, 100))]
    assert len(events(db_engine)) == 1


# Row 4: one active account -----------------------------------------------------------------------


def test_a_second_active_account_is_refused_and_both_keep_their_caps(
    units: UnitOfWorkFactory, accounts: AssistAccounts, db_engine: Engine
) -> None:
    first = add(units, accounts, "Main")
    second = add(units, accounts, "Second", {**BLANK_CAPS, "daily_invite_cap": "5"})
    save(units, accounts, first, "active", {**BLANK_CAPS, "weekly_invite_cap": "50"})
    before = listed(units)

    with pytest.raises(AccountRefused) as refused:
        save(units, accounts, second, "active", {**BLANK_CAPS, "daily_invite_cap": "7"}, "Second")

    assert refused.value.problems == {"form": ONE_ACCOUNT}
    assert listed(units) == before == [
        ("Main", "active", AccountCaps(10, 10, 50)),
        ("Second", "inactive", AccountCaps(10, 5, 100)),
    ]
    assert [row[2:5] for row in events(db_engine)] == [
        (None, "inactive", "account_added"),
        (None, "inactive", "account_added"),
        ("inactive", "active", "account_saved"),
    ]


def test_the_active_account_can_be_saved_again_and_switched_after_deactivating(
    units: UnitOfWorkFactory, accounts: AssistAccounts
) -> None:
    first = add(units, accounts, "Main")
    second = add(units, accounts, "Second")
    save(units, accounts, first, "active")
    save(units, accounts, first, "active", {**BLANK_CAPS, "tasks_per_weekday": "5"})
    save(units, accounts, first, "inactive")
    save(units, accounts, second, "active", label="Second")

    assert listed(units) == [
        ("Main", "inactive", AccountCaps(10, 10, 100)),
        ("Second", "active", AccountCaps(10, 10, 100)),
    ]


def test_other_refusals(units: UnitOfWorkFactory, accounts: AssistAccounts) -> None:
    first = add(units, accounts, "Main")
    add(units, accounts, "Second")

    with pytest.raises(AccountRefused) as duplicate:
        add(units, accounts, "Main")
    with pytest.raises(AccountRefused) as renamed:
        save(units, accounts, first, "inactive", label="Second")
    with pytest.raises(AccountRefused) as unknown:
        save(units, accounts, UNKNOWN)
    with pytest.raises(AccountRefused) as not_ulid:
        save(units, accounts, "not-an-id")
    with pytest.raises(AccountRefused) as bad_status:
        save(units, accounts, first, "paused")

    assert duplicate.value.problems == {"label": DUPLICATE_LABEL}
    assert renamed.value.problems == {"label": DUPLICATE_LABEL}
    assert unknown.value.problems == not_ulid.value.problems == {"form": UNKNOWN_ACCOUNT}
    assert bad_status.value.problems == {"status": BAD_STATUS}
    assert [row[0] for row in listed(units)] == ["Main", "Second"]


# The table backs the rules -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        "'inactive', 10, 10, 101",
        "'inactive', 10, 20, 15",
        "'inactive', -1, 10, 100",
        "'paused', 10, 10, 100",
    ],
)
def test_the_checks_refuse_what_the_domain_refuses(db_engine: Engine, values: str) -> None:
    with db_engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                "INSERT INTO linkedin_accounts (id, label, owner_name, owner_role, status, "
                "tasks_per_weekday, daily_invite_cap, weekly_invite_cap, created_at, updated_at) "
                f"VALUES ('{UNKNOWN}', 'x', 'y', 'z', {values}, "
                "'2026-11-18T09:00:00.000000Z', '2026-11-18T09:00:00.000000Z')"
            )
        )


def test_the_partial_index_allows_one_active_row(
    units: UnitOfWorkFactory, accounts: AssistAccounts, db_engine: Engine
) -> None:
    first = add(units, accounts, "Main")
    second = add(units, accounts, "Second")
    save(units, accounts, first, "active")

    with db_engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(f"UPDATE linkedin_accounts SET status = 'active' WHERE id = '{second}'")
        )


# Row 7: assist_setup and the health issue ------------------------------------------------------


def test_tasks_are_off_until_an_active_account_and_a_chat_exist(
    units: UnitOfWorkFactory, accounts: AssistAccounts
) -> None:
    def read() -> tuple[object, tuple[str, ...]]:
        with units.open(read_only=True):
            setup = pipeline_queries.assist_setup()
            issues = pipeline_queries.health_issues()
        return setup, tuple(issue.text for issue in issues)

    assert read() == (None, (TASKS_OFF,))
    account_id = add(units, accounts)
    set_chat(units, CHAT)
    assert read() == (None, (TASKS_OFF,))  # inactive
    save(units, accounts, account_id, "active")
    setup, issues = read()
    assert issues == ()
    assert isinstance(setup, pipeline_queries.AssistSetup)
    assert (setup.account.id, setup.account.status, setup.chat_id) == (
        account_id,
        AccountStatus.ACTIVE,
        CHAT,
    )
    set_chat(units, "")  # blank is the default again
    assert read() == (None, (TASKS_OFF,))


def ready(units: UnitOfWorkFactory, accounts: AssistAccounts) -> None:
    save(units, accounts, add(units, accounts), "active")
    set_chat(units, CHAT)
    with units.open(read_only=True):
        assert pipeline_queries.assist_setup() is not None


def test_tasks_are_off_while_the_owner_chat_is_the_alert_chat(
    units: UnitOfWorkFactory, accounts: AssistAccounts
) -> None:
    ready(units, accounts)
    with units.open():
        Setup(FrozenClock()).save({"alert_chat_id": CHAT})

    with units.open(read_only=True):
        assert pipeline_queries.assist_setup() is None
        assert [issue.kind for issue in pipeline_queries.health_issues()] == [
            "linkedin_tasks_off"
        ]
    with units.open():
        Setup(FrozenClock()).save({"alert_chat_id": "-1001234567890"})
    with units.open(read_only=True):
        assert pipeline_queries.assist_setup() is not None


def test_an_unparseable_stored_owner_chat_counts_as_unset(
    units: UnitOfWorkFactory, accounts: AssistAccounts, db_engine: Engine
) -> None:
    ready(units, accounts)
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE settings SET value = '@someone' WHERE key = 'linkedin_owner_chat'")
        )

    with units.open(read_only=True):
        assert pipeline_queries.assist_setup() is None


def test_the_health_issue_names_kind_and_subject(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        [issue] = pipeline_queries.health_issues()

    assert (issue.kind, issue.subject, issue.text) == (
        "linkedin_tasks_off",
        "linkedin_tasks",
        TASKS_OFF,
    )
    assert issue.detail is not None and "Settings" in issue.detail


def test_the_registry_reads_the_pipeline_source(units: UnitOfWorkFactory) -> None:
    registry = main.build_health_issues(budget=Dashboard(units=units).budget)

    [source] = [found for found in registry.sources if found.name == "pipeline"]
    with units.open(read_only=True):
        assert [issue.kind for issue in source.read()] == ["linkedin_tasks_off"]


# The Settings page ------------------------------------------------------------------------------


class Page(HTMLParser):
    """The LinkedIn panel's notices, headings, forms and field errors."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.notices: list[tuple[str, str]] = []
        self.headings: list[str] = []
        self.forms: list[dict[str, object]] = []
        self.errors: list[str] = []
        self._capture: str | None = None
        self._role: str | None = None
        self._text: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self.forms.append({"attrs": values, "inputs": []})
        elif tag in {"input", "select"} and self.forms:
            self.forms[-1]["inputs"].append(values)  # type: ignore[union-attr]
        if tag in {"p", "h2", "h3"}:
            self._capture = tag
            self._role = values.get("role") or values.get("class")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._capture:
            return
        found = " ".join("".join(self._text).split())
        if tag in {"h2", "h3"}:
            self.headings.append(found)
        elif self._role in {"status", "alert"}:
            self.notices.append((self._role, found))
        elif self._role == "field-error":
            self.errors.append(found)
        self._capture = None


@pytest.fixture
def dashboard(units: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=units)


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def panel_of(reply: Reply) -> Page:
    start = reply.text.index('<section id="assist-accounts-panel"')
    marker = '<section id="campaigns-panel"'
    end = reply.text.index(marker) if marker in reply.text else None
    return Page(reply.text[start:end])


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


def add_fields(label: str = "Main", **caps: str) -> list[tuple[str, str]]:
    return [
        ("label", label),
        ("owner_name", "Furqaan Ahmed"),
        ("owner_role", "Founder"),
        ("tasks_per_weekday", caps.get("tasks_per_weekday", "")),
        ("daily_invite_cap", caps.get("daily_invite_cap", "")),
        ("weekly_invite_cap", caps.get("weekly_invite_cap", "")),
    ]


def test_a_fresh_settings_page_shows_the_section_the_panel_and_the_health_issue(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = dashboard.get("/settings", cookie=session[0])
    health = dashboard.get("/health/panel", cookie=session[0])

    assert reply.status == 200
    assert "<legend>LinkedIn</legend>" in reply.text
    assert 'name="linkedin_owner_chat"' in reply.text
    page = panel_of(reply)
    assert page.headings == ["LinkedIn account", "Add an account"]
    [form] = page.forms
    assert form["attrs"] == {  # type: ignore[comparison-overlap]
        "method": "post",
        "hx-post": "/settings/linkedin/add",
        "hx-target": "#assist-accounts-panel",
        "hx-swap": "outerHTML",
    }
    names = [found.get("name") for found in form["inputs"]]  # type: ignore[attr-defined]
    assert names == [
        "csrf_token",
        "label",
        "owner_name",
        "owner_role",
        "tasks_per_weekday",
        "daily_invite_cap",
        "weekly_invite_cap",
    ]
    assert health.status == 200
    assert TASKS_OFF in health.text


def test_add_then_activate_through_the_panel(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    added = post(dashboard, session, "/settings/linkedin/add", add_fields())

    assert added.status == 200
    page = panel_of(added)
    assert page.notices == [
        ("status", "Added Main: it is inactive until you make it active.")
    ]
    assert page.headings == ["LinkedIn account", "Main", "Add an account"]
    with units.open(read_only=True):
        [found] = pipeline_queries.accounts()
    saved = post(
        dashboard,
        session,
        "/settings/linkedin/save",
        [("account", found.id), ("status", "active"), *add_fields(weekly_invite_cap="80")],
    )
    assert panel_of(saved).notices == [("status", "Saved Main.")]
    assert listed(units) == [("Main", "active", AccountCaps(10, 10, 80))]


def test_a_refused_post_keeps_the_values_and_marks_the_field(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    refused = post(
        dashboard, session, "/settings/linkedin/add", add_fields(weekly_invite_cap="101")
    )

    assert refused.status == 200
    page = panel_of(refused)
    assert page.notices == [("alert", "Nothing was saved.")]
    assert page.errors == [WEEKLY_CAP_TOO_HIGH]
    [form] = page.forms
    inputs: list[dict[str, str]] = form["inputs"]  # type: ignore[assignment]
    weekly = [found for found in inputs if found.get("name") == "weekly_invite_cap"]
    assert weekly[0]["value"] == "101" and weekly[0]["aria-invalid"] == "true"
    assert listed(units) == []


def test_a_refused_save_keeps_the_values_and_marks_the_field(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    accounts: AssistAccounts,
) -> None:
    account_id = add(units, accounts)

    refused = post(
        dashboard,
        session,
        "/settings/linkedin/save",
        [("account", account_id), ("status", "inactive"), *add_fields(weekly_invite_cap="101")],
    )

    assert refused.status == 200
    page = panel_of(refused)
    assert page.notices == [("alert", "Nothing was saved.")]
    assert page.errors == ["At most 100 invitations per account in a rolling week"]
    account_form, add_form = page.forms
    account_inputs: list[dict[str, str]] = account_form["inputs"]  # type: ignore[assignment]
    [weekly] = [found for found in account_inputs if found.get("name") == "weekly_invite_cap"]
    assert weekly["value"] == "101" and weekly["aria-invalid"] == "true"
    add_inputs: list[dict[str, str]] = add_form["inputs"]  # type: ignore[assignment]
    assert not any(found.get("aria-invalid") for found in add_inputs)
    blank = [found.get("value", "") for found in add_inputs if found["name"] != "csrf_token"]
    assert blank == ["", "", "", "", "", ""]
    assert listed(units) == [("Main", "inactive", AccountCaps(10, 10, 100))]


def test_the_one_account_refusal_is_shown_on_the_panel(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    accounts: AssistAccounts,
) -> None:
    first = add(units, accounts, "Main")
    second = add(units, accounts, "Second")
    save(units, accounts, first, "active")

    refused = post(
        dashboard,
        session,
        "/settings/linkedin/save",
        [("account", second), ("status", "active"), *add_fields("Second")],
    )

    assert panel_of(refused).notices == [("alert", f"Nothing was saved. {ONE_ACCOUNT}.")]
    assert listed(units)[1][1] == "inactive"


@pytest.mark.parametrize(
    ("path", "fields"),
    [
        ("/settings/linkedin/add", [*add_fields(), ("extra", "x")]),
        ("/settings/linkedin/add", add_fields()[:-1]),
        ("/settings/linkedin/add", [*add_fields(), ("label", "Again")]),
        ("/settings/linkedin/save", [("account", "nope"), ("status", "active"), *add_fields()]),
        ("/settings/linkedin/save", add_fields()),
    ],
)
def test_a_malformed_form_is_400_and_stores_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    path: str,
    fields: list[tuple[str, str]],
) -> None:
    reply = post(dashboard, session, path, fields)

    assert reply.status == 400
    assert MALFORMED_FORM in reply.text
    assert listed(units) == []


def test_a_post_without_the_csrf_token_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = post(dashboard, session, "/settings/linkedin/add", add_fields(), token=False)

    assert reply.status == 403
    assert SECURITY_CHECK_FAILED in reply.text
    assert listed(units) == []
