"""The SendingInbox machine and the registration rules (``nextron.domain.sending_inbox``, AD-4,
AD-14, FR-32, Story 4.7): every (status, event) pair is its table row or raises; the exact
statuses, pause causes, ``PAUSE_EVENTS``, ``CREATION``, ``BINDABLE_STATUSES`` and
``TERMINAL_STATUSES``; every refusal row of the I/O matrix at domain level, with messages that
never hold the input; the role list with a ``+tag``; ``allowlist_refusal`` in prod, dev and
test; ``crowded_domains``; the Sender's name ``display_name`` rule; and Story 6.4's
``check_warmup``: every field's range edges, blank, non-integer, today and tomorrow, the blank
record, and the first refusal in form order."""

import itertools
from datetime import date
from types import MappingProxyType

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.sending_inbox import (
    BINDABLE_STATUSES,
    BRAND_DOMAIN,
    CONNECTING_NOTE,
    CREATION,
    MACHINE,
    MAX_INBOXES_PER_DOMAIN,
    MAX_LABEL_LENGTH,
    MAX_METHOD_LENGTH,
    PAUSE_EVENTS,
    REFUSAL_MESSAGES,
    ROLE_LOCAL_PARTS,
    TERMINAL_STATUSES,
    TRANSITIONS,
    WARMUP_MESSAGES,
    InboxAddress,
    InboxCause,
    InboxEvent,
    InboxRefused,
    InboxStatus,
    PauseCause,
    Refusal,
    WarmupRecord,
    WarmupRefusal,
    WarmupRefused,
    allowlist_refusal,
    check_address,
    check_label,
    check_warmup,
    crowded_domains,
    crowding_warning,
    transition,
)
from nextron.domain.settings_schema import (
    DISPLAY_NAME_RULE,
    InvalidSettingValue,
    Key,
    Section,
    SettingType,
    is_display_name,
    parse,
    setting,
)

STATUSES = ["connecting", "active", "paused", "auth_failed", "removed"]
CAUSES = ["operator", "bounce_rate", "policy_bounce", "blocklist", "spam_rate"]

# The spec's table, written out by hand: event -> (from statuses, to, cause).
TABLE: dict[str, tuple[list[str], str, str]] = {
    "connect": (["connecting"], "active", "connected"),
    **{f"pause_{cause}": (["active"], "paused", cause) for cause in CAUSES},
    "resume": (["paused"], "active", "resumed"),
    "auth_fail": (["active", "paused"], "auth_failed", "invalid_grant"),
    "reconsent": (["auth_failed"], "active", "reconsented"),
    "remove": (["connecting", "active", "paused", "auth_failed"], "removed", "operator_removed"),
}
EXPECTED = {
    (status, event): (to, cause)
    for event, (sources, to, cause) in TABLE.items()
    for status in sources
}


# The machine ----------------------------------------------------------------------------------


def test_the_statuses_are_the_ad4_list_in_order() -> None:
    assert [str(status) for status in InboxStatus] == STATUSES


def test_the_pause_causes_are_the_ad4_list() -> None:
    assert [str(cause) for cause in PauseCause] == CAUSES


def test_the_events_are_connect_one_pause_per_cause_and_the_rest() -> None:
    assert [str(event) for event in InboxEvent] == [
        "connect",
        *(f"pause_{cause}" for cause in CAUSES),
        "resume",
        "auth_fail",
        "reconsent",
        "remove",
    ]


def test_the_causes_are_closed() -> None:
    assert [str(cause) for cause in InboxCause] == [
        "registered",
        "connected",
        *CAUSES,
        "resumed",
        "invalid_grant",
        "reconsented",
        "operator_removed",
    ]


def test_pause_events_maps_each_pause_cause_to_its_event() -> None:
    assert isinstance(PAUSE_EVENTS, MappingProxyType)
    assert {str(cause): str(event) for cause, event in PAUSE_EVENTS.items()} == {
        cause: f"pause_{cause}" for cause in CAUSES
    }


def test_creation_terminal_and_bindable_statuses() -> None:
    assert MACHINE == "sending_inbox"
    assert CREATION == Transition(InboxStatus.CONNECTING, InboxCause.REGISTERED)
    assert TERMINAL_STATUSES == (InboxStatus.REMOVED,)
    assert BINDABLE_STATUSES == (InboxStatus.CONNECTING, InboxStatus.ACTIVE)


def test_the_table_is_read_only_and_exactly_the_spec() -> None:
    assert isinstance(TRANSITIONS, MappingProxyType)
    assert {
        (str(status), str(event)): (str(row.to), str(row.cause))
        for (status, event), row in TRANSITIONS.items()
    } == EXPECTED


@pytest.mark.parametrize(
    ("status", "event"), list(itertools.product(InboxStatus, InboxEvent))
)
def test_every_status_event_pair_is_its_row_or_raises(
    status: InboxStatus, event: InboxEvent
) -> None:
    expected = EXPECTED.get((str(status), str(event)))
    if expected is None:
        with pytest.raises(IllegalTransition) as caught:
            transition(status, event)
        assert caught.value.code == "illegal_transition"
        assert str(caught.value) == f"sending_inbox: {event} is not allowed from {status}"
        assert (caught.value.machine, caught.value.event, caught.value.status) == (
            "sending_inbox",
            str(event),
            str(status),
        )
    else:
        found = transition(status, event)
        assert (found.to, found.cause) == expected
        assert isinstance(found.to, InboxStatus) and isinstance(found.cause, InboxCause)


def test_removed_is_terminal() -> None:
    for event in InboxEvent:
        with pytest.raises(IllegalTransition):
            transition(InboxStatus.REMOVED, event)


@pytest.mark.parametrize("value", [None, ["connect"], "nope", 3])
def test_a_value_that_is_no_status_or_event_raises(value: object) -> None:
    with pytest.raises(IllegalTransition):
        transition(value, InboxEvent.CONNECT)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransition):
        transition(InboxStatus.ACTIVE, value)  # type: ignore[arg-type]


# check_address --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "address", "domain"),
    [
        ("Furqaan@Nextron-AI.com", "furqaan@nextron-ai.com", "nextron-ai.com"),
        ("  mac@nextron-ai.com ", "mac@nextron-ai.com", "nextron-ai.com"),
        ("jane.doe+x@mail.example.co.uk", "jane.doe+x@mail.example.co.uk", "example.co.uk"),
        ("mac@www.example.com", "mac@www.example.com", "example.com"),
        ("mac@b\u00fccher.de", "mac@xn--bcher-kva.de", "xn--bcher-kva.de"),  # IDN as punycode
        ("information@d.com", "information@d.com", "d.com"),  # not exactly a role
        ("teams@d.com", "teams@d.com", "d.com"),
        ("mac@nextronai.co", "mac@nextronai.co", "nextronai.co"),
        ("mac@notnextronai.com", "mac@notnextronai.com", "notnextronai.com"),
    ],
)
def test_check_address_normalises_and_finds_the_sending_domain(
    text: str, address: str, domain: str
) -> None:
    assert check_address(text) == InboxAddress(address, domain)


@pytest.mark.parametrize(
    "text",
    [
        "x@nextronai.com",
        "x@mail.NextronAI.com",
        "team@nextronai.com",  # brand before role
        "info+cold@NEXTRONAI.COM",
        "x@a.b.nextronai.com",
        "x@www.nextronai.com",
    ],
)
def test_the_brand_domain_and_its_subdomains_are_refused(text: str) -> None:
    refused = refusal_of(check_address, text)

    assert refused.refusal is Refusal.BRAND_DOMAIN
    assert str(refused) == "Cold email never goes from nextronai.com"


@pytest.mark.parametrize("role", sorted(ROLE_LOCAL_PARTS))
def test_every_role_local_part_is_refused_with_or_without_a_tag(role: str) -> None:
    for text in (f"{role}@d.com", f"{role.upper()}@d.com", f"{role}+cold@d.com", f"{role}+@d.com"):
        refused = refusal_of(check_address, text)
        assert refused.refusal is Refusal.ROLE_ADDRESS
        assert str(refused) == "Use a personal address in the Sender's name"


def test_the_role_list_is_the_ac_eight_plus_the_common_role_mailboxes() -> None:
    assert ROLE_LOCAL_PARTS == {
        "team",
        "info",
        "sales",
        "contact",
        "admin",
        "office",
        "hello",
        "support",
        "abuse",
        "billing",
        "careers",
        "enquiries",
        "help",
        "hostmaster",
        "inquiries",
        "jobs",
        "marketing",
        "no-reply",
        "noreply",
        "postmaster",
        "press",
        "webmaster",
    }


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ("info@d.com", Refusal.ROLE_ADDRESS),
        ("Sales@d.com", Refusal.ROLE_ADDRESS),
        ("info+cold@d.com", Refusal.ROLE_ADDRESS),
        ("mac+info@d.com", None),  # only the text before the first + counts
    ],
)
def test_the_role_check_reads_the_text_before_the_first_plus(
    text: str, refusal: Refusal | None
) -> None:
    if refusal is None:
        assert check_address(text).address == text
    else:
        assert refusal_of(check_address, text).refusal is refusal


@pytest.mark.parametrize(
    "text",
    [
        "not-an-email",
        "",
        "   ",
        "a@co.uk",  # a public suffix, not a registrable domain
        "\u00fc@d.com",  # a non-ASCII local part
        "a@b@d.com",
        "a b@d.com",
        "a@localhost",
        "a@d.local",
        "a@192.168.0.1",
        "@d.com",
        "a\u200b@d.com",
        "x" * 65 + "@d.com",
        "a@" + "x" * 3000 + ".com",
        # the local part must be an RFC 5322 dot-atom
        "a,b@d.com",
        '"mac"@d.com',
        "mac<x>@d.com",
        "a..b@d.com",
        ".mac@d.com",
        "mac.@d.com",
        '"info"@d.com',  # no role-check bypass by quoting
        "info(x)@d.com",  # or by a comment
        "mac;x@d.com",
        "mac[x]@d.com",
    ],
)
def test_an_invalid_address_is_refused_without_echoing_it(text: str) -> None:
    refused = refusal_of(check_address, text)

    assert refused.refusal is Refusal.INVALID_ADDRESS
    assert str(refused) == "Enter a valid email address, such as furqaan@yourdomain.com"
    assert refused.__cause__ is None
    assert refused.__context__ is None or refused.__suppress_context__  # no input in a chain


@pytest.mark.parametrize("value", [None, 1, b"a@d.com", ["a@d.com"]])
def test_check_address_takes_a_str(value: object) -> None:
    with pytest.raises(TypeError, match="check_address takes a str"):
        check_address(value)  # type: ignore[arg-type]


# check_label ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Main", "Main"),
        ("  Main inbox  ", "Main inbox"),
        ("x" * MAX_LABEL_LENGTH, "x" * MAX_LABEL_LENGTH),
        ("Caf\u00e9 \u2014 primary", "Caf\u00e9 \u2014 primary"),
        ("<script>alert(1)</script>", "<script>alert(1)</script>"),  # escaped when shown
    ],
)
def test_check_label_strips_and_keeps_a_one_line_label(text: str, label: str) -> None:
    assert check_label(text) == label


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "x" * (MAX_LABEL_LENGTH + 1),
        "Main\ninbox",
        "Main\rinbox",
        "Main\tinbox",
        "Main\u2028inbox",  # line separator
        "Main\u2029inbox",  # paragraph separator
        "Main\u200binbox",  # zero-width space (format)
        "Main\u202einbox",  # bidi override (format)
        "Main\ud800inbox",  # a lone surrogate
        "Main\x00inbox",
    ],
)
def test_a_label_off_one_line_or_of_the_wrong_length_is_refused(text: str) -> None:
    refused = refusal_of(check_label, text)

    assert refused.refusal is Refusal.INVALID_LABEL
    assert str(refused) == "Enter a label of 1 to 80 characters on one line"


@pytest.mark.parametrize("value", [None, 1, b"Main"])
def test_check_label_takes_a_str(value: object) -> None:
    with pytest.raises(TypeError, match="check_label takes a str"):
        check_label(value)  # type: ignore[arg-type]


# allowlist_refusal ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "allowlist", "refusal"),
    [
        ("prod", (), None),  # prod has no allowlist
        ("prod", ("other@d.com",), None),
        ("dev", (), Refusal.NOT_ALLOWLISTED),
        ("test", (), Refusal.NOT_ALLOWLISTED),
        ("dev", ("other@d.com",), Refusal.NOT_ALLOWLISTED),
        ("dev", ("mac@d.com",), None),
        ("test", ("MAC@D.COM",), None),  # any ASCII case
        ("test", ("-1001234567890", "Mac@d.com"), None),
        ("test", "mac@d.com", Refusal.NOT_ALLOWLISTED),  # a bare str is no allowlist
        ("Prod", ("other@d.com",), Refusal.NOT_ALLOWLISTED),  # only exactly prod is prod
        ("", (), Refusal.NOT_ALLOWLISTED),
    ],
)
def test_allowlist_refusal_applies_outside_prod_only(
    env: str, allowlist: tuple[str, ...], refusal: Refusal | None
) -> None:
    assert allowlist_refusal("mac@d.com", env, allowlist) is refusal


def test_allowlist_refusal_takes_a_str_address() -> None:
    with pytest.raises(TypeError):
        allowlist_refusal(None, "test", ())  # type: ignore[arg-type]


# crowded_domains ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domains", "crowded"),
    [
        ([], ()),
        (["a.com"] * MAX_INBOXES_PER_DOMAIN, ()),
        (["a.com"] * (MAX_INBOXES_PER_DOMAIN + 1), ("a.com",)),
        (["b.com"] * 5 + ["a.com"] * 4 + ["c.com"] * 3, ("a.com", "b.com")),
        (iter(["z.com"] * 4), ("z.com",)),
    ],
)
def test_crowded_domains_are_those_over_three_sorted(
    domains: list[str], crowded: tuple[str, ...]
) -> None:
    assert MAX_INBOXES_PER_DOMAIN == 3
    assert crowded_domains(domains) == crowded


def test_the_crowding_warning_and_connecting_note() -> None:
    assert crowding_warning("nextron-ai.com") == (
        "More than 3 inboxes on nextron-ai.com raises deliverability risk"
    )
    assert CONNECTING_NOTE == (
        "Gmail connection and warm-up not set up yet; nothing can be sent from it"
    )


# Refusals -------------------------------------------------------------------------------------


def test_every_refusal_has_its_exact_message() -> None:
    assert {str(refusal): message for refusal, message in REFUSAL_MESSAGES.items()} == {
        "invalid_address": "Enter a valid email address, such as furqaan@yourdomain.com",
        "invalid_label": "Enter a label of 1 to 80 characters on one line",
        "brand_domain": "Cold email never goes from nextronai.com",
        "role_address": "Use a personal address in the Sender's name",
        "not_allowlisted": "Outside prod, only allowlisted test inboxes can be registered",
        "already_registered": "Inbox already registered",
        "sender_name_unset": "Set the Sender's name on the Settings page first",
    }
    assert isinstance(REFUSAL_MESSAGES, MappingProxyType)


@pytest.mark.parametrize("refusal", list(Refusal))
def test_inbox_refused_is_a_domain_error_with_its_refusal(refusal: Refusal) -> None:
    error = InboxRefused(refusal)

    assert isinstance(error, DomainError)
    assert error.code == "inbox_refused"
    assert error.refusal is refusal
    assert str(error) == error.message == REFUSAL_MESSAGES[refusal]


def test_no_refusal_message_holds_the_input() -> None:
    planted = "planted.person"
    for function, text in (
        (check_address, f"{planted}@nextronai.com"),
        (check_address, f"info+{planted}@d.com"),
        (check_address, f"{planted}"),
        (check_label, f"{planted}\ninbox"),
        (check_label, planted * 20),
    ):
        refused = refusal_of(function, text)
        assert planted not in str(refused)
        assert all(planted not in str(arg) for arg in refused.args)
    assert BRAND_DOMAIN == "nextronai.com"


def refusal_of(function: object, text: str) -> InboxRefused:
    with pytest.raises(InboxRefused) as caught:
        function(text)  # type: ignore[operator]
    return caught.value


# The Sender's name setting --------------------------------------------------------------------


def test_sender_name_is_a_required_string_first_in_the_sender_section() -> None:
    found = setting(Key.SENDER_NAME)

    assert (found.key, found.type, found.section, found.label) == (
        "sender_name",
        SettingType.STRING,
        Section.SENDER,
        "Sender's name",
    )
    assert found.required
    assert [rule.name for rule in found.rules] == ["max_length", "display_name"]
    assert found.rules[1] is DISPLAY_NAME_RULE


@pytest.mark.parametrize(
    "text",
    [
        "Furqaan Ahmed",
        "Mac",
        "O'Brien",
        "O\u2019Brien",
        "Jean-Luc Picard",
        "J. R. R. Tolkien",
        "Zo\u00eb",
        "Zoe\u0308",  # a combining mark
        "\u0645\u062d\u0645\u062f",  # Arabic letters
        "x" * 100,
    ],
)
def test_the_display_name_rule_accepts_a_persons_name(text: str) -> None:
    assert is_display_name(text)
    assert parse(setting(Key.SENDER_NAME), text) == text


@pytest.mark.parametrize(
    "text",
    [
        "...",
        "- '",
        "\u0308",  # a combining mark alone
        "Mac 2",
        "Mac@Nextron",
        "Nextron AI <sales@nextronai.com>",
        "Mac, CEO",
        "Mac!",
        "Mac\u00a0Ahmed",  # a no-break space
        "Mac_Ahmed",
        "\U0001f600",
    ],
)
def test_the_display_name_rule_refuses_anything_else(text: str) -> None:
    assert not is_display_name(text)
    with pytest.raises(InvalidSettingValue) as caught:
        parse(setting(Key.SENDER_NAME), text)
    assert caught.value.rule == "display_name"
    assert text not in str(caught.value)


def test_the_sender_name_is_at_most_100_characters() -> None:
    with pytest.raises(InvalidSettingValue) as caught:
        parse(setting(Key.SENDER_NAME), "x" * 101)

    assert caught.value.rule == "max_length"


@pytest.mark.parametrize("value", [None, 1, b"Mac"])
def test_the_display_name_check_refuses_a_non_str(value: object) -> None:
    assert not is_display_name(value)


# Warm-up (Story 6.4) ----------------------------------------------------------------------------

TODAY = date(2026, 11, 18)
GOOD = {
    "started_on": "2026-11-01",
    "method": "manual",
    "warm_level": "12",
    "tool_daily": "0",
    "ramp_step": "5",
    "ramp_target": "30",
}


def warmup(**changes: str) -> WarmupRecord:
    return check_warmup(**{**GOOD, **changes}, today=TODAY)


def warmup_refusal(**changes: str) -> WarmupRefusal:
    with pytest.raises(WarmupRefused) as caught:
        warmup(**changes)
    assert caught.value.code == "warmup_refused"
    # a fixed message per refusal, so it never holds the value typed
    assert str(caught.value) == WARMUP_MESSAGES[caught.value.refusal]
    return caught.value.refusal


def test_a_valid_form_gives_the_typed_record() -> None:
    assert warmup() == WarmupRecord(date(2026, 11, 1), "manual", 12, 0, 5, 30)


def test_values_are_stripped_and_manual_is_stored_lowercase() -> None:
    record = warmup(started_on=" 2026-11-01 ", method=" MaNuAl ", warm_level=" 7 ")
    assert (record.warmup_started_on, record.warmup_method, record.warm_level) == (
        date(2026, 11, 1),
        "manual",
        7,
    )


def test_a_tool_name_keeps_its_case() -> None:
    assert warmup(method="Instantly").warmup_method == "Instantly"
    assert warmup(method="x" * MAX_METHOD_LENGTH).warmup_method == "x" * 40


def test_today_is_allowed_and_tomorrow_is_refused() -> None:
    assert warmup(started_on="2026-11-18").warmup_started_on == TODAY
    assert warmup_refusal(started_on="2026-11-19") is WarmupRefusal.FUTURE_START


@pytest.mark.parametrize("text", ["2026-11-31", "20261101", "2026-11-1", "yesterday", "2026/11/01"])
def test_a_malformed_start_is_refused(text: str) -> None:
    assert warmup_refusal(started_on=text) is WarmupRefusal.INVALID_START


@pytest.mark.parametrize("text", ["", "x" * 41, "Tool\nname", "Tool\u200bname"])
def test_a_bad_method_is_refused(text: str) -> None:
    assert warmup_refusal(method=text) is WarmupRefusal.INVALID_METHOD


@pytest.mark.parametrize(
    ("field", "low", "high", "refusal"),
    [
        ("warm_level", 0, 100, WarmupRefusal.INVALID_WARM_LEVEL),
        ("tool_daily", 0, 100, WarmupRefusal.INVALID_TOOL_DAILY),
        ("ramp_step", 1, 10, WarmupRefusal.INVALID_RAMP_STEP),
        ("ramp_target", 1, 50, WarmupRefusal.INVALID_RAMP_TARGET),
    ],
)
def test_each_number_takes_its_range_and_refuses_anything_else(
    field: str, low: int, high: int, refusal: WarmupRefusal
) -> None:
    attribute = {"tool_daily": "warmup_tool_daily"}.get(field, field)
    assert getattr(warmup(**{field: str(low)}), attribute) == low
    assert getattr(warmup(**{field: str(high)}), attribute) == high
    for text in (str(low - 1), str(high + 1), "abc", "", "1.5", "-1", "\uff11", "1e2"):
        assert warmup_refusal(**{field: text}) is refusal, text


def test_all_three_record_fields_blank_clears_the_record() -> None:
    record = warmup(started_on="", method=" ", warm_level="")
    assert record == WarmupRecord(None, None, None, 0, 5, 30)


@pytest.mark.parametrize(
    ("blank", "refusal"),
    [
        ("started_on", WarmupRefusal.INVALID_START),
        ("method", WarmupRefusal.INVALID_METHOD),
        ("warm_level", WarmupRefusal.INVALID_WARM_LEVEL),
    ],
)
def test_one_blank_record_field_is_refused(blank: str, refusal: WarmupRefusal) -> None:
    assert warmup_refusal(**{blank: ""}) is refusal


def test_the_first_bad_field_in_form_order_wins() -> None:
    assert warmup_refusal(warm_level="101", ramp_step="0") is WarmupRefusal.INVALID_WARM_LEVEL
    assert warmup_refusal(started_on="x", method="") is WarmupRefusal.INVALID_START


def test_every_warmup_refusal_has_a_message_naming_its_field() -> None:
    assert set(WARMUP_MESSAGES) == set(WarmupRefusal)
    assert isinstance(WARMUP_MESSAGES, MappingProxyType)
    assert issubclass(WarmupRefused, DomainError)


def test_check_warmup_refuses_a_non_str() -> None:
    with pytest.raises(TypeError):
        check_warmup(1, "manual", "1", "0", "5", "30", today=TODAY)  # type: ignore[arg-type]
