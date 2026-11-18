"""The notification area (UX-DR5, Story 1.12) through ``Dashboard(units=units)`` on a temporary
database at head: ``GET /notifications``, ``POST /notifications/dismiss`` and its refusals, the
base layout's notice elements, the exact ``htmx-config`` meta JSON, and the guard's plain-text
401 for an HTMX request without a session. Since Story 1.19 also the recovery banner: first in
the area while recovery mode is on (with the error count and the notifications after it), on
every page that loads the area, and gone once recovery mode ends."""

import json
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from nextron.db.models.notify import NotificationRecord
from nextron.app.ops.api import Recovery
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.recovery import RECOVERY_BANNER
from tests.fakes.clock import FrozenClock
from nextron.web.app import STATIC_DIR
from nextron.web.guard import LOCKED_TEXT
from tests.asgi import DEFAULT_HOST, Reply, request
from tests.dashboard import FORM, Dashboard, form

HTMX_CONFIG = (
    '{"allowEval":false,"allowScriptTags":false,"selfRequestsOnly":true,'
    '"historyCacheSize":0,"includeIndicatorStyles":false,'
    '"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},'
    '{"code":"[45]..","swap":true,"error":true,"target":"#request-error",'
    '"swapOverride":"textContent"}]}'
)
SUBJECTS = [f"01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}" for n in range(10, 19)]  # nine
DISMISS = "/notifications/dismiss"
ONE = ["Job dead: enrich_lead x, attempts: 1"]


class Tags(HTMLParser):
    """The tags of a fragment, each with its attributes, and its non-blank texts, stripped."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.texts: list[str] = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.texts.append(data.strip())

    def find(self, tag: str, **attrs: str) -> list[dict[str, str | None]]:
        return [
            found
            for name, found in self.tags
            if name == tag and all(found.get(key) == value for key, value in attrs.items())
        ]


@pytest.fixture
def dashboard(units: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=units)


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def seed(dashboard: Dashboard, units: UnitOfWorkFactory, texts: list[str]) -> list[str]:
    """One dashboard notification per text, a minute apart, oldest first; their ids."""

    @use_case
    def raise_one(subject: str, text: str) -> None:
        with current().transaction():
            dashboard.notify.alert("job_dead", subject, text, [Channel.DASHBOARD])

    ids = []
    for subject, text in zip(SUBJECTS, texts, strict=False):
        with units.open():
            raise_one(subject, text)
        dashboard.clock.advance(timedelta(minutes=1))
        with units.open(read_only=True):
            statement = select(NotificationRecord.id).where(
                NotificationRecord.subject == subject
            )
            ids.append(current().session.execute(statement).scalar_one())
    return ids


def area(dashboard: Dashboard, cookie: bytes) -> Reply:
    return dashboard.get("/notifications", cookie=cookie)


def dismiss(dashboard: Dashboard, session: tuple[bytes, str], body: bytes) -> Reply:
    cookie, token = session
    return dashboard.post(
        DISMISS, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
    )


def shown_count(reply: Reply) -> str | None:
    found = [text for text in Tags(reply.text).texts if text.startswith("Notifications (")]
    return found[0] if found else None


# Row: Area --------------------------------------------------------------------------------------


def test_with_nothing_undismissed_the_area_is_empty(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = area(dashboard, session[0])

    assert reply.status == 200
    assert reply.body == b""
    assert reply.header(b"cache-control") == b"no-store"


def test_the_area_shows_the_count_the_newest_five_and_how_many_more(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    texts = [f"Job dead: enrich_lead {subject}, attempts: 1" for subject in SUBJECTS]
    ids = seed(dashboard, units, texts)
    for notification_id in ids[:2]:  # the two oldest are dismissed
        assert dismiss(dashboard, session, form(notification=notification_id)).status == 200

    reply = area(dashboard, session[0])

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    page = Tags(reply.text)
    assert page.find("h2", **{"class": "notifications-heading"})
    assert shown_count(reply) == "Notifications (7)"
    shown = [item["value"] for item in page.find("input", name="notification")]
    assert shown == ids[:1:-1][:5]  # the newest five, newest first
    assert [text for text in page.texts if text.startswith("Job dead")] == texts[:1:-1][:5]
    assert "2026-11-16 09:08 UTC" in page.texts  # the newest one's time
    assert "2026-11-16 09:04 UTC" in page.texts  # the fifth newest
    assert "and 2 more" in page.texts
    forms = page.find("form")
    assert forms == [
        {"method": "post", "hx-post": "/notifications/dismiss", "hx-target": "#notification-list"}
    ] * 5
    assert len(page.find("input", type="hidden", name="csrf_token", value=session[1])) == 5
    assert len(page.find("button", type="submit")) == 5


def test_dismiss_answers_with_the_area_again_one_fewer(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    ids = seed(dashboard, units, [f"Job dead: x {subject}, attempts: 1" for subject in SUBJECTS])
    assert shown_count(area(dashboard, session[0])) == "Notifications (9)"

    reply = dismiss(dashboard, session, form(notification=ids[-1]))

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    page = Tags(reply.text)
    assert shown_count(reply) == "Notifications (8)"
    assert [item["value"] for item in page.find("input", name="notification")] == ids[-2:-7:-1]
    assert "and 3 more" in page.texts

    again = dismiss(dashboard, session, form(notification=ids[-1]))  # already dismissed

    assert again.status == 200 and shown_count(again) == "Notifications (8)"


def test_the_last_dismiss_empties_the_area(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    [only] = seed(dashboard, units, ONE)

    reply = dismiss(dashboard, session, form(notification=only))

    assert (reply.status, reply.body) == (200, b"")
    assert area(dashboard, session[0]).body == b""


def test_an_unknown_id_dismisses_nothing_and_shows_the_area(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    seed(dashboard, units, ONE)

    reply = dismiss(dashboard, session, form(notification="01JD3QZ8X5V9K2M7N4P6R8T0ZZ"))

    assert reply.status == 200 and shown_count(reply) == "Notifications (1)"


def test_notification_text_renders_as_plain_text(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    hostile = '<script>alert(1)</script><img src=x onerror=alert(2)> hx-post="/x"'
    seed(dashboard, units, [hostile])

    reply = area(dashboard, session[0])

    assert "<script>" not in reply.text and "<img" not in reply.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in reply.text
    assert hostile in Tags(reply.text).texts


MALFORMED = [
    ("no field", b""),
    ("not a ulid", form(notification="not-an-id")),
    ("lowercase", form(notification="01jd3qz8x5v9k2m7n4p6r8t0zz")),
    ("blank", form(notification="")),
    ("extra field", form(notification=SUBJECTS[0], other="x")),
    ("wrong name", form(note=SUBJECTS[0])),
    ("repeated", form(notification=SUBJECTS[0]) + b"&notification=" + SUBJECTS[1].encode()),
    ("bad body", form(notification=SUBJECTS[0]) + b"&&"),
]


@pytest.mark.parametrize("body", [body for _, body in MALFORMED], ids=[n for n, _ in MALFORMED])
def test_a_malformed_dismiss_is_refused_with_400_and_changes_nothing(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str], body: bytes
) -> None:
    seed(dashboard, units, ONE)
    _, token = session
    sent = form(csrf_token=token) + (b"&" + body if body else b"")

    reply = dismiss(dashboard, session, sent)

    assert reply.status == 400
    assert reply.header(b"content-type") == b"text/plain; charset=utf-8"
    assert reply.body == b"Request refused: malformed form"
    assert shown_count(area(dashboard, session[0])) == "Notifications (1)"


def test_a_dismiss_without_the_csrf_token_is_refused_with_403(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    [only] = seed(dashboard, units, ONE)
    cookie, _ = session

    reply = dashboard.post(
        DISMISS, cookie=cookie, headers=[(b"content-type", FORM)], body=form(notification=only)
    )

    assert reply.status == 403
    assert reply.header(b"content-type") == b"text/plain; charset=utf-8"
    assert reply.body == b"Request refused: security check failed"
    assert shown_count(area(dashboard, session[0])) == "Notifications (1)"


# The base layout --------------------------------------------------------------------------------


def test_the_layout_holds_the_error_notice_and_loads_the_list_once(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = dashboard.get("/", cookie=session[0])

    page = Tags(reply.text)
    [section] = page.find("section", id="notifications")
    assert section == {"id": "notifications", "aria-live": "polite"}
    assert page.find("p", id="request-error", **{"class": "request-error", "role": "alert"})
    assert page.find(
        "div", id="notification-list", **{"hx-get": "/notifications", "hx-trigger": "load, call-outcome-saved from:body"}
    )
    assert (
        '<section id="notifications" aria-live="polite">'
        '<p id="request-error" class="request-error" role="alert"></p>'
        '<div id="notification-list" hx-get="/notifications" hx-trigger="load, call-outcome-saved from:body"></div>'
        "</section>"
    ) in reply.text  # both empty: no whitespace, so each matches :empty
    assert not page.find("div", **{"hx-trigger": "every 30s"})  # loaded once, never polled


def test_the_htmx_config_is_exactly_the_specified_json(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    page = Tags(dashboard.get("/", cookie=session[0]).text)

    [meta] = page.find("meta", name="htmx-config")
    assert meta["content"] == HTMX_CONFIG
    assert json.loads(HTMX_CONFIG)["responseHandling"] == [
        {"code": "204", "swap": False},
        {"code": "[23]..", "swap": True},
        {
            "code": "[45]..",
            "swap": True,
            "error": True,
            "target": "#request-error",
            "swapOverride": "textContent",
        },
    ]


def test_empty_notices_take_no_space_and_are_never_hidden() -> None:
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")  # universal newlines
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    assert not re.search(r"display\s*:\s*none", rules)
    assert ".request-error {\n  margin: 0;\n}" in rules  # an empty <p> has no margin
    assert "#request-error:not(:empty) {\n  padding:" in rules  # space only with a notice
    assert "#notification-list:not(:empty) {\n  padding:" in rules
    assert "#notifications:not(:empty)" not in rules  # the section is never empty now


# The guard --------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/notifications", "/", "/settings"])
def test_an_htmx_request_without_a_session_gets_the_plain_text_401(
    dashboard: Dashboard, path: str
) -> None:
    reply = request(
        dashboard.app, path, headers=[(b"host", DEFAULT_HOST), (b"hx-request", b"true")]
    )

    assert reply.status == 401
    assert reply.header(b"content-type") == b"text/plain; charset=utf-8"
    assert reply.body == LOCKED_TEXT.encode()
    assert LOCKED_TEXT == (
        "The dashboard is locked: run nextron open in a terminal to open it again."
    )
    assert reply.header(b"cache-control") == b"no-store"


def test_an_htmx_post_without_a_session_gets_the_plain_text_401(dashboard: Dashboard) -> None:
    reply = dashboard.post(
        DISMISS,
        headers=[(b"hx-request", b"true"), (b"content-type", FORM)],
        body=form(notification=SUBJECTS[0]),
    )

    assert (reply.status, reply.body) == (401, LOCKED_TEXT.encode())


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"hx-request", b"false")],
        [(b"hx-request", b"True")],
        [(b"hx-request", b"true"), (b"hx-request", b"true")],
        [(b"hx-request", b"")],
    ],
    ids=["none", "false", "capitalised", "twice", "blank"],
)
def test_any_other_request_without_a_session_gets_the_html_locked_page(
    dashboard: Dashboard, headers: list[tuple[bytes, bytes]]
) -> None:
    reply = request(dashboard.app, "/notifications", headers=[(b"host", DEFAULT_HOST), *headers])

    assert reply.status == 401
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    assert b"<h1>Dashboard locked</h1>" in reply.body


# Row: Gate and UI (Story 1.19) -------------------------------------------------------------------

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def turn_recovery_on(units: UnitOfWorkFactory) -> str:
    with units.open():
        row = Recovery(FrozenClock(T0)).begin(
            backup_at=T0 - timedelta(days=1),
            source_name="nextron-test-20261116T090000.000Z.db",
            pre_restore_name=None,
        )
    return row.id


def end_recovery(units: UnitOfWorkFactory) -> None:
    recovery = Recovery(FrozenClock(T0 + timedelta(minutes=1)))
    with units.open():
        recovery.pending(frozenset())  # every step not applicable
    with units.open():
        assert recovery.finish() is False


def banners(reply: Reply) -> list[dict[str, str | None]]:
    return Tags(reply.text).find("p", **{"class": "recovery-banner"})


def test_while_recovery_mode_is_on_the_area_starts_with_the_banner(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    turn_recovery_on(units)

    reply = area(dashboard, session[0])

    assert reply.status == 200
    assert reply.text.startswith(
        '<p class="recovery-banner" role="alert"><a href="/health/panel">'
        f"{RECOVERY_BANNER}</a></p>"
    )
    assert banners(reply) == [{"class": "recovery-banner", "role": "alert"}]
    assert Tags(reply.text).texts == [RECOVERY_BANNER]  # nothing else needs attention
    assert shown_count(reply) is None


def test_the_banner_comes_before_the_errors_and_the_notifications(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    seed(dashboard, units, ONE)
    turn_recovery_on(units)

    reply = area(dashboard, session[0])

    texts = Tags(reply.text).texts
    assert texts[0] == RECOVERY_BANNER
    assert texts.index(RECOVERY_BANNER) < texts.index("Notifications (1)")


def test_the_banner_shows_on_the_dismiss_answer_too(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    [notification_id] = seed(dashboard, units, ONE)
    turn_recovery_on(units)

    reply = dismiss(dashboard, session, form(notification=notification_id))

    assert reply.status == 200
    assert Tags(reply.text).texts == [RECOVERY_BANNER]


def test_the_banner_is_gone_once_recovery_mode_ends(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str]
) -> None:
    turn_recovery_on(units)
    assert banners(area(dashboard, session[0]))

    end_recovery(units)

    reply = area(dashboard, session[0])
    assert banners(reply) == []
    assert reply.body == b""


@pytest.mark.parametrize("path", ["/", "/settings", "/health/panel"])
def test_every_page_loads_the_area_that_holds_the_banner(
    dashboard: Dashboard, units: UnitOfWorkFactory, session: tuple[bytes, str], path: str
) -> None:
    turn_recovery_on(units)

    reply = dashboard.get(path, cookie=session[0])

    # the layout loads the area once per page view, and the area holds the banner
    assert reply.status == 200
    assert Tags(reply.text).find(
        "div", id="notification-list", **{"hx-get": "/notifications", "hx-trigger": "load, call-outcome-saved from:body"}
    )
    assert banners(area(dashboard, session[0]))
