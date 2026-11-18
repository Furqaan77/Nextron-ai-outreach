"""Story 10.7: Channel Permissions for WhatsApp and Instagram (``nextron.app.pipeline
.channel_permissions``, ``Research.record_operator_quote``, pipeline's erasure hook and export)
on a temporary database at head with the real subscriber registry and ``FrozenClock``: every row
of the spec's I/O matrix. A refusal stores nothing (no permission, no Evidence, no event); every
write appends ``events`` rows with no personal data; the table's CHECKs and partial unique index
back the domain rules."""

import json
import sqlite3
from datetime import timedelta
from typing import Any

import pytest

from nextron.app.compliance.erasure import Erasures
from nextron.app.pipeline.channel_permissions import (
    TIMEZONE_UNSET,
    UNKNOWN_CONTACT,
    ChannelPermissions,
    PermissionsView,
)
from nextron.app.setup.api import Setup
from nextron.domain import channel_permission as cp
from nextron.domain.channel_permission import PermissionRefused
from nextron.domain.contactability import Allowed, Blocked, BlockReason, ContactSubject
from tests.app.test_contactable import LATER, T0, World, world  # noqa: F401 - the fixture

ZONE = "Europe/London"  # UTC in November
EVIDENCE = "Jane messaged our Instagram account first, asking about the AI intake demo. " * 2
HANDLE = "jane.doe"
NUMBER = "+447700900123"
PLANTED = ("jane", "Jane", "doe", "7700900123", "Instagram account")
TABLES = ("channel_permissions", "evidence", "events")


def service(world: World) -> ChannelPermissions:
    return ChannelPermissions(world.clock, world.research, world.compliance)


def zone(world: World, name: str = ZONE) -> None:
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": name})


def contact(world: World, tag: str = "a", **fields: Any) -> str:
    zone(world)
    lead_id = world.lead(f"firm-{tag}.co.uk")
    return world.contact(lead_id, name="Jane Doe", email=f"jane@firm-{tag}.co.uk", **fields)


def record(world: World, contact_id: str, **overrides: str) -> str:
    fields = {
        "channel": "instagram",
        "action": "started_instagram_conversation",
        "occurred_at": "2026-11-18T09:00",  # an hour before LATER
        "evidence": EVIDENCE[:120],
        "number": "",
        "handle": f"@{HANDLE}",
    }
    fields.update(overrides)
    with world.units.open():
        return service(world).record(
            contact_id,
            fields["channel"],
            fields["action"],
            fields["occurred_at"],
            fields["evidence"],
            fields["number"],
            fields["handle"],
        )


def whatsapp(world: World, contact_id: str) -> str:
    return record(
        world,
        contact_id,
        channel="whatsapp",
        action="started_whatsapp_conversation",
        number=NUMBER,
        handle="",
    )


def counts(world: World) -> dict[str, int]:
    return {table: world.sql(f"SELECT COUNT(*) FROM {table}")[0][0] for table in TABLES}


def refused(world: World, call: Any) -> dict[str, str]:
    before = counts(world)
    with pytest.raises(PermissionRefused) as caught:
        with world.units.open():
            call(service(world))
    assert counts(world) == before  # nothing stored
    return caught.value.problems


def row(world: World, permission_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(world.db_file)
    connection.row_factory = sqlite3.Row
    try:
        found = connection.execute(
            "SELECT * FROM channel_permissions WHERE id = ?", (permission_id,)
        ).fetchone()
    finally:
        connection.close()
    return dict(found)


def events(world: World, permission_id: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'channel_permissions' AND entity_id = ? ORDER BY rowid",
        (permission_id,),
    )


def view(world: World, contact_id: str) -> PermissionsView:
    return world.read(lambda: service(world).view(contact_id))


def no_personal_data_in_events(world: World) -> None:
    for (text,) in world.sql("SELECT coalesce(cause_ref, '') || cause || actor FROM events"):
        assert not any(planted in text for planted in PLANTED)


# Record -----------------------------------------------------------------------------------------


def test_recording_instagram_stores_the_permission_its_evidence_and_one_event(
    world: World,
) -> None:
    contact_id = contact(world)

    permission_id = record(world, contact_id)

    stored = row(world, permission_id)
    assert {key: stored[key] for key in stored if key not in ("id", "evidence_id")} == {
        "contact_id": contact_id,
        "channel": "instagram",
        "prospect_action": "started_instagram_conversation",
        "occurred_at": "2026-11-18T09:00:00.000000Z",
        "whatsapp_number": None,
        "instagram_handle": HANDLE,
        "last_inbound_at": "2026-11-18T09:00:00.000000Z",
        "status": "active",
        "revoked_at": None,
        "recorded_by": "operator",
        "created_at": "2026-11-18T10:00:00.000000Z",
        "updated_at": "2026-11-18T10:00:00.000000Z",
    }
    [evidence] = world.sql(
        "SELECT type, quote, source, extracted_by, person_contact_id, source_url, pages_checked "
        "FROM evidence WHERE id = ?",
        (stored["evidence_id"],),
    )
    assert evidence == (
        "quote",
        EVIDENCE[:120].strip(),
        "operator",
        "operator",
        contact_id,
        None,
        "[]",
    )
    assert events(world, permission_id) == [
        (None, "active", "permission_recorded", stored["evidence_id"], "operator")
    ]
    no_personal_data_in_events(world)
    shown = view(world, contact_id)
    assert [found.id for found in shown.permissions] == [permission_id]
    assert str(shown.zone) == ZONE and shown.now == LATER
    last = shown.permissions[0].last_inbound_at
    assert last is not None
    assert cp.window_text(last, shown.now, shown.zone) == (
        "24-hour window closes at 2026-11-19 09:00 (Europe/London)"
    )


def test_recording_whatsapp_keeps_no_inbound_time(world: World) -> None:
    permission_id = whatsapp(world, contact(world))

    stored = row(world, permission_id)
    assert (stored["whatsapp_number"], stored["instagram_handle"]) == (NUMBER, None)
    assert stored["last_inbound_at"] is None


@pytest.mark.parametrize(
    ("overrides", "problems"),
    [
        ({"action": "gave_number_for_whatsapp"}, {"prospect_action": cp.BAD_ACTION}),
        ({"occurred_at": "2026-11-18T10:01"}, {"occurred_at": cp.FUTURE_TIME}),
        ({"evidence": "x" * 301}, {"evidence": cp.LONG_EVIDENCE}),
        ({"evidence": "  "}, {"evidence": cp.BLANK_EVIDENCE}),
        ({"handle": ""}, {"instagram_handle": cp.BAD_HANDLE}),
    ],
)
def test_a_bad_field_is_refused_and_stores_nothing(
    world: World, overrides: dict[str, str], problems: dict[str, str]
) -> None:
    contact_id = contact(world)

    assert refused(world, lambda s: record_with(s, contact_id, **overrides)) == problems


def record_with(target: ChannelPermissions, contact_id: str, **overrides: str) -> str:
    fields = {
        "channel": "instagram",
        "action": "started_instagram_conversation",
        "occurred_at": "2026-11-18T09:00",
        "evidence": "They messaged us first",
        "number": "",
        "handle": HANDLE,
    }
    fields.update(overrides)
    return target.record(
        contact_id,
        fields["channel"],
        fields["action"],
        fields["occurred_at"],
        fields["evidence"],
        fields["number"],
        fields["handle"],
    )


def test_a_blocked_contact_is_refused_with_the_reason_and_nothing_is_stored(
    world: World,
) -> None:
    contact_id = contact(world)
    world.entry("address", "jane@firm-a.co.uk")

    problems = refused(world, lambda s: record_with(s, contact_id))

    assert problems == {
        "form": "Nothing was recorded: this Contact can't be contacted (the email address is on "
        "the Suppression List)"
    }


def test_an_unknown_contact_or_an_unset_timezone_is_refused(world: World) -> None:
    contact_id = contact(world)
    assert refused(world, lambda s: record_with(s, "01JD3QZ8X5V9K2M7N4P6R8T0AB")) == {
        "form": UNKNOWN_CONTACT
    }
    world.sql("DELETE FROM settings WHERE key = 'sender_timezone'")

    assert refused(world, lambda s: record_with(s, contact_id)) == {"form": TIMEZONE_UNSET}
    assert world.read(lambda: service(world).view(contact_id)).zone is None


def test_a_second_active_permission_on_the_channel_is_refused(world: World) -> None:
    contact_id = contact(world)
    record(world, contact_id)
    whatsapp(world, contact_id)  # another channel is fine

    assert refused(world, lambda s: record_with(s, contact_id, handle="other.handle")) == {
        "form": "This Contact already has an active Instagram permission"
    }


# Last inbound message ---------------------------------------------------------------------------


def test_updating_the_last_inbound_message_moves_the_window(world: World) -> None:
    contact_id = contact(world)
    permission_id = record(world, contact_id)

    with world.units.open():
        service(world).update_inbound(contact_id, permission_id, "2026-11-18T09:45")

    stored = row(world, permission_id)
    assert stored["last_inbound_at"] == "2026-11-18T09:45:00.000000Z"
    assert events(world, permission_id)[-1] == (
        "active",
        "active",
        "inbound_updated",
        None,
        "operator",
    )
    shown = view(world, contact_id)
    last = shown.permissions[0].last_inbound_at
    assert last is not None and shown.zone is not None
    assert cp.window_text(last, shown.now, shown.zone).endswith("2026-11-19 09:45 (Europe/London)")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("2026-11-18T08:00", cp.NOT_LATER),
        ("2026-11-18T09:00", cp.NOT_LATER),
        ("2026-11-18T10:30", cp.FUTURE_TIME),
    ],
)
def test_an_earlier_or_future_inbound_time_is_refused(
    world: World, text: str, message: str
) -> None:
    contact_id = contact(world)
    permission_id = record(world, contact_id)

    assert refused(
        world, lambda s: s.update_inbound(contact_id, permission_id, text)
    ) == {"last_inbound_at": message}


def test_only_an_active_instagram_permission_of_the_contact_has_an_inbound_time(
    world: World,
) -> None:
    contact_id = contact(world)
    other = contact(world, "b")
    on_whatsapp = whatsapp(world, contact_id)
    on_instagram = record(world, contact_id)

    assert refused(
        world, lambda s: s.update_inbound(contact_id, on_whatsapp, "2026-11-18T09:30")
    ) == {"form": cp.NO_WINDOW}
    assert refused(
        world, lambda s: s.update_inbound(other, on_instagram, "2026-11-18T09:30")
    ) == {"form": cp.UNKNOWN_PERMISSION}
    assert refused(world, lambda s: s.update_inbound(contact_id, "nope", "2026-11-18T09:30")) == {
        "form": cp.UNKNOWN_PERMISSION
    }
    with world.units.open():
        service(world).revoke(contact_id, on_instagram)
    assert refused(
        world, lambda s: s.update_inbound(contact_id, on_instagram, "2026-11-18T09:30")
    ) == {"form": cp.NOT_ACTIVE}


def test_updating_the_inbound_time_of_a_blocked_contact_is_refused(world: World) -> None:
    contact_id = contact(world)
    permission_id = record(world, contact_id)
    world.entry("address", "jane@firm-a.co.uk")

    problems = refused(world, lambda s: s.update_inbound(contact_id, permission_id, "2026-11-18T09:30"))

    assert problems["form"].startswith("Nothing was recorded: this Contact can't be contacted")


def test_the_window_is_closed_25_hours_later(world: World) -> None:
    contact_id = contact(world)
    record(world, contact_id)
    world.clock.set(LATER + timedelta(hours=24))  # 25 h after the message

    shown = view(world, contact_id)
    last = shown.permissions[0].last_inbound_at
    assert last is not None and shown.zone is not None
    assert cp.window_text(last, shown.now, shown.zone) == (
        "24-hour window closed at 2026-11-19 09:00 (Europe/London): don't send"
    )


# Revoke and opt out -----------------------------------------------------------------------------


def test_revoking_is_never_gated_and_happens_once(world: World) -> None:
    contact_id = contact(world)
    permission_id = record(world, contact_id)
    world.entry("address", "jane@firm-a.co.uk")  # revoke still works for a blocked Contact

    with world.units.open():
        service(world).revoke(contact_id, permission_id)

    stored = row(world, permission_id)
    assert (stored["status"], stored["revoked_at"]) == ("revoked", "2026-11-18T10:00:00.000000Z")
    assert events(world, permission_id)[-1] == (
        "active",
        "revoked",
        "permission_revoked",
        None,
        "operator",
    )
    assert refused(world, lambda s: s.revoke(contact_id, permission_id)) == {
        "form": cp.ALREADY_REVOKED
    }


def test_opting_out_suppresses_the_person_and_revokes_every_active_permission(
    world: World,
) -> None:
    contact_id = contact(world)
    on_instagram = record(world, contact_id)
    on_whatsapp = whatsapp(world, contact_id)
    with world.units.open():
        service(world).revoke(contact_id, on_whatsapp)
    again = whatsapp(world, contact_id)

    with world.units.open():
        result = service(world).opt_out(contact_id, on_instagram)

    assert result.written > 0 and result.existing == 0
    assert set(result.revoked) == {on_instagram, again}
    entries = world.sql(
        "SELECT id, source, cause_ref FROM suppression_entries WHERE status = 'active'"
    )
    assert entries and {(source, ref) for _, source, ref in entries} == {
        ("operator", on_instagram)
    }
    first_entry = world.sql(
        "SELECT id FROM suppression_entries WHERE cause_ref = ? ORDER BY rowid LIMIT 1",
        (on_instagram,),
    )[0][0]
    for permission_id in (on_instagram, again):
        assert row(world, permission_id)["status"] == "revoked"
        assert events(world, permission_id)[-1] == (
            "active",
            "revoked",
            "opted_out",
            first_entry,
            "operator",
        )
    assert len(events(world, on_whatsapp)) == 2  # its revoke only: it was already revoked
    gate = world.gate(ContactSubject(contact_id), "whatsapp", "follow_up")
    assert isinstance(gate, Blocked)
    assert BlockReason.ADDRESS_SUPPRESSED in gate.reasons
    no_personal_data_in_events(world)


def test_opting_out_works_from_a_revoked_permission_and_is_never_gated(world: World) -> None:
    contact_id = contact(world)
    permission_id = record(world, contact_id)
    with world.units.open():
        service(world).revoke(contact_id, permission_id)

    with world.units.open():
        result = service(world).opt_out(contact_id, permission_id)

    assert result.revoked == ()
    with world.units.open():  # a second opt-out finds every key already active
        again = service(world).opt_out(contact_id, permission_id)
    assert (again.written, again.existing > 0) == (0, True)


def test_opting_out_a_person_with_no_known_key_still_revokes_every_permission(
    world: World,
) -> None:
    zone(world)
    lead_id = world.lead("firm-nokey.co.uk")
    contact_id = world.contact(lead_id, name="Jane Doe")  # no email address, no profile URL
    permission_id = record(world, contact_id)
    # a stored name with no letters or digits makes no person key
    world.sql("UPDATE contacts SET name = '...' WHERE id = ?", (contact_id,))

    with world.units.open():
        result = service(world).opt_out(contact_id, permission_id)

    assert (result.written, result.existing, result.revoked) == (0, 0, (permission_id,))
    assert world.sql("SELECT COUNT(*) FROM suppression_entries") == [(0,)]
    assert row(world, permission_id)["status"] == "revoked"
    assert events(world, permission_id)[-1] == ("active", "revoked", "opted_out", None, "operator")


def test_an_unparseable_stored_timezone_reads_as_unset(world: World) -> None:
    contact_id = contact(world)
    world.sql("UPDATE settings SET value = 'Mars/Olympus' WHERE key = 'sender_timezone'")
    assert world.sql("SELECT value FROM settings WHERE key = 'sender_timezone'") == [
        ("Mars/Olympus",)
    ]

    assert view(world, contact_id).zone is None
    assert refused(world, lambda s: record_with(s, contact_id)) == {"form": TIMEZONE_UNSET}


# Erasure and export -----------------------------------------------------------------------------


def test_erasing_the_contact_revokes_and_clears_its_permissions(world: World) -> None:
    contact_id = contact(world)
    on_instagram = record(world, contact_id)
    on_whatsapp = whatsapp(world, contact_id)
    with world.units.open():
        service(world).revoke(contact_id, on_whatsapp)
    evidence_ids = [row(world, found)["evidence_id"] for found in (on_instagram, on_whatsapp)]

    with world.units.open():
        Erasures(world.clock, world.compliance).erase_on_request(contact_id, "TICKET-42")

    for permission_id in (on_instagram, on_whatsapp):
        stored = row(world, permission_id)
        assert (stored["whatsapp_number"], stored["instagram_handle"]) == (None, None)
        assert stored["status"] == "revoked"
    assert events(world, on_instagram)[-1] == (
        "active",
        "revoked",
        "contact_erased",
        None,
        "system:pipeline",
    )
    assert len(events(world, on_whatsapp)) == 2  # already revoked: no erase event
    for evidence_id in evidence_ids:
        assert world.sql("SELECT quote FROM evidence WHERE id = ?", (evidence_id,)) == [
            ("[erased]",)
        ]
    assert world.read(lambda: service(world).view(contact_id)).permissions[0].status == "revoked"
    assert refused(world, lambda s: record_with(s, contact_id)) == {"form": UNKNOWN_CONTACT}


def test_the_export_holds_every_permission_of_the_contact(world: World) -> None:
    contact_id = contact(world)
    permission_id = record(world, contact_id)
    other = contact(world, "b")
    record(world, other)

    found = world.read(lambda: world.compliance.export_contact(contact_id))

    document = json.loads(found.body)
    [exported] = document["pipeline"]["channel_permissions"]
    assert exported["id"] == permission_id
    assert exported["instagram_handle"] == HANDLE
    assert exported["prospect_action"] == "started_instagram_conversation"
    quotes = [item["quote"] for item in document["research"]["evidence"]]
    assert EVIDENCE[:120].strip() in quotes


# The table backs the rules ----------------------------------------------------------------------


def _insert(world: World, contact_id: str, evidence_id: str, **overrides: object) -> None:
    values: dict[str, object] = {
        "id": "01JD3QZ8X5V9K2M7N4P6R8T0AA",
        "contact_id": contact_id,
        "channel": "instagram",
        "prospect_action": "started_instagram_conversation",
        "occurred_at": "2026-11-18T09:00:00.000000Z",
        "evidence_id": evidence_id,
        "whatsapp_number": None,
        "instagram_handle": HANDLE,
        "last_inbound_at": "2026-11-18T09:00:00.000000Z",
        "status": "active",
        "revoked_at": None,
        "recorded_by": "operator",
        "created_at": "2026-11-18T10:00:00.000000Z",
        "updated_at": "2026-11-18T10:00:00.000000Z",
    }
    values.update(overrides)
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    world.sql(f"INSERT INTO channel_permissions ({names}) VALUES ({marks})", tuple(values.values()))


@pytest.mark.parametrize(
    "overrides",
    [
        {"channel": "linkedin"},
        {"prospect_action": "gave_number_for_whatsapp"},
        {"whatsapp_number": NUMBER},
        {"instagram_handle": None},
        {"last_inbound_at": None},
        {"last_inbound_at": "2026-11-18T08:00:00.000000Z"},
        {"status": "paused"},
        {"status": "revoked"},
        {"revoked_at": "2026-11-18T10:00:00.000000Z"},
        {"recorded_by": "system:pipeline"},
        {
            "channel": "whatsapp",
            "prospect_action": "started_whatsapp_conversation",
            "instagram_handle": None,
            "last_inbound_at": None,
        },
    ],
)
def test_the_checks_refuse_a_row_the_rules_forbid(
    world: World, overrides: dict[str, object]
) -> None:
    contact_id = contact(world)
    first = record(world, contact_id)
    evidence_id = row(world, first)["evidence_id"]
    with world.units.open():
        service(world).revoke(contact_id, first)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _insert(world, contact_id, evidence_id, **overrides)
    _insert(world, contact_id, evidence_id)  # the base row itself is fine


def test_the_partial_index_allows_one_active_permission_per_channel(world: World) -> None:
    contact_id = contact(world)
    evidence_id = row(world, record(world, contact_id))["evidence_id"]

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert(world, contact_id, evidence_id)
    _insert(
        world,
        contact_id,
        evidence_id,
        instagram_handle=None,
        status="revoked",
        revoked_at="2026-11-18T10:00:00.000000Z",
    )


# Research's operator Quote ----------------------------------------------------------------------


def test_record_operator_quote_refuses_bad_arguments_and_unknown_contacts(world: World) -> None:
    contact_id = contact(world)
    for quote in (" padded ", "", "x" * 301):
        with pytest.raises(ValueError, match="1 to 300 stripped characters"):
            world.transact(lambda q=quote: world.research.record_operator_quote(contact_id, q, T0))
    with pytest.raises(TypeError):
        world.transact(
            lambda: world.research.record_operator_quote(
                contact_id, "ok", T0.replace(tzinfo=None)
            )
        )
    with pytest.raises(LookupError):
        world.transact(
            lambda: world.research.record_operator_quote("01JD3QZ8X5V9K2M7N4P6R8T0AB", "ok", T0)
        )
    assert counts(world)["evidence"] == 0


def test_the_gate_allows_a_clean_contact_on_both_channels(world: World) -> None:
    contact_id = contact(world)
    for channel in ("whatsapp", "instagram"):
        assert world.gate(ContactSubject(contact_id), channel, "follow_up") == Allowed()
