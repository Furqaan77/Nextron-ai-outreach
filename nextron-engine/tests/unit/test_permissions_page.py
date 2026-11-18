"""Story 10.7: the Channel Permission page of a Contact (``nextron.web.permissions_page``) through
``Dashboard(units=...)`` on a temporary database at head with the real subscriber registry: the
Lead page links it from each live Contact's Action cell (eight columns kept); the form states
what doesn't qualify and offers only the valid actions; every POST needs CSRF and exactly its
fields (400 otherwise); refusals re-render 200 with ``aria-invalid``; record, the inbound update,
revoke and opt-out answer the panel; an erased Contact shows no form and runs nothing; every 404;
the routes declare personal data and come before ``/leads/{lead_id}``; nothing links to the
platforms; and the view reads only through ``nextron.app.queries``."""

import ast
import re
from datetime import timedelta

import pytest

from nextron.app.pipeline.channel_permissions import OptedOut
from nextron.app.setup.api import Setup
from nextron.domain.channel_permission import NOT_QUALIFYING
from nextron.web.permissions_page import opted_out
from nextron.web.routing import declared_personal_data
from tests.contracts.support import PACKAGE
from tests.dashboard import form
from tests.unit.test_erase_page import (
    NAME,
    UNKNOWN,
    World,
    jane_and_bob,
    world,  # noqa: F401 - the fixture
)
from tests.unit.test_erase_page import path_of as erase_path
from tests.unit.test_leads_page import Tree

EVIDENCE = "Jane started a conversation with our Instagram account about the intake demo"
HANDLE = "jane.doe"
RECORD = {
    "channel": "instagram",
    "prospect_action": "started_instagram_conversation",
    "occurred_at": "2026-11-17T08:00",  # an hour before the clock's T
    "evidence": EVIDENCE,
    "whatsapp_number": "",
    "instagram_handle": f"@{HANDLE}",
}
PLATFORM_LINK = re.compile(r"instagram\.com|instagr\.am|wa\.me|whatsapp\.com", re.IGNORECASE)


def path_of(lead: str, contact: str) -> str:
    return f"/leads/{lead}/contacts/{contact}/permissions"


def zone(world: World, name: str = "Europe/London") -> None:
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": name})


def submit(world: World, path: str, **fields: str) -> Tree:
    reply = world.post(path, form(csrf_token=world.token, **fields))
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    assert not PLATFORM_LINK.search(reply.text)
    return Tree(reply.text)


def recorded(world: World) -> tuple[str, str, str]:
    """A Lead, Jane on it and her recorded Instagram permission's id."""
    zone(world)
    lead, jane, _ = jane_and_bob(world)
    submit(world, path_of(lead, jane), **RECORD)
    [(permission_id,)] = world.sql("SELECT id FROM channel_permissions")
    return lead, jane, permission_id


def notice(panel: Tree) -> tuple[str, str]:
    [found] = [
        node
        for node in panel.find_all("p", class_="notice")
        if node.attrs.get("role") in ("status", "alert")
    ]
    return found.attrs["role"], found.text


def permissions_count(world: World) -> int:
    return int(world.sql("SELECT COUNT(*) FROM channel_permissions")[0][0])


# The Lead page link -----------------------------------------------------------------------------


def test_the_lead_page_links_each_live_contact_in_its_action_cell(world: World) -> None:
    lead, jane, bob = jane_and_bob(world)

    page = world.page(f"/leads/{lead}")

    rows = page.find_all("tr", class_="contact-row")
    assert all(len(row.find_all("td")) == 8 for row in rows)
    links = [row.find("a", class_="permissions-link") for row in rows]
    assert [(link.text, link.attrs["href"]) for link in links] == [
        ("Channel permissions", path_of(lead, jane)),
        ("Channel permissions", path_of(lead, bob)),
    ]
    world.erase(erase_path(lead, jane))
    rows = world.page(f"/leads/{lead}").find_all("tr", class_="contact-row")
    assert [len(row.find_all("a", class_="permissions-link")) for row in rows] == [0, 1]


# The page ---------------------------------------------------------------------------------------


def test_the_page_states_what_does_not_qualify_and_offers_only_valid_actions(
    world: World,
) -> None:
    zone(world)
    lead, jane, _ = jane_and_bob(world)

    page = world.page(path_of(lead, jane))

    assert page.find("title").text == "Nextron: Channel permissions"
    assert NOT_QUALIFYING in page.text
    assert "No Channel Permission yet." in page.text
    [panel] = page.find_all("section", id="permissions-panel")
    [formed] = panel.find_all("form")
    assert formed.attrs["method"] == "post"
    assert formed.attrs["hx-post"] == path_of(lead, jane)
    assert formed.attrs["action"] == formed.attrs["hx-post"]  # posts the same without HTMX
    assert formed.attrs["hx-target"] == "#permissions-panel"
    assert not [node for node in formed.ancestors() if "hx-disable" in node.attrs]
    channel = formed.find("select", id="permission-new-channel")
    assert [option.attrs["value"] for option in channel.find_all("option")] == [
        "whatsapp",
        "instagram",
    ]
    action = formed.find("select", id="permission-new-prospect_action")
    groups = action.find_all("optgroup")
    assert [
        (group.attrs["label"], [option.attrs["value"] for option in group.find_all("option")])
        for group in groups
    ] == [
        ("WhatsApp", ["started_whatsapp_conversation", "gave_number_for_whatsapp"]),
        ("Instagram", ["started_instagram_conversation"]),
    ]
    names = [node.attrs["name"] for node in formed.walk() if node.tag in ("input", "select", "textarea")]
    assert names == [
        "csrf_token",
        "channel",
        "prospect_action",
        "occurred_at",
        "evidence",
        "whatsapp_number",
        "instagram_handle",
    ]
    assert formed.find("textarea").attrs["maxlength"] == "300"
    assert formed.find("input", id="permission-new-occurred_at").attrs["type"] == "datetime-local"
    assert "Europe/London" in page.text
    assert NAME in page.text
    assert not PLATFORM_LINK.search(world.get(path_of(lead, jane)).text)


def test_an_unset_timezone_is_said_and_times_show_in_utc(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)

    page = world.page(path_of(lead, jane))

    assert "The sender's timezone isn't set" in page.text
    panel = submit(world, path_of(lead, jane), **RECORD)
    assert notice(panel)[0] == "alert"
    assert "Set the sender's timezone on the Settings page first" in notice(panel)[1]
    assert permissions_count(world) == 0


# The POSTs --------------------------------------------------------------------------------------


def test_recording_answers_the_panel_with_the_permission_and_its_window(world: World) -> None:
    lead, jane, permission_id = recorded(world)

    panel = Tree(world.get(path_of(lead, jane)).text)

    row = panel.find("section", **{"aria-labelledby": f"permission-{permission_id}-heading"})
    assert row.find("h3").text == "Instagram: active"
    facts = [node.text for node in row.find("dl").walk() if node.tag in ("dt", "dd")]
    assert facts == [
        "What the prospect did",
        "Started an Instagram conversation with us",
        "When",
        "2026-11-17 08:00 (Europe/London)",
        "Instagram handle",
        HANDLE,
        "Evidence",
        EVIDENCE,
    ]
    assert "hx-disable" in row.find("dl").attrs
    window = row.find("p", class_="permission-window")
    assert window.text == "24-hour window closes at 2026-11-18 08:00 (Europe/London)"
    assert "role" not in window.attrs  # not re-announced on every swap
    assert row.find_all("p", class_="window-closed") == []
    posts = [found.attrs["hx-post"] for found in row.find_all("form")]
    base = f"{path_of(lead, jane)}/{permission_id}"
    assert posts == [f"{base}/inbound", f"{base}/revoke", f"{base}/do-not-contact"]
    assert [found.attrs["action"] for found in row.find_all("form")] == posts


def test_a_closed_window_line_is_marked_closed(world: World) -> None:
    lead, jane, _ = recorded(world)
    world.clock.advance(timedelta(hours=24))

    panel = Tree(world.get(path_of(lead, jane)).text)

    [window] = panel.find_all("p", class_="window-closed")
    assert "permission-window" in window.attrs["class"].split()
    assert window.text == (
        "24-hour window closed at 2026-11-18 08:00 (Europe/London): don't send"
    )


def test_an_unparseable_stored_timezone_shows_the_unset_note(world: World) -> None:
    zone(world)
    lead, jane, _ = jane_and_bob(world)
    world.sql("UPDATE settings SET value = 'Mars/Olympus' WHERE key = 'sender_timezone'")

    reply = world.get(path_of(lead, jane))

    assert reply.status == 200
    assert "The sender's timezone isn't set" in Tree(reply.text).text


def test_the_record_answer_says_recorded(world: World) -> None:
    zone(world)
    lead, jane, _ = jane_and_bob(world)

    panel = submit(world, path_of(lead, jane), **RECORD)

    assert notice(panel) == ("status", "Permission recorded.")
    assert len(panel.find_all("section", id="permissions-panel")) == 1
    assert panel.find_all("title") == []  # the panel alone


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("occurred_at", "2026-11-17T10:00", "Can't be in the future"),
        ("evidence", "x" * 301, "At most 300 characters"),
        ("prospect_action", "gave_number_for_whatsapp", "Choose what the prospect did"),
    ],
)
def test_a_refusal_re_renders_the_form_with_the_field_marked(
    world: World, field: str, value: str, message: str
) -> None:
    zone(world)
    lead, jane, _ = jane_and_bob(world)

    panel = submit(world, path_of(lead, jane), **{**RECORD, field: value})

    assert notice(panel) == ("alert", "Nothing was changed.")
    marked = [node for node in panel.root.walk() if node.attrs.get("aria-invalid") == "true"]
    assert [node.attrs["name"] for node in marked] == [field]
    assert message in panel.find("p", id=f"permission-new-{field}-error").text
    kept = panel.find("input", id="permission-new-instagram_handle")
    assert kept.attrs["value"] == f"@{HANDLE}"
    assert permissions_count(world) == 0


def test_a_blocked_contact_is_refused_with_the_reason(world: World) -> None:
    zone(world)
    lead, jane, _ = jane_and_bob(world)
    world.sql(
        "UPDATE leads SET pipeline_stage = 'lost' WHERE id = ?", (lead,)
    )

    panel = submit(world, path_of(lead, jane), **RECORD)

    role, text = notice(panel)
    assert role == "alert"
    assert "this Contact can't be contacted (the Lead is closed as lost)" in text
    assert permissions_count(world) == 0


def test_the_inbound_update_revoke_and_opt_out_answer_the_panel(world: World) -> None:
    lead, jane, permission_id = recorded(world)
    base = f"{path_of(lead, jane)}/{permission_id}"

    panel = submit(world, f"{base}/inbound", last_inbound_at="2026-11-17T08:30")
    assert notice(panel) == ("status", "Last inbound message updated.")
    assert "closes at 2026-11-18 08:30" in panel.find("p", class_="permission-window").text

    panel = submit(world, f"{base}/inbound", last_inbound_at="2026-11-17T08:00")
    assert notice(panel) == ("alert", "Nothing was changed.")
    inbound = panel.find("input", id=f"permission-{permission_id}-last_inbound_at")
    assert inbound.attrs["aria-invalid"] == "true"

    panel = submit(world, f"{base}/revoke")
    assert notice(panel) == ("status", "Permission revoked.")
    assert panel.find_all("p", class_="permission-window") == []
    row = panel.find("section", **{"aria-labelledby": f"permission-{permission_id}-heading"})
    assert row.find("h3").text == "Instagram: revoked"
    facts = [node.text for node in row.find("dl").walk() if node.tag in ("dt", "dd")]
    assert facts[-2:] == ["Revoked", "2026-11-17 09:00 (Europe/London)"]
    assert [found.attrs["hx-post"] for found in row.find_all("form")] == [
        f"{base}/do-not-contact"
    ]  # no Revoke and no inbound form any more
    assert "Opted out on this channel" in row.text
    panel = submit(world, f"{base}/revoke")
    assert notice(panel) == ("alert", "Nothing was changed. This permission is already revoked.")

    panel = submit(world, f"{base}/do-not-contact")
    role, text = notice(panel)
    assert role == "status" and text.startswith("Opted out: ")
    assert "already there, 0 permissions revoked." in text
    assert "Every channel is blocked for this person from now on." in text
    assert world.sql("SELECT DISTINCT source FROM suppression_entries") == [("operator",)]


def test_each_post_needs_csrf_and_exactly_its_fields(world: World) -> None:
    lead, jane, permission_id = recorded(world)
    base = f"{path_of(lead, jane)}/{permission_id}"

    assert world.post(path_of(lead, jane), form(**RECORD), token=None).status == 403
    for path, fields in [
        (path_of(lead, jane), {key: value for key, value in RECORD.items() if key != "evidence"}),
        (path_of(lead, jane), {**RECORD, "note": "x"}),
        (f"{base}/inbound", {}),
        (f"{base}/revoke", {"reason": "x"}),
        (f"{base}/do-not-contact", {"channel": "instagram"}),
    ]:
        reply = world.post(path, form(csrf_token=world.token, **fields))
        assert reply.status == 400, path
        assert reply.text == "Request refused: malformed form"
    repeated = form(csrf_token=world.token, **RECORD) + b"&evidence=again"
    assert world.post(path_of(lead, jane), repeated).status == 400
    assert permissions_count(world) == 1
    assert world.sql("SELECT status FROM channel_permissions") == [("active",)]


def test_an_erased_contact_shows_no_form_and_runs_nothing(world: World) -> None:
    lead, jane, permission_id = recorded(world)
    world.erase(erase_path(lead, jane))

    page = world.page(path_of(lead, jane))
    assert "This Contact was erased: it has no Channel Permissions to show." in page.text
    assert page.find("section", id="permissions-panel").find_all("form") == []
    assert HANDLE not in page.text and NAME not in page.text
    panel = submit(world, path_of(lead, jane), **RECORD)
    assert notice(panel) == ("status", "This Contact was erased; nothing changed.")
    panel = submit(world, f"{path_of(lead, jane)}/{permission_id}/do-not-contact")
    assert notice(panel) == ("status", "This Contact was erased; nothing changed.")
    assert permissions_count(world) == 1


@pytest.mark.parametrize("case", ["lead", "contact", "other lead", "not ulid"])
def test_a_contact_not_on_the_leads_survivor_is_404(world: World, case: str) -> None:
    zone(world)
    lead, jane, _ = jane_and_bob(world)
    other = world.lead("Jones Law", "jones.co.uk")
    path = {
        "lead": path_of(UNKNOWN, jane),
        "contact": path_of(lead, UNKNOWN),
        "other lead": path_of(other, jane),
        "not ulid": path_of(lead, "nope"),
    }[case]

    for reply in (
        world.get(path),
        world.post(path, form(csrf_token=world.token, **RECORD)),
        world.post(f"{path}/{UNKNOWN}/revoke", form(csrf_token=world.token)),
    ):
        assert reply.status == 404
        assert Tree(reply.text).find("h1").text == "Lead not found"
        assert UNKNOWN not in reply.text and jane not in reply.text
    assert permissions_count(world) == 0


def test_an_unknown_permission_is_refused(world: World) -> None:
    lead, jane, _ = recorded(world)

    panel = submit(world, f"{path_of(lead, jane)}/{UNKNOWN}/revoke")

    assert notice(panel) == ("alert", "Nothing was changed. This permission no longer exists.")


# The routes and the view module -----------------------------------------------------------------


def test_every_route_declares_personal_data_and_comes_before_the_lead_page(
    world: World,
) -> None:
    names = (
        "contact_permissions_page",
        "contact_permission_record",
        "contact_permission_inbound",
        "contact_permission_revoke",
        "contact_permission_do_not_contact",
        "lead_page",
    )
    routes = [route for route in world.dashboard.app.routes if getattr(route, "name", None) in names]

    assert [getattr(r, "name") for r in routes] == list(names)
    assert all(declared_personal_data(getattr(r, "endpoint")) is True for r in routes)


def test_the_page_is_locked_without_a_session(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)

    reply = world.dashboard.get(path_of(lead, jane))

    assert reply.status == 401
    assert NAME not in reply.text


def test_the_view_module_reads_only_through_queries() -> None:
    tree = ast.parse((PACKAGE / "web" / "permissions_page.py").read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]

    app_modules = [name for name in modules if name.startswith("nextron.app")]
    assert app_modules  # not vacuous
    assert all(
        name.startswith("nextron.app.queries.")
        or name == "nextron.app.pipeline.channel_permissions"  # its types only
        for name in app_modules
    ), app_modules
    assert not [name for name in modules if name.startswith("nextron.db")]


@pytest.mark.parametrize(
    ("written", "existing", "revoked", "text"),
    [
        (
            2,
            1,
            ("a", "b"),
            "Opted out: 2 suppression entries written, 1 entry already there, 2 permissions "
            "revoked. Every channel is blocked for this person from now on.",
        ),
        (
            1,
            0,
            ("a",),
            "Opted out: 1 suppression entry written, 0 entries already there, 1 permission "
            "revoked. Every channel is blocked for this person from now on.",
        ),
        (
            0,
            0,
            ("a",),
            "Opted out: 1 permission revoked. No suppression key is known for this person, so "
            "nothing was added to the Suppression List.",
        ),
    ],
)
def test_the_opt_out_notice_counts_every_entry_in_words(
    written: int, existing: int, revoked: tuple[str, ...], text: str
) -> None:
    assert opted_out(OptedOut(written, existing, revoked)).text == text
