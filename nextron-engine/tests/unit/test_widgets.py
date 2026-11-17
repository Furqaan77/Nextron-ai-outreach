"""``nextron.domain.widgets`` (Story 2.6): every signature from a script and an iframe source, a
marker and a phrase; links for booking and forms only; the generic rules; the first match, the
order and the cap; garbage input; and the Lead's profile value (``summarize``, ``encode``,
``decode``)."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nextron.domain.widgets import (
    BOOKING_SIGNATURES,
    CHAT_SIGNATURES,
    FORM_SIGNATURES,
    GENERIC_SIGNATURES,
    MAX_MATCHED,
    RULE_KEYS,
    SIGNATURES,
    ContactMethod,
    ContactMethods,
    Detection,
    MatchSource,
    Signature,
    decode,
    detect,
    encode,
    summarize,
)

RUN = "01JD0000000000000000000011"
AT = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)


def url_for(spec: str) -> str:
    """A URL a signature host matches: on a subdomain, under any path prefix it names."""
    host, _, prefix = spec.partition("/")
    path = f"/{prefix}/x" if prefix else "/widget.js"
    return f"https://cdn.{host}{path}"


def keys(found: tuple[Detection, ...]) -> set[tuple[str, str, str]]:
    return {(str(d.method), d.signature, str(d.source)) for d in found}


def only(found: tuple[Detection, ...], signature: str, source: MatchSource) -> Detection:
    [match] = [d for d in found if d.signature == signature and d.source is source]
    return match


HOST_SIGNATURES = [signature for signature in SIGNATURES if signature.hosts]


def host_cases() -> Iterator[tuple[Signature, str]]:
    for signature in HOST_SIGNATURES:
        for host in signature.hosts:
            yield signature, host


# The lists ------------------------------------------------------------------------------------


def test_the_signature_lists() -> None:
    assert [s.key for s in CHAT_SIGNATURES] == [
        "intercom",
        "drift",
        "livechat",
        "tawk_to",
        "zendesk_chat",
        "hubspot_chat",
        "tidio",
        "smith_ai",
        "ngage",
        "apexchat",
        "intaker",
        "podium",
        "olark",
        "crisp",
        "freshchat",
        "zoho_salesiq",
        "liveagent",
        "jivochat",
    ]
    assert [s.key for s in BOOKING_SIGNATURES] == [
        "calendly",
        "cal_com",
        "acuity",
        "clio_grow",
        "lawtap",
        "ms_bookings",
        "hubspot_meetings",
        "setmore",
        "simplybook",
        "youcanbookme",
        "zoho_bookings",
        "google_appointments",
    ]
    assert max(len(key) for key in RULE_KEYS) <= 32  # page_contact_methods.signature
    assert [s.key for s in GENERIC_SIGNATURES] == ["chat_text", "booking_text", "after_hours_text"]
    assert {s.method for s in CHAT_SIGNATURES} == {ContactMethod.CHAT}
    assert {s.method for s in BOOKING_SIGNATURES} == {ContactMethod.BOOKING}
    assert {s.method for s in FORM_SIGNATURES} == {ContactMethod.FORM}
    assert len(RULE_KEYS) == len(set(RULE_KEYS))
    assert RULE_KEYS[-3:] == ("form_tag", "tel_link", "phone_text")


@pytest.mark.parametrize("signature", [*CHAT_SIGNATURES, *BOOKING_SIGNATURES], ids=str)
def test_every_chat_and_booking_signature_has_a_host_and_a_phrase(signature: Signature) -> None:
    assert signature.hosts and signature.phrases
    if signature.method is ContactMethod.CHAT:  # a chat loader also shows in the raw HTML
        assert signature.markers
    else:  # a booking host as a raw-HTML substring would match other names (medical.com)
        assert not signature.markers


def test_the_named_hosts_markers_and_phrases_are_listed() -> None:
    hosts = {host for s in SIGNATURES for host in s.hosts}
    markers = {marker for s in SIGNATURES for marker in s.markers}
    phrases = {phrase for s in SIGNATURES for phrase in s.phrases}

    assert {
        "widget.intercom.io",
        "js.intercomcdn.com",
        "js.driftt.com",
        "cdn.livechatinc.com",
        "embed.tawk.to",
        "static.zdassets.com",
        "zopim.com",
        "js.usemessages.com",
        "code.tidio.co",
        "smith.ai",
        "calendly.com",
        "cal.com",
        "acuityscheduling.com",
        "as.me",
        "grow.clio.com",
        "lawtap.com",
        "outlook.office365.com/owa/calendar",
        "outlook.office365.com/book",
        "outlook.office.com/book",
        "meetings.hubspot.com",
        "docs.google.com/forms",
        "ngageics.com",
        "apexchat.net",
        "apexchat.com",
        "intaker.com",
        "podium.com",
        "olark.com",
        "crisp.chat",
        "freshchat.com",
        "salesiq.zoho.com",
        "salesiq.zohopublic.com",
        "salesiq.zoho.eu",
        "ladesk.com",
        "jivosite.com",
        "setmore.com",
        "simplybook.me",
        "youcanbook.me",
        "bookings.zoho.com",
        "zohobookings.com",
        "calendar.app.google",
        "calendar.google.com/calendar/appointments",
    } <= hosts
    assert {
        "intercomSettings",
        "drift.load(",
        "__lc.license",
        "Tawk_API",
        "zESettings",
        "HubSpotConversations",
        "tidioChatApi",
        "gform_wrapper",
        "wpcf7",
        "wpforms-form",
        "connect.podium.com",
        "static.olark.com",
        "client.crisp.chat",
        "$crisp",
        "wchat.freshchat.com",
        "$zoho.salesiq",
        "code.jivosite.com",
    } <= markers
    assert {
        "powered by intercom",
        "livechat",
        "tawk.to",
        "zendesk chat",
        "hubspot chat",
        "tidio",
        "smith.ai",
        "calendly",
        "acuity scheduling",
        "clio grow",
        "lawtap",
        "microsoft bookings",
        "hubspot meetings",
        "ngage live",
        "apexchat",
        "intaker",
        "powered by podium",
        "olark",
        "crisp chat",
        "freshchat",
        "zoho salesiq",
        "liveagent",
        "jivochat",
        "setmore",
        "simplybook.me",
        "youcanbook.me",
        "zoho bookings",
        "google calendar appointment",
    } <= phrases
    # the first eight chat loaders' hosts are also their markers (the spec's list)
    for signature in CHAT_SIGNATURES[:8]:
        assert set(signature.hosts) <= set(signature.markers), signature.key


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"key": "Bad Key", "method": ContactMethod.CHAT}, ValueError),
        ({"key": "x", "method": "chat"}, TypeError),
        ({"key": "x", "method": ContactMethod.CHAT, "hosts": ["a.com"]}, ValueError),
        ({"key": "x", "method": ContactMethod.CHAT, "hosts": ("A.com",)}, ValueError),
        ({"key": "x", "method": ContactMethod.CHAT, "hosts": ("https://a.com",)}, ValueError),
        ({"key": "x", "method": ContactMethod.CHAT, "markers": ("",)}, ValueError),
        ({"key": "x", "method": ContactMethod.CHAT, "phrases": ("Live chat",)}, ValueError),
        ({"key": "x", "method": ContactMethod.CHAT, "phrases": (" chat",)}, ValueError),
    ],
)
def test_a_bad_signature_is_refused(arguments: dict[str, object], error: type) -> None:
    with pytest.raises(error):
        Signature(**arguments)  # type: ignore[arg-type]


# Each signature from each source ----------------------------------------------------------------


@pytest.mark.parametrize(("signature", "host"), list(host_cases()), ids=str)
@pytest.mark.parametrize("tag", ["script", "iframe"])
def test_every_signature_host_is_detected_from_a_script_or_iframe_source(
    signature: Signature, host: str, tag: str
) -> None:
    url = url_for(host)

    found = detect("", [(tag, url)], "")

    match = only(found, signature.key, MatchSource(tag))
    assert (match.method, match.matched) == (signature.method, f"cdn.{host.split('/')[0]}")


@pytest.mark.parametrize(("signature", "host"), list(host_cases()), ids=str)
def test_only_booking_and_form_hosts_are_detected_from_a_link(
    signature: Signature, host: str
) -> None:
    found = detect("", [("a", url_for(host))], "")

    if signature.method in (ContactMethod.BOOKING, ContactMethod.FORM):
        assert only(found, signature.key, MatchSource.LINK).method is signature.method
    else:  # a link to a chat vendor's own site is no chat widget
        assert found == ()


@pytest.mark.parametrize(
    ("signature", "marker"),
    [(signature, marker) for signature in SIGNATURES for marker in signature.markers],
    ids=str,
)
def test_every_marker_is_detected_in_the_raw_html_whatever_its_case(
    signature: Signature, marker: str
) -> None:
    html = f"<html><head><script>var x = '{marker.upper()}';</script></head><body></body></html>"

    found = detect(html, [], "")

    match = only(found, signature.key, MatchSource.HTML)
    assert (match.method, match.matched) == (signature.method, marker)


@pytest.mark.parametrize(
    ("signature", "phrase"),
    [(signature, phrase) for signature in SIGNATURES for phrase in signature.phrases],
    ids=str,
)
def test_every_phrase_is_detected_in_the_visible_text_as_written(
    signature: Signature, phrase: str
) -> None:
    written = phrase.title().replace(" ", "\n", 1)  # another case, and a line break
    text = f"Welcome. ({written}) Thanks."

    found = detect("", [], text)

    match = only(found, signature.key, MatchSource.TEXT)
    assert (match.method, match.matched) == (signature.method, written)


def test_a_rich_page_shows_every_source_at_once() -> None:
    html = (
        "<html><head><script>window.intercomSettings = {app_id: 'x'};</script></head><body>"
        '<a href="tel:+12125550147">Call us</a></body></html>'
    )
    embeds = [
        ("script", "https://widget.intercom.io/widget/abc123"),
        ("iframe", "https://calendly.com/smith-law/consultation"),
        ("a", "https://forms.gle/AbCdEf"),
    ]

    found = detect(html, embeds, "Chat with us any time. Powered by Intercom")

    assert [(str(d.method), d.signature, str(d.source), d.matched) for d in found] == [
        ("form", "google_forms", "link", "forms.gle"),
        ("chat", "intercom", "script", "widget.intercom.io"),
        ("chat", "intercom", "html", "intercomSettings"),
        ("chat", "intercom", "text", "Powered by Intercom"),
        ("chat", "chat_text", "text", "Chat with us"),
        ("booking", "calendly", "iframe", "calendly.com"),
        ("phone", "tel_link", "html", "tel:+12125550147"),
    ]


# Hosts ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "detected"),
    [
        ("https://calendly.com/jane", True),
        ("https://assets.calendly.com/assets/external/widget.js", True),
        ("HTTPS://ASSETS.CALENDLY.COM./x", True),
        ("//calendly.com/jane", True),
        ("https://calendly.com:443/jane", True),
        ("https://notcalendly.com/jane", False),
        ("https://calendly.com.evil.example/jane", False),
        ("https://example.com/?next=https://calendly.com/jane", False),
        ("calendly.com/jane", False),
        ("https://[::1/x", False),
        ("", False),
    ],
)
def test_a_host_matches_itself_or_a_subdomain_only(url: str, detected: bool) -> None:
    found = detect("", [("iframe", url)], "")

    assert bool(found) is detected


@pytest.mark.parametrize(
    ("url", "detected"),
    [
        ("https://docs.google.com/forms/d/e/abc/viewform", True),
        ("https://docs.google.com/forms", True),
        ("https://docs.google.com/FORMS/d/e/abc", True),
        ("https://docs.google.com/formsx/d", False),
        ("https://docs.google.com/document/d/abc", False),
        ("https://docs.google.com/", False),
        ("https://outlook.office365.com/owa/calendar/SmithLaw@smith.com/bookings/", True),
        ("https://outlook.office365.com/owa/mail/", False),
        ("https://outlook.office.com/book/SmithLaw@smith.com/", True),
        ("https://outlook.office.com/bookings", False),
    ],
)
def test_a_host_with_a_path_needs_that_path_or_one_under_it(url: str, detected: bool) -> None:
    found = detect("", [("a", url)], "")

    assert bool(found) is detected


def test_the_stored_match_is_the_host_never_the_url() -> None:
    url = "https://smithlaw.as.me/schedule.php?owner=12345678&appointmentType=999"

    [match] = detect("", [("a", url)], "")

    assert (match.signature, match.matched) == ("acuity", "smithlaw.as.me")


# Generic rules ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("html", "matched"),
    [
        ("<form action='/send'><textarea name='m'></textarea></form>", "form textarea"),
        ("<FORM><div><TEXTAREA></TEXTAREA></div></FORM>", "form textarea"),
        ('<form><input type="TEL" name="p"></form>', "form input[type=tel]"),
        ("<form><input type=tel /></form>", "form input[type=tel]"),
        ("<form><input type='text'><input type='email'></form>", None),
        ("<textarea></textarea><form><input></form>", None),
        ("<form></form><textarea></textarea>", None),
        ("<p>a form with a textarea</p>", None),
    ],
)
def test_form_tag_needs_a_textarea_or_a_tel_input_inside_a_form(
    html: str, matched: str | None
) -> None:
    found = [d for d in detect(html, [], "") if d.signature == "form_tag"]

    assert [d.matched for d in found] == ([] if matched is None else [matched])
    assert all(d.method is ContactMethod.FORM and d.source is MatchSource.HTML for d in found)


@pytest.mark.parametrize(
    ("html", "matched"),
    [
        ('<a href="tel:+12125550147">Call</a>', "tel:+12125550147"),
        ("<a href=' TEL:020 7946 0958 '>Call</a>", "TEL:020 7946 0958"),
        ('<a href="&#116;el:0207">Call</a>', "tel:0207"),
        ('<area href="tel:999">', "tel:999"),
        ('<a href="mailto:jane@smith-law.co.uk">Mail</a>', None),
        ("<p>tel: 020 7946 0958</p>", None),
    ],
)
def test_tel_link_is_an_href_that_starts_tel(html: str, matched: str | None) -> None:
    found = [d for d in detect(html, [], "") if d.signature == "tel_link"]

    assert [d.matched for d in found] == ([] if matched is None else [matched])
    assert all(d.method is ContactMethod.PHONE for d in found)


@pytest.mark.parametrize(
    ("text", "matched"),
    [
        ("Call (212) 555-0147 today", "(212) 555-0147"),
        ("Call 212-555-0147", "212-555-0147"),
        ("Call 212.555.0147", "212.555.0147"),
        ("Call 212 555 0147", "212 555 0147"),
        ("Call 2125550147", "2125550147"),
        ("Call +1 212 555 0147", "+1 212 555 0147"),
        ("Call +1-212-555-0147", "+1-212-555-0147"),
        ("Call 020 7946 0958", "020 7946 0958"),
        ("Call +44 20 7946 0958", "+44 20 7946 0958"),
        ("Call +44 (0)20 7946 0958", "+44 (0)20 7946 0958"),
        ("Call 01632 960001", "01632 960001"),
        ("Call 0800-123-4567", "0800-123-4567"),
        ("Call 0161 496 000", "0161 496 000"),
        ("Founded in 1998, 25 lawyers", None),
        ("Case 212555014", None),
        ("Ref 21255501470", None),
        ("Ref 0123456", None),
    ],
)
def test_phone_text_finds_a_us_or_uk_number(text: str, matched: str | None) -> None:
    found = [d for d in detect("", [], text) if d.signature == "phone_text"]

    assert [d.matched for d in found] == ([] if matched is None else [matched])
    assert all(d.method is ContactMethod.PHONE and d.source is MatchSource.TEXT for d in found)


def test_phone_text_keeps_the_first_number_on_the_page() -> None:
    [match] = detect("", [], "London: +44 20 7946 0958\nNew York: (212) 555-0147")

    assert match.matched == "+44 20 7946 0958"


def test_the_matrix_generic_signals() -> None:
    html = "<form><textarea></textarea></form>"
    text = "24/7 emergency line\nLive chat\nCall (212) 555-0147"

    found = detect(html, [], text)

    assert [(str(d.method), d.signature, str(d.source), d.matched) for d in found] == [
        ("form", "form_tag", "html", "form textarea"),
        ("chat", "chat_text", "text", "Live chat"),
        ("phone", "phone_text", "text", "(212) 555-0147"),
        ("after_hours", "after_hours_text", "text", "24/7"),
    ]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Out-of-hours line", True),
        ("We work around the clock", True),
        ("A 24-hour service", True),
        ("24x7 support", True),
        ("Open evenings and weekends", True),
        ("Call our emergency line", True),
        ("We are open 24 hours a day", True),
        ("24-hours", False),  # a letter after the phrase
        ("124/7", False),  # a digit before it
        ("afterhours", False),
        ("Livechat-free", False),
    ],
)
def test_phrases_are_whole(text: str, found: bool) -> None:
    after_hours = [d for d in detect("", [], text) if d.method is ContactMethod.AFTER_HOURS]

    assert bool(after_hours) is found


def test_a_phrase_or_marker_in_the_wrong_source_is_not_detected() -> None:
    # A phrase only in the raw HTML (a script's string), a marker only in the text, and a chat
    # host only in a link: none counts
    html = "<script>var label = 'live chat';</script>"
    text = "Our intercomSettings are private."

    found = detect(html, [("a", "https://www.intercom.com/")], text)

    assert found == ()


# First match, order, cap, garbage --------------------------------------------------------------


def test_one_detection_per_method_signature_and_source_keeps_the_first() -> None:
    embeds = [
        ("script", "https://js.intercomcdn.com/frame.js"),
        ("script", "https://widget.intercom.io/widget/abc"),
        ("iframe", "https://widget.intercom.io/frame"),
    ]
    html = "<script>js.intercomcdn.com; window.intercomSettings = {};</script>"
    text = "Book online, or online booking"

    found = detect(html, embeds, text)

    assert only(found, "intercom", MatchSource.SCRIPT).matched == "js.intercomcdn.com"
    assert only(found, "intercom", MatchSource.IFRAME).matched == "widget.intercom.io"
    assert only(found, "intercom", MatchSource.HTML).matched == "js.intercomcdn.com"  # earliest
    assert only(found, "booking_text", MatchSource.TEXT).matched == "Book online"
    assert len(found) == len(keys(found))


def test_the_order_is_by_method_then_signature_then_source() -> None:
    embeds = [
        ("a", "https://lawtap.com/smith"),
        ("iframe", "https://calendly.com/smith"),
        ("script", "https://code.tidio.co/abc.js"),
        ("script", "https://embed.tawk.to/abc"),
    ]

    found = detect("<form><textarea></textarea></form>", embeds, "Call 020 7946 0958")

    assert [(str(d.method), d.signature, str(d.source)) for d in found] == [
        ("form", "form_tag", "html"),
        ("chat", "tawk_to", "script"),
        ("chat", "tidio", "script"),
        ("booking", "calendly", "iframe"),
        ("booking", "lawtap", "link"),
        ("phone", "phone_text", "text"),
    ]
    assert detect("<form><textarea></textarea></form>", list(reversed(embeds)), "") == tuple(
        d for d in found if d.signature != "phone_text"
    )


def test_matched_is_capped_and_never_in_the_repr() -> None:
    number = "tel:" + "1" * 300

    [match] = detect(f'<a href="{number}">Call</a>', [], "")

    assert match.matched == number[:MAX_MATCHED]
    assert len(match.matched) == MAX_MATCHED
    assert "1111" not in repr(match)


def raising_pairs() -> Iterator[tuple[str, str]]:
    yield ("script", "https://embed.tawk.to/abc")
    raise RuntimeError("a broken iterable")


@pytest.mark.parametrize(
    ("html", "embeds", "text"),
    [
        (None, None, None),
        (b"<form><textarea>", 5, b"Live chat"),
        ("<form><textarea", [("script",), ("img", "https://calendly.com/x"), "ab", 7], ""),
        ("<!x <![CDATA[ <form <textarea", [("a", None), (None, "https://x")], "\x00\ud800"),
        ("</form></form><form>" * 50, iter(()), " " * 10),
        ("", "https://calendly.com/x", ""),
        ("", {("iframe", "https://calendly.com/x")}, ""),
    ],
)
def test_garbage_never_raises(html: object, embeds: object, text: object) -> None:
    found = detect(html, embeds, text)  # type: ignore[arg-type]

    assert isinstance(found, tuple)
    assert all(isinstance(d, Detection) for d in found)


def test_a_broken_iterable_keeps_what_was_read() -> None:
    found = detect("", raising_pairs(), "")

    assert keys(found) == {("chat", "tawk_to", "script")}


# The profile value ------------------------------------------------------------------------------


def test_summarize_lists_every_page_checked_and_each_methods_pages() -> None:
    pages = [
        ("https://smith-law.co.uk/team", []),
        ("https://smith-law.co.uk/", ["chat", ContactMethod.PHONE]),
        ("https://smith-law.co.uk/contact", [ContactMethod.FORM, ContactMethod.PHONE, "phone"]),
        ("https://smith-law.co.uk/", ["booking"]),
    ]

    value = summarize(RUN, AT, pages)

    assert value.run_id == RUN
    assert value.checked_at == AT
    assert value.pages_checked == (
        "https://smith-law.co.uk/",
        "https://smith-law.co.uk/contact",
        "https://smith-law.co.uk/team",
    )
    assert dict(value.found) == {
        ContactMethod.FORM: ("https://smith-law.co.uk/contact",),
        ContactMethod.CHAT: ("https://smith-law.co.uk/",),
        ContactMethod.BOOKING: ("https://smith-law.co.uk/",),
        ContactMethod.PHONE: ("https://smith-law.co.uk/", "https://smith-law.co.uk/contact"),
        ContactMethod.AFTER_HOURS: (),
    }
    assert list(value.found) == list(ContactMethod)


def test_summarize_with_no_page_checks_nothing() -> None:
    value = summarize(RUN, AT, [])

    assert value.pages_checked == ()
    assert all(urls == () for urls in value.found.values())


@pytest.mark.parametrize(
    ("pages", "error"),
    [
        ([("https://a/", ["fax"])], ValueError),
        ([(None, [])], TypeError),
    ],
)
def test_summarize_refuses_bad_pages(pages: list[tuple[object, list[object]]], error: type) -> None:
    with pytest.raises(error):
        summarize(RUN, AT, pages)  # type: ignore[arg-type]


def test_encode_writes_canonical_json_and_decode_reads_it_back() -> None:
    value = summarize(
        RUN,
        datetime(2026, 11, 18, 10, 30, 5, 123, tzinfo=timezone(timedelta(hours=1))),
        [("https://a.example/", ["chat"]), ("https://b.example/", [])],
    )

    text = encode(value)

    assert text == (
        '{"checked_at":"2026-11-18T09:30:05.000123Z","found":{"after_hours":[],"booking":[],'
        '"chat":["https://a.example/"],"form":[],"phone":[]},"pages_checked":'
        '["https://a.example/","https://b.example/"],"run_id":"01JD0000000000000000000011","v":1}'
    )
    assert decode(text) == value
    assert encode(decode(text)) == text


def good_document() -> dict[str, object]:
    return json.loads(encode(summarize(RUN, AT, [("https://a/", ["chat"])])))


def mutated(change: str) -> str:
    document = good_document()
    found = document["found"]
    assert isinstance(found, dict)
    if change == "extra field":
        document["x"] = 1
    elif change == "missing field":
        del document["run_id"]
    elif change == "version 2":
        document["v"] = 2
    elif change == "version true":
        document["v"] = True
    elif change == "version float":  # 1.0 == 1, but encode never writes it
        document["v"] = 1.0
    elif change == "naive stamp":
        document["checked_at"] = "2026-11-18T09:30:00"
    elif change == "missing method":
        del found["phone"]
    elif change == "unknown method":
        found["fax"] = []
    elif change == "urls not a list":
        found["chat"] = "https://a/"
    elif change == "url not checked":
        found["phone"] = ["https://b/"]
    elif change == "unsorted":
        document["pages_checked"] = ["https://b/", "https://a/"]
    elif change == "run id not a str":
        document["run_id"] = 7
    return json.dumps(document)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        *(
            mutated(change)
            for change in (
                "extra field",
                "missing field",
                "version 2",
                "version true",
                "version float",
                "naive stamp",
                "missing method",
                "unknown method",
                "urls not a list",
                "url not checked",
                "unsorted",
                "run id not a str",
            )
        ),
    ],
)
def test_decode_refuses_anything_encode_would_not_write(text: str) -> None:
    with pytest.raises(ValueError):
        decode(text)


def test_decode_refuses_a_non_str() -> None:
    with pytest.raises(ValueError):
        decode(b"{}")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "arguments",
    [
        {"checked_at": datetime(2026, 11, 18)},  # naive
        {"pages_checked": ["https://a/"]},
        {"pages_checked": ("https://b/", "https://a/")},
        {"found": {}},
    ],
)
def test_contact_methods_checks_its_values(arguments: dict[str, object]) -> None:
    values: dict[str, object] = {
        "run_id": RUN,
        "checked_at": AT,
        "pages_checked": ("https://a/",),
        "found": {method: () for method in ContactMethod},
        **arguments,
    }

    with pytest.raises((TypeError, ValueError)):
        ContactMethods(**values)  # type: ignore[arg-type]


def test_encode_takes_only_contact_methods() -> None:
    with pytest.raises(TypeError):
        encode({"v": 1})  # type: ignore[arg-type]
