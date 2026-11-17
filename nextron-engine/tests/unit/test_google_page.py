"""The Google section of the Credentials page (``nextron.web.google_page``) through ``Dashboard``,
with the HTML parsed: the client import rows, Grant (waiting and polling), the polled row, every
outcome as text, the two-step remove, and the CSRF and malformed-form refusals."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

import pytest

from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.google_oauth import (
    CLIENT_SECRET_NAME,
    WEB_CLIENT,
    ClientConfig,
    GrantMeta,
    encode_client_config,
    encode_grant_meta,
)
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import CLIENT_FILE_NOT_DELETED
from nextron.web import app as web_app
from nextron.web.credentials_page import STORE_FAILED
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM
from nextron.web.routing import PERSONAL_DATA_ATTRIBUTE
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.fakes.google_oauth import CLIENT_ID, token_set
from tests.fakes.secrets import InMemorySecrets

T = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
SERVICE = "nextron-engine/test"
SECRET = "Cs4Vq8Lp2Xz6Nw1Rt5Hy"
PATH = r"C:\Users\mac\Downloads\client_secret_Pz4Kq8Wn3Rt6.json"
MAC = "mac@nextronai.com"
STATE = "St3Lm8Qw2Zn6Vb9Hx4Ck" * 2
VERIFIER = "Vf2Bn6Qr9Lk3Hs7Wd1Zm" * 4
CODE = "Ac7Wm3Kq9Jz2Pv5Tx8Ns"
CLIENT_FILE = (
    '{"installed": {"client_id": "%s", "client_secret": "%s"}}' % (CLIENT_ID, SECRET)
).encode()
ANSWER = {"state": (STATE,), "code": (CODE,)}
GRANT, REMOVE, CLIENT = (
    "/credentials/google/grant",
    "/credentials/google/remove",
    "/credentials/google/client",
)
VOID = frozenset({"input", "meta", "link", "br", "img"})


@pytest.fixture(autouse=True)
def fixed_randomness(monkeypatch: pytest.MonkeyPatch) -> None:
    import nextron.app.setup.api as api  # noqa: PLC0415

    monkeypatch.setattr(api, "token_urlsafe", lambda size: {32: STATE, 64: VERIFIER}[size])


# Parsing ----------------------------------------------------------------------------------------


@dataclass
class Node:
    tag: str
    attrs: dict[str, str | None]
    parent: "Node | None"
    children: list["Node"] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join("".join(self.parts).split())

    def all(self, tag: str | None = None, **attrs: str) -> list["Node"]:
        found = []
        for child in self.children:
            if (tag is None or child.tag == tag) and all(
                child.attrs.get(name.rstrip("_").replace("_", "-")) == value
                for name, value in attrs.items()
            ):
                found.append(child)
            found += child.all(tag, **attrs)
        return found


class Html(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self._open = [self.root]
        self.feed(text)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, dict(attrs), self._open[-1])
        self._open[-1].children.append(node)
        if tag not in VOID:
            self._open.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                for closed in reversed(self._open[index:]):
                    self._open.pop()
                    if closed.parent is not None:
                        closed.parent.parts.append("".join(closed.parts))
                return

    def handle_data(self, data: str) -> None:
        self._open[-1].parts.append(data)

    def by_id(self, element_id: str) -> Node:
        [node] = self.root.all(id=element_id)
        return node


@dataclass
class Row:
    """One Google row, as a test reads it."""

    node: Node

    @property
    def state(self) -> str:
        [state] = self.node.all("p", class_="credential-state")
        return state.text

    @property
    def notices(self) -> list[tuple[str | None, str]]:
        return [(p.attrs.get("role"), p.text) for p in self.node.all("p") if p.attrs.get("role")]

    @property
    def waiting(self) -> list[str]:
        return [p.text for p in self.node.all("p", class_="notice google-waiting")]

    @property
    def forms(self) -> list[Node]:
        return self.node.all("form")

    def buttons(self) -> list[str]:
        return [button.text for button in self.node.all("button")]

    def hidden(self, form_: Node) -> dict[str, str | None]:
        return {
            str(i.attrs["name"]): i.attrs.get("value")
            for i in form_.all("input")
            if i.attrs.get("type") == "hidden"
        }


def grant_row(html: str, grant: str = "internal_notify") -> Row:
    return Row(Html(html).by_id(f"google-grant-{grant}"))


def client_row(html: str) -> Row:
    return Row(Html(html).by_id("google-client"))


def answered_row(reply: Reply, element_id: str) -> Row:
    """The answer to a row request: 200, no-store, and the row alone for HTMX to swap in."""
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    assert reply.text.startswith('<div class="credential google-')
    assert f'id="{element_id}"' in reply.text.splitlines()[0]
    assert "<html" not in reply.text and "<nav" not in reply.text
    return Row(Html(reply.text).by_id(element_id))


# Fixtures and helpers ---------------------------------------------------------------------------


@pytest.fixture
def dashboard(units: UnitOfWorkFactory) -> Dashboard:
    # real units of work: the Credentials page's unsubscribe token keys read the database
    return Dashboard(
        secrets=InMemorySecrets("test"),
        clock=FrozenClock(T),
        allowlist=[MAC, "x@gmail.com"],
        units=units,
    )


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def post(
    dashboard: Dashboard, session: tuple[bytes, str], path: str, body: bytes, *, token: bool = True
) -> Reply:
    cookie, csrf = session
    return dashboard.post(
        path,
        cookie=cookie,
        token=csrf if token else None,
        headers=[(b"content-type", FORM)],
        body=body,
    )


def fields(session: tuple[bytes, str], **values: str) -> bytes:
    return form(csrf_token=session[1], **values)


def page(dashboard: Dashboard, session: tuple[bytes, str]) -> str:
    reply = dashboard.get("/credentials", cookie=session[0])
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return reply.text


def import_client(dashboard: Dashboard) -> None:
    dashboard.secrets.store[(SERVICE, CLIENT_SECRET_NAME)] = encode_client_config(
        ClientConfig(CLIENT_ID, SECRET)
    )


def click_grant(
    dashboard: Dashboard, session: tuple[bytes, str], grant: str = "internal_notify"
) -> Reply:
    return post(dashboard, session, GRANT, fields(session, grant=grant))


def poll(dashboard: Dashboard, session: tuple[bytes, str], grant: str = "internal_notify") -> Row:
    reply = dashboard.get(f"/credentials/google/grant/{grant}", cookie=session[0])
    return answered_row(reply, f"google-grant-{grant}")


def granted(
    dashboard: Dashboard, session: tuple[bytes, str], tokens: Any = None, grant: str = "internal_notify"
) -> Row:
    if tokens is not None:
        dashboard.oauth.exchanges.append(tokens)
    dashboard.next_answer = ANSWER
    click_grant(dashboard, session, grant)
    dashboard.run_jobs()
    return poll(dashboard, session, grant)


def keyring_changes(dashboard: Dashboard) -> list[tuple[str, str]]:
    return [
        call
        for call in dashboard.secrets.calls
        if call[0] in ("write", "delete") and not call[1].startswith("session/")
    ]


# The section ------------------------------------------------------------------------------------


def test_the_page_shows_the_client_row_and_each_grant_not_granted(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    html = Html(page(dashboard, session))

    section = html.root.all("section", class_="google")[0]
    assert section.all("h2")[0].text == "Google"
    client = Row(html.by_id("google-client"))
    assert client.state == "Not imported"
    [import_form] = client.forms
    assert {k: import_form.attrs.get(k) for k in ("method", "hx-post", "hx-target", "hx-swap")} == {
        "method": "post",
        "hx-post": CLIENT,
        "hx-target": "#google-client",
        "hx-swap": "outerHTML",
    }
    assert client.hidden(import_form) == {"csrf_token": session[1]}
    [path_input] = import_form.all("input", type="text")
    assert path_input.attrs["name"] == "path" and path_input.attrs["autocomplete"] == "off"
    assert "value" not in path_input.attrs
    assert "Copy as path" in import_form.all("p", class_="hint")[0].text
    assert client.buttons() == ["Import"]
    for grant, label in (
        ("internal_notify", "Internal notifications (internal_notify)"),
        ("postmaster", "Postmaster Tools (postmaster)"),
    ):
        row = Row(html.by_id(f"google-grant-{grant}"))
        assert row.node.all("h3")[0].text == label
        assert row.state == "Not granted"
        assert row.forms == []  # no Grant before the client is imported
        assert "hx-get" not in row.node.attrs
    assert "Dev and test allowlist" in section.all("p", class_="google-allowlist")[0].text


def test_prod_has_no_allowlist_note(units: UnitOfWorkFactory) -> None:
    dashboard = Dashboard(env="prod", units=units)

    html = Html(page(dashboard, (dashboard.open_session(), "")))

    assert html.root.all("p", class_="google-allowlist") == []


def test_the_credentials_page_declares_personal_data_and_the_import_does_not() -> None:
    app = Dashboard().app
    declared = {
        getattr(route, "name", None): getattr(route.endpoint, PERSONAL_DATA_ATTRIBUTE, None)  # type: ignore[attr-defined]
        for route in app.routes
    }

    assert declared["credentials_page"] is True
    assert declared["google_client_import"] is False
    assert declared["google_grant_start"] is True
    assert declared["google_grant_row"] is True
    assert declared["google_grant_remove"] is True


# Row: Import ------------------------------------------------------------------------------------


def test_importing_answers_with_the_client_row_and_deletes_the_file(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    dashboard.files.files[PATH] = CLIENT_FILE

    reply = post(dashboard, session, CLIENT, fields(session, path=f'"{PATH}"'))

    row = answered_row(reply, "google-client")
    assert row.notices == [("status", "Imported. The file was deleted.")]
    assert row.state == "Imported, last updated 2026-11-17 09:30 UTC"
    assert "Reload this page to grant Google access." in reply.text
    assert dashboard.files.deleted == [PATH]
    assert SECRET not in reply.text and "Pz4Kq8Wn3Rt6" not in reply.text
    assert [r.state for r in (grant_row(page(dashboard, session)),)] == ["Not granted"]
    assert grant_row(page(dashboard, session)).buttons() == ["Grant"]


def test_an_import_whose_file_cannot_be_deleted_says_so(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    dashboard.files.files[PATH] = CLIENT_FILE
    dashboard.files.delete_error = CLIENT_FILE_NOT_DELETED

    row = answered_row(post(dashboard, session, CLIENT, fields(session, path=PATH)), "google-client")

    assert row.notices == [
        ("alert", "Imported, but the file could not be deleted: delete it yourself.")
    ]


@pytest.mark.parametrize(
    ("typed", "content", "reason"),
    [
        (PATH, b'{"web": {}}', WEB_CLIENT),
        ("client_secret.json", CLIENT_FILE, "enter a full path on a drive of this PC"),
        (r"\\srv\share\x.json", CLIENT_FILE, "enter a full path on a drive of this PC"),
        (r"C:\x\client_secret.txt", CLIENT_FILE, "the file name must end in .json"),
        (r"C:\x\gone.json", None, "there is no file at that path"),
        (PATH, b"not json", "the file is not UTF-8 JSON"),
    ],
)
def test_a_refused_import_says_why_and_stores_nothing(
    typed: str,
    content: bytes | None,
    reason: str,
    dashboard: Dashboard,
    session: tuple[bytes, str],
) -> None:
    if content is not None:
        dashboard.files.files[PATH] = content

    reply = post(dashboard, session, CLIENT, fields(session, path=typed))

    row = answered_row(reply, "google-client")
    [(role, text)] = row.notices
    assert role == "alert" and text.startswith("Nothing was imported: ") and text.endswith(".")
    assert reason in text
    assert row.state == "Not imported"
    assert keyring_changes(dashboard) == [] and dashboard.files.deleted == []
    assert "Pz4Kq8Wn3Rt6" not in reply.text and "srv" not in reply.text


def test_an_import_whose_keyring_write_fails_shows_store_failed(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    dashboard.files.files[PATH] = CLIENT_FILE
    dashboard.secrets.failing = {"write"}

    row = answered_row(post(dashboard, session, CLIENT, fields(session, path=PATH)), "google-client")

    assert row.notices == [("alert", STORE_FAILED)]
    assert PATH in dashboard.files.files


# Row: Grant start, waiting and polling ----------------------------------------------------------


def test_grant_answers_with_a_waiting_row_that_polls_itself(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)

    reply = click_grant(dashboard, session)

    row = answered_row(reply, "google-grant-internal_notify")
    assert {k: row.node.attrs.get(k) for k in ("hx-get", "hx-trigger", "hx-swap")} == {
        "hx-get": "/credentials/google/grant/internal_notify",
        "hx-trigger": "every 2s",
        "hx-swap": "outerHTML",
    }
    assert row.waiting == ["Waiting for consent in your browser until 09:35:00 UTC."]
    assert row.state == "Not granted"
    assert row.forms == [] and row.notices == []
    assert len(dashboard.listeners) == 1 and len(dashboard.browser.urls) == 1
    assert keyring_changes(dashboard) == []
    for secret in (STATE, VERIFIER, dashboard.browser.urls[0], SECRET):
        assert secret not in reply.text


def test_the_polled_row_keeps_polling_until_the_flow_ends(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    click_grant(dashboard, session)

    waiting = poll(dashboard, session)
    assert waiting.node.attrs.get("hx-trigger") == "every 2s"
    assert "hx-get" in Html(page(dashboard, session)).by_id("google-grant-internal_notify").attrs

    dashboard.listeners[0].answer = ANSWER
    dashboard.run_jobs()
    done = poll(dashboard, session)

    assert "hx-get" not in done.node.attrs and "hx-trigger" not in done.node.attrs
    assert done.waiting == []
    assert done.state == "Granted for mac@nextronai.com, 2026-11-17 09:30 UTC"
    assert done.buttons() == ["Grant again", "Remove"]
    assert done.notices == []
    html = page(dashboard, session)
    assert html.count("hx-get") == 1  # only the layout's notification list, loaded once
    assert 'hx-get="/notifications" hx-trigger="load"' in html
    assert dashboard.oauth.verifiers == [VERIFIER]


def test_only_a_waiting_row_polls(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    import_client(dashboard)
    click_grant(dashboard, session, "postmaster")

    html = Html(page(dashboard, session))

    polling = [node.attrs.get("id") for node in html.root.all() if "hx-get" in node.attrs]
    assert polling == ["notification-list", "google-grant-postmaster"]  # the layout's loads once
    [layout] = [node for node in html.root.all() if node.attrs.get("id") == "notification-list"]
    assert layout.attrs.get("hx-trigger") == "load"


def test_grant_without_a_client_asks_for_the_import_first(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    row = answered_row(click_grant(dashboard, session), "google-grant-internal_notify")

    assert row.notices == [("alert", "Import the Google Desktop client first.")]
    assert dashboard.listeners == [] and dashboard.browser.urls == []


def test_grant_with_an_unreadable_keyring_shows_store_failed(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    dashboard.secrets.failing = {"read"}

    row = answered_row(click_grant(dashboard, session), "google-grant-internal_notify")

    assert row.notices == [("alert", STORE_FAILED)]
    assert row.state == "Unknown: Windows Credential Manager could not be read"
    assert dashboard.listeners == [] and dashboard.browser.urls == [] and dashboard.jobs == []


def test_an_unknown_polled_grant_is_404(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    for grant in ("gmail", "Internal_notify", "x" * 40):
        reply = dashboard.get(f"/credentials/google/grant/{grant}", cookie=session[0])
        assert reply.status == 404
        assert reply.header(b"cache-control") == b"no-store"


def test_the_polled_row_needs_a_session(dashboard: Dashboard) -> None:
    reply = dashboard.get("/credentials/google/grant/internal_notify")

    assert reply.status == 401 and b"<form" not in reply.body


# Rows: outcomes as text -------------------------------------------------------------------------


def failure_of(row: Row) -> str:
    [(role, text)] = row.notices
    assert role == "alert"
    assert row.node.all("p", role="alert")[0].attrs["class"] == "notice notice-refused"
    return text


def test_a_timeout_reads_consent_failed(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    import_client(dashboard)
    click_grant(dashboard, session)
    dashboard.run_jobs()  # Google never answered

    row = poll(dashboard, session)

    assert failure_of(row) == "Consent failed: no answer from Google within 5 minutes."
    assert row.state == "Not granted" and row.buttons() == ["Grant"]


@pytest.mark.parametrize(
    "answer",
    [{"state": ("wrong",), "code": (CODE,)}, {"code": (CODE,)}, {"state": (STATE,) * 2, "code": (CODE,)}],
)
def test_a_bad_state_reads_consent_failed(
    answer: dict[str, tuple[str, ...]], dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    dashboard.next_answer = answer
    click_grant(dashboard, session)
    dashboard.run_jobs()

    text = failure_of(poll(dashboard, session))

    assert text.startswith("Consent failed: ") and "did not match" in text
    assert dashboard.oauth.calls == []


def test_a_declined_consent_reads_consent_failed(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    dashboard.next_answer = {"state": (STATE,), "error": ("access_denied",)}
    click_grant(dashboard, session)
    dashboard.run_jobs()

    assert failure_of(poll(dashboard, session)) == (
        "Consent failed: consent was declined or cancelled on Google's page."
    )


def test_a_wrong_domain_reads_the_rule_and_the_revocation(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)

    row = granted(dashboard, session, token_set(email="x@gmail.com"))

    assert failure_of(row) == (
        "Consent failed: internal_notify must be a nextronai.com account. "
        "Google access was revoked."
    )
    assert row.state == "Not granted"


def test_an_account_off_the_allowlist_reads_why() -> None:
    dashboard = Dashboard(clock=FrozenClock(T), allowlist=[])
    cookie = dashboard.open_session()
    session = (cookie, dashboard.csrf_token(cookie))
    import_client(dashboard)

    row = granted(dashboard, session)

    assert failure_of(row) == (
        "Consent failed: outside prod, the account must be on the Dev and test allowlist "
        "(Settings, Engine). Google access was revoked."
    )


def test_a_failed_revoke_after_a_refusal_says_how_to_revoke_by_hand(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    dashboard.oauth.revoke_error = AdapterError(True, "google_unavailable", "down")

    row = granted(dashboard, session, token_set(scope="openid email"))

    assert failure_of(row) == (
        "Consent failed: the permission was not ticked on Google's consent page; grant it "
        "again and tick it. Revoking it at Google failed: remove Nextron at "
        "myaccount.google.com/permissions."
    )


def test_a_regrant_on_the_same_account_says_the_old_token_was_kept(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    granted(dashboard, session, token_set(refresh_token="1//first"))

    row = granted(dashboard, session, token_set(refresh_token="1//second"))

    assert row.notices == [
        (
            "status",
            "The previous grant was replaced. It was not revoked, because a stored grant uses "
            "the same Google account, and revoking it would end that grant too.",
        )
    ]
    assert dashboard.oauth.revoked == []


def test_a_regrant_on_another_account_says_the_old_token_was_revoked(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    dashboard.allowlist.append("b@nextronai.com")
    granted(dashboard, session, token_set(refresh_token="1//first"))

    row = granted(dashboard, session, token_set(email="b@nextronai.com", refresh_token="1//b"))

    assert row.notices == [("status", "The previous grant was replaced. Google access was revoked.")]
    assert row.state == "Granted for b@nextronai.com, 2026-11-17 09:30 UTC"


def test_a_grant_that_needs_reconsent_says_so(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    dashboard.secrets.store[(SERVICE, "google/postmaster")] = "1//p"
    dashboard.secrets.store[(SERVICE, "meta/google/postmaster")] = encode_grant_meta(
        GrantMeta("x@gmail.com", T, datetime(2026, 11, 18, 7, 5, 9, tzinfo=UTC))
    )

    row = Row(Html(page(dashboard, session)).by_id("google-grant-postmaster"))

    assert row.state == (
        "Needs re-consent: Google refused the stored grant on 2026-11-18 07:05 UTC. "
        "Grant it again."
    )
    assert row.buttons() == ["Grant again", "Remove"]


def test_an_unreadable_keyring_shows_unknown(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    dashboard.secrets.failing = {"read"}

    html = Html(page(dashboard, session))

    unreadable = "Unknown: Windows Credential Manager could not be read"
    assert Row(html.by_id("google-client")).state == unreadable
    assert Row(html.by_id("google-grant-postmaster")).state == unreadable


# Row: Remove ------------------------------------------------------------------------------------


def test_remove_asks_first_then_revokes_and_deletes(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    granted(dashboard, session, token_set(refresh_token="1//r"))
    dashboard.secrets.calls.clear()

    asked = answered_row(
        post(dashboard, session, REMOVE, fields(session, grant="internal_notify")),
        "google-grant-internal_notify",
    )

    assert keyring_changes(dashboard) == [] and dashboard.oauth.revoked == []
    [(role, question)] = asked.notices
    assert role == "alert" and question.startswith(
        "Remove the Internal notifications (internal_notify) grant?"
    )
    [confirm] = asked.forms
    assert confirm.attrs["hx-post"] == REMOVE
    assert asked.hidden(confirm) == {
        "csrf_token": session[1],
        "grant": "internal_notify",
        "confirm": "yes",
    }
    assert [(a.attrs.get("href"), a.text) for a in asked.node.all("a")] == [
        ("/credentials", "Cancel")
    ]

    removed = answered_row(
        post(dashboard, session, REMOVE, fields(session, grant="internal_notify", confirm="yes")),
        "google-grant-internal_notify",
    )

    assert dashboard.oauth.revoked == ["1//r"]
    assert keyring_changes(dashboard) == [
        ("delete", "google/internal_notify"),
        ("delete", "meta/google/internal_notify"),
    ]
    assert removed.notices == [("status", "Removed. Google access was revoked.")]
    assert removed.state == "Not granted" and removed.buttons() == ["Grant"]


def test_a_removal_whose_revoke_fails_still_deletes_and_says_so(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    granted(dashboard, session)
    dashboard.oauth.revoke_error = AdapterError(True, "google_unavailable", "down")

    removed = answered_row(
        post(dashboard, session, REMOVE, fields(session, grant="internal_notify", confirm="yes")),
        "google-grant-internal_notify",
    )

    assert removed.notices == [
        (
            "alert",
            "Removed. Revoking it at Google failed: remove Nextron at "
            "myaccount.google.com/permissions.",
        )
    ]
    assert removed.state == "Not granted"


def test_a_removal_that_shares_an_account_with_postmaster_is_not_revoked(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    granted(dashboard, session, token_set(refresh_token="1//i"))
    granted(
        dashboard,
        session,
        token_set(scope="https://www.googleapis.com/auth/postmaster.readonly", refresh_token="1//p"),
        "postmaster",
    )

    removed = answered_row(
        post(dashboard, session, REMOVE, fields(session, grant="internal_notify", confirm="yes")),
        "google-grant-internal_notify",
    )

    assert removed.notices == [
        (
            "status",
            "Removed. It was not revoked, because a stored grant uses the same Google account, "
            "and revoking it would end that grant too.",
        )
    ]
    assert dashboard.oauth.revoked == []


def test_a_removal_whose_keyring_fails_shows_store_failed(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    granted(dashboard, session)
    dashboard.secrets.failing = {"delete"}

    row = answered_row(
        post(dashboard, session, REMOVE, fields(session, grant="internal_notify", confirm="yes")),
        "google-grant-internal_notify",
    )

    assert row.notices == [("alert", STORE_FAILED)]


# Refusals ---------------------------------------------------------------------------------------

MALFORMED: list[tuple[str, str, bytes]] = [
    ("client: no path", CLIENT, b""),
    ("client: extra field", CLIENT, urlencode({"path": PATH, "grant": "postmaster"}).encode()),
    ("client: repeated path", CLIENT, b"path=a.json&path=b.json"),
    ("grant: unknown", GRANT, b"grant=gmail"),
    ("grant: case", GRANT, b"grant=Postmaster"),
    ("grant: none", GRANT, b""),
    ("grant: repeated", GRANT, b"grant=postmaster&grant=postmaster"),
    ("grant: extra", GRANT, b"grant=postmaster&confirm=yes"),
    ("remove: unknown", REMOVE, b"grant=gmail&confirm=yes"),
    ("remove: confirm=no", REMOVE, b"grant=postmaster&confirm=no"),
    ("remove: blank confirm", REMOVE, b"grant=postmaster&confirm="),
    ("remove: repeated confirm", REMOVE, b"grant=postmaster&confirm=yes&confirm=yes"),
    ("remove: no grant", REMOVE, b"confirm=yes"),
]


@pytest.mark.parametrize(("case", "path", "sent"), MALFORMED, ids=[c for c, *_ in MALFORMED])
def test_a_malformed_form_is_refused_with_400_and_starts_nothing(
    case: str,
    path: str,
    sent: bytes,
    dashboard: Dashboard,
    session: tuple[bytes, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    import_client(dashboard)
    dashboard.files.files[PATH] = CLIENT_FILE
    caplog.set_level(logging.INFO)
    body = form(csrf_token=session[1]) + (b"&" + sent if sent else b"")

    reply = post(dashboard, session, path, body)

    assert reply.status == 400 and reply.body == MALFORMED_FORM.encode()
    assert dashboard.listeners == [] and dashboard.browser.urls == []
    assert keyring_changes(dashboard) == [] and dashboard.files.deleted == []
    assert dashboard.oauth.calls == []
    assert [r.getMessage() for r in caplog.records].count("request_refused") == 1


@pytest.mark.parametrize("path", [CLIENT, GRANT, REMOVE])
@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_a_post_without_the_sessions_token_is_refused_with_403(
    path: str, token: str | None, dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    import_client(dashboard)
    granted(dashboard, session)
    dashboard.files.files[PATH] = CLIENT_FILE
    dashboard.secrets.calls.clear()
    dashboard.oauth.calls.clear()
    listeners = len(dashboard.listeners)
    values = {
        CLIENT: {"path": PATH},
        GRANT: {"grant": "internal_notify"},
        REMOVE: {"grant": "internal_notify", "confirm": "yes"},
    }[path]
    body = form(**values) if token is None else form(csrf_token=token, **values)

    reply = post(dashboard, session, path, body, token=False)

    assert reply.status == 403 and reply.body == SECURITY_CHECK_FAILED.encode()
    assert len(dashboard.listeners) == listeners
    assert keyring_changes(dashboard) == [] and dashboard.oauth.calls == []
    assert dashboard.files.deleted == []


def test_a_post_without_a_session_is_refused_with_401(dashboard: Dashboard) -> None:
    import_client(dashboard)

    reply = dashboard.post(
        GRANT, headers=[(b"content-type", FORM)], body=form(csrf_token="x", grant="postmaster")
    )

    assert reply.status == 401
    assert dashboard.listeners == [] and dashboard.browser.urls == []


def test_the_routes_are_registered_by_name() -> None:
    names = {getattr(route, "name", None) for route in Dashboard().app.routes}

    assert {
        "google_client_import",
        "google_grant_start",
        "google_grant_row",
        "google_grant_remove",
    } <= names
    assert web_app.NO_STORE == {"Cache-Control": "no-store"}
