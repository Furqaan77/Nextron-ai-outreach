"""The dev and test sink (AD-14, NFR-8, NFR-17): the rules in ``nextron.domain.sink`` and every
I/O row for ``AlertSink`` and ``MailSink`` (``nextron.adapters.sink``), on in-memory fakes and
temporary folders."""

import json
import logging
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from nextron.adapters.sink import AlertSink, MailSink, SinkRecorder
from nextron.domain.sink import PROD, allowlisted, is_sink_recipient, sink_applies
from nextron.ports.alert import AlertDelivery, AlertMessage, AlertPort
from nextron.ports.errors import AdapterError
from nextron.ports.mail import MailDelivery, MailMessage, MailPort
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.mail import InMemoryMail

T = datetime(2026, 11, 17, 23, 59, 59, tzinfo=UTC)
TEXT = "Hot Lead 01JD3QZ8X5V9K2M7N4P6R8T0WY: 2 replies waiting"
SUBJECT = "Quick question about your intake process"


# The rules --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "applies"),
    [
        ("prod", False),
        ("dev", True),
        ("test", True),
        ("PROD", True),  # anything but exactly "prod" is sunk
        ("prod ", True),
        ("", True),
        (None, True),
    ],
)
def test_the_sink_applies_everywhere_except_exactly_prod(env: object, applies: bool) -> None:
    assert sink_applies(env) is applies
    assert PROD == "prod"


@pytest.mark.parametrize(
    "text",
    [
        "a@b.co",
        "Mac@Example.com",
        "ann.lee+x@mail.example.co.uk",
        "o'brien_x!#$%&*/=?^`{|}~-@x-y.example",
        "a" * 64 + "@" + ("b" * 60 + ".") * 3 + "c" * 6,  # 254 characters
        "123",
        "-100123",
        "1" * 20,
        "-" + "9" * 20,
    ],
)
def test_email_addresses_and_numeric_chat_ids_are_recipients(text: str) -> None:
    assert is_sink_recipient(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "@chan",  # a Telegram user name: it can be handed to someone else
        "a@b",  # one label
        "a@b.",
        "a@.b.co",
        "@b.co",
        "a b@c.co",
        "a@x.com ",  # nothing is stripped
        " a@x.com",
        "a@x.com\n",
        "a@x.com, b@y.com",
        "straße@x.com",  # ASCII only
        "a@exämple.com",
        "<a@x.com>",
        "Mac <a@x.com>",
        "a@[127.0.0.1]",
        "a" * 64 + "@" + ("b" * 60 + ".") * 3 + "c" * 7,  # 255 characters
        "0",
        "-0",
        "012",
        "1" * 21,
        "+44123",
        "12a",
        "١٢٣",  # Arabic-Indic digits
    ],
)
def test_anything_else_is_not_a_recipient(text: str) -> None:
    assert not is_sink_recipient(text)


@pytest.mark.parametrize("value", [None, 123, b"a@b.co", ["a@b.co"]])
def test_a_recipient_is_a_str(value: object) -> None:
    assert not is_sink_recipient(value)


def test_allowlisted_compares_after_ascii_lowercasing() -> None:
    assert allowlisted("mac@example.com", ["Mac@Example.com"])
    assert allowlisted("MAC@EXAMPLE.COM", ("mac@example.com",))
    assert allowlisted("-100123", ["-100123"])
    assert not allowlisted("-100123", ["100123"])
    assert not allowlisted("b@example.com", ["mac@example.com"])


@pytest.mark.parametrize(
    ("recipient", "allowlist"),
    [
        ("a@x.com ", ["a@x.com"]),  # nothing is stripped
        ("a@x.com ", ["a@x.com "]),  # an invalid recipient never matches, even itself
        ("a@x.com, b@y.com", ["a@x.com", "b@y.com"]),
        ("straße@x.com", ["strasse@x.com"]),  # no Unicode casefolding
        ("straße@x.com", ["straße@x.com"]),
        ("strasse@x.com", ["straße@x.com"]),
        ("K@x.com", ["k@x.com"]),  # the Kelvin sign lowercases to k in Unicode only
        ("a@x.com", []),
        ("a@x.com", "a@x.com"),  # a bare str is not a list of entries
        ("1", "1"),
        ("a@x.com", [None, 7, b"a@x.com", ["a@x.com"]]),  # entries that aren't str are ignored
        (None, [None]),
    ],
)
def test_near_misses_are_not_allowlisted(recipient: object, allowlist: Any) -> None:
    assert not allowlisted(recipient, allowlist)


def test_invalid_entries_are_ignored_and_valid_ones_still_match() -> None:
    assert allowlisted("a@x.com", ["@chan", "a@x.com ", 7, "A@X.COM"])


# The sinks --------------------------------------------------------------------------------------


class Allowlist:
    """An allowlist provider that counts its calls; ``failure`` makes it raise."""

    def __init__(self, *entries: str) -> None:
        self.entries: list[str] = list(entries)
        self.calls = 0
        self.failure: BaseException | None = None

    def __call__(self) -> Iterable[str]:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return tuple(self.entries)


def sink_folder(tmp_path: Path) -> Path:
    return tmp_path / "data" / "dev" / "sink"


def lines(folder: Path, day: str = "2026-11-17") -> list[str]:
    return (folder / f"{day}.jsonl").read_bytes().decode("ascii").splitlines()


def logged(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str, dict[str, object]]]:
    """``(level, event, extras)`` of every ``nextron.adapters.sink`` record."""
    standard = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
        "message",
        "asctime",
    }
    return [
        (
            record.levelname,
            record.getMessage(),
            {key: value for key, value in vars(record).items() if key not in standard},
        )
        for record in caplog.records
        if record.name == "nextron.adapters.sink"
    ]


def assert_nothing_personal_logged(caplog: pytest.LogCaptureFixture, *texts: str) -> None:
    for record in caplog.records:
        everything = json.dumps(vars(record), default=repr)
        for text in texts:
            assert text not in everything, record


@pytest.fixture
def caplog_info(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="nextron.adapters.sink")
    return caplog


SinkFactory = Callable[..., Any]


def alert_sink(wrapped: Any, allowlist: Any, recorder: SinkRecorder) -> AlertSink:
    return AlertSink(wrapped, allowlist, recorder)


def mail_sink(wrapped: Any, allowlist: Any, recorder: SinkRecorder) -> MailSink:
    return MailSink(wrapped, allowlist, recorder)


class Case:
    """One port's sink, its fake, a message to ``recipient`` and the recorded line it gives."""

    def __init__(self, port: str) -> None:
        self.port = port
        self.fake: InMemoryAlert | InMemoryMail = (
            InMemoryAlert() if port == "alert" else InMemoryMail()
        )

    def sink(self, allowlist: Any, recorder: SinkRecorder) -> AlertSink | MailSink:
        factory = alert_sink if self.port == "alert" else mail_sink
        return factory(self.fake, allowlist, recorder)

    def message(self, recipient: str, text: str = TEXT) -> AlertMessage | MailMessage:
        if self.port == "alert":
            return AlertMessage(recipient, text)
        return MailMessage(recipient, SUBJECT, text)

    def sunk(self) -> AlertDelivery | MailDelivery:
        if self.port == "alert":
            return AlertDelivery(None, sink=True)
        return MailDelivery(None, None, sink=True)

    def line(self, recipient: str, time: str, text: str = TEXT) -> str:
        fields: dict[str, str] = {"time": time, "port": self.port, "recipient": recipient}
        if self.port == "mail":
            fields["subject"] = SUBJECT
        fields["text"] = text
        return json.dumps(fields, separators=(",", ":"))


@pytest.fixture(params=["alert", "mail"])
def case(request: pytest.FixtureRequest) -> Case:
    return Case(request.param)


def test_the_sinks_implement_their_ports(tmp_path: Path) -> None:
    recorder = SinkRecorder(tmp_path, FrozenClock(T))
    alert = AlertSink(InMemoryAlert(), Allowlist(), recorder)
    mail = MailSink(InMemoryMail(), Allowlist(), recorder)

    assert isinstance(alert, AlertPort) and isinstance(mail, MailPort)
    assert recorder.folder == tmp_path


def test_the_wrapped_adapter_is_exposed(case: Case, tmp_path: Path) -> None:
    sink = case.sink(Allowlist(), SinkRecorder(tmp_path, FrozenClock(T)))

    assert sink.wrapped is case.fake


# Row: Allowlisted
def test_an_allowlisted_recipient_is_delivered_by_the_wrapped_adapter(
    case: Case, tmp_path: Path, caplog_info: pytest.LogCaptureFixture
) -> None:
    folder = sink_folder(tmp_path)
    sink = case.sink(Allowlist("Mac@Example.com"), SinkRecorder(folder, FrozenClock(T)))
    message = case.message("mac@example.com")

    delivery = sink.send(message)

    assert case.fake.sent == [message]
    assert delivery.sink is False
    assert delivery.message_id == f"{case.port}-1"
    assert not folder.exists()
    assert logged(caplog_info) == [("INFO", "sink_passed", {"port": case.port})]
    assert_nothing_personal_logged(caplog_info, "mac@example.com", "Mac@Example.com", TEXT)


def test_the_wrapped_delivery_is_returned_unchanged(tmp_path: Path) -> None:
    returned = AlertDelivery("tg-42")

    class Wrapped:
        def send(self, message: AlertMessage) -> AlertDelivery:
            return returned

    sink = AlertSink(Wrapped(), Allowlist("-100123"), SinkRecorder(tmp_path, FrozenClock(T)))

    assert sink.send(AlertMessage("-100123", TEXT)) is returned


def test_the_wrapped_adapters_errors_propagate(case: Case, tmp_path: Path) -> None:
    case.fake.failure = AdapterError(True, "rate_limited", "429 from the provider")
    folder = sink_folder(tmp_path)
    sink = case.sink(Allowlist("mac@example.com"), SinkRecorder(folder, FrozenClock(T)))

    with pytest.raises(AdapterError) as raised:
        sink.send(case.message("mac@example.com"))

    assert raised.value is case.fake.failure
    assert not folder.exists()  # a failed delivery is not recorded instead


# Rows: Other recipient, Mail
def test_any_other_recipient_is_recorded_and_never_sent(
    case: Case, tmp_path: Path, caplog_info: pytest.LogCaptureFixture
) -> None:
    folder = sink_folder(tmp_path)
    sink = case.sink(Allowlist("mac@example.com"), SinkRecorder(folder, FrozenClock(T)))

    delivery = sink.send(case.message("-100123"))

    assert delivery == case.sunk()
    assert type(delivery) is type(case.sunk())
    assert case.fake.sent == []
    assert lines(folder) == [case.line("-100123", "2026-11-17T23:59:59.000000Z")]
    assert [path.name for path in folder.iterdir()] == ["2026-11-17.jsonl"]
    assert logged(caplog_info) == [("INFO", "sink_recorded", {"port": case.port})]
    assert_nothing_personal_logged(caplog_info, "-100123", TEXT, SUBJECT)


def test_the_alert_line_is_exactly_the_documented_one(tmp_path: Path) -> None:
    folder = sink_folder(tmp_path)
    sink = AlertSink(InMemoryAlert(), Allowlist(), SinkRecorder(folder, FrozenClock(T)))

    sink.send(AlertMessage("-100123", "2 replies"))

    assert (folder / "2026-11-17.jsonl").read_bytes() == (
        b'{"time":"2026-11-17T23:59:59.000000Z","port":"alert","recipient":"-100123",'
        b'"text":"2 replies"}\n'
    )


def test_the_mail_line_also_has_the_subject(tmp_path: Path) -> None:
    folder = sink_folder(tmp_path)
    clock = FrozenClock(T.replace(microsecond=123456))
    sink = MailSink(InMemoryMail(), Allowlist(), SinkRecorder(folder, clock))

    delivery = sink.send(MailMessage("ann@prospect.example", "Hello", "Hi Ann"))

    assert delivery == MailDelivery(None, None, sink=True)
    assert json.loads(lines(folder)[0]) == {
        "time": "2026-11-17T23:59:59.123456Z",
        "port": "mail",
        "recipient": "ann@prospect.example",
        "subject": "Hello",
        "text": "Hi Ann",
    }
    assert list(json.loads(lines(folder)[0])) == ["time", "port", "recipient", "subject", "text"]


# Row: Near miss
@pytest.mark.parametrize("recipient", ["a@x.com ", "a@x.com, b@y.com", "straße@x.com"])
def test_a_near_miss_is_recorded(case: Case, recipient: str, tmp_path: Path) -> None:
    folder = sink_folder(tmp_path)
    allowlist = Allowlist("a@x.com", "b@y.com", "strasse@x.com")
    sink = case.sink(allowlist, SinkRecorder(folder, FrozenClock(T)))

    delivery = sink.send(case.message(recipient))

    assert delivery.sink is True
    assert case.fake.sent == []
    assert json.loads(lines(folder)[0])["recipient"] == recipient


def test_with_an_empty_allowlist_everything_is_recorded(case: Case, tmp_path: Path) -> None:
    folder = sink_folder(tmp_path)
    sink = case.sink(Allowlist(), SinkRecorder(folder, FrozenClock(T)))

    for recipient in ("mac@example.com", "-100123"):
        assert sink.send(case.message(recipient)).sink is True

    assert case.fake.sent == []
    assert len(lines(folder)) == 2


# Row: Allowlist fails
@pytest.mark.parametrize(
    ("failure", "error"),
    [(RuntimeError("database is locked"), "RuntimeError"), (KeyError("x"), "KeyError")],
)
def test_an_unreadable_allowlist_records_the_message_with_a_warning(
    case: Case,
    failure: BaseException,
    error: str,
    tmp_path: Path,
    caplog_info: pytest.LogCaptureFixture,
) -> None:
    folder = sink_folder(tmp_path)
    allowlist = Allowlist("mac@example.com")
    allowlist.failure = failure
    sink = case.sink(allowlist, SinkRecorder(folder, FrozenClock(T)))

    delivery = sink.send(case.message("mac@example.com"))

    assert delivery == case.sunk()
    assert case.fake.sent == []  # never delivered when the allowlist can't be read
    assert len(lines(folder)) == 1
    assert logged(caplog_info) == [
        ("WARNING", "sink_allowlist_unreadable", {"port": case.port, "error": error}),
        ("INFO", "sink_recorded", {"port": case.port}),
    ]
    assert_nothing_personal_logged(caplog_info, "mac@example.com", "database is locked")


@pytest.mark.parametrize("returned", [None, 42])
def test_an_allowlist_that_isnt_iterable_counts_as_unreadable(
    returned: object, tmp_path: Path, caplog_info: pytest.LogCaptureFixture
) -> None:
    fake = InMemoryAlert()
    provider: Any = lambda: returned  # noqa: E731
    sink = AlertSink(fake, provider, SinkRecorder(tmp_path, FrozenClock(T)))

    assert sink.send(AlertMessage("-100123", TEXT)).sink is True
    assert fake.sent == []
    assert logged(caplog_info)[0] == (
        "WARNING",
        "sink_allowlist_unreadable",
        {"port": "alert", "error": "TypeError"},
    )


@pytest.mark.parametrize(("returned", "error"), [("123", "str"), (b"123", "bytes")])
def test_an_allowlist_that_is_a_bare_string_counts_as_unreadable(
    case: Case,
    returned: object,
    error: str,
    tmp_path: Path,
    caplog_info: pytest.LogCaptureFixture,
) -> None:
    """``tuple("123")`` would be ``("1", "2", "3")``, which would allowlist the chat id ``1``."""
    folder = sink_folder(tmp_path)
    provider: Any = lambda: returned  # noqa: E731
    sink = case.sink(provider, SinkRecorder(folder, FrozenClock(T)))

    delivery = sink.send(case.message("1"))

    assert delivery == case.sunk()
    assert case.fake.sent == []
    assert json.loads(lines(folder)[0])["recipient"] == "1"
    assert logged(caplog_info) == [
        ("WARNING", "sink_allowlist_unreadable", {"port": case.port, "error": error}),
        ("INFO", "sink_recorded", {"port": case.port}),
    ]


def test_an_allowlist_that_fails_midway_counts_as_unreadable(tmp_path: Path) -> None:
    def entries() -> Iterable[str]:
        yield "-100123"
        raise RuntimeError("broken")

    fake = InMemoryAlert()
    sink = AlertSink(fake, entries, SinkRecorder(tmp_path, FrozenClock(T)))

    assert sink.send(AlertMessage("-100123", TEXT)).sink is True
    assert fake.sent == []


# Row: Write fails
def test_a_folder_that_cant_be_created_raises_sink_write_failed(
    case: Case, tmp_path: Path, caplog_info: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "data"
    blocker.write_text("a file where the data dir should be")
    sink = case.sink(Allowlist(), SinkRecorder(blocker / "dev" / "sink", FrozenClock(T)))

    with pytest.raises(AdapterError) as raised:
        sink.send(case.message("-100123"))

    assert (raised.value.retryable, raised.value.code) == (False, "sink_write_failed")
    assert isinstance(raised.value.__cause__, OSError)
    assert case.fake.sent == []
    assert "-100123" not in raised.value.message and TEXT not in raised.value.message
    assert [event for _, event, _ in logged(caplog_info)] == []


def test_a_file_that_cant_be_written_raises_sink_write_failed(
    case: Case, tmp_path: Path
) -> None:
    folder = sink_folder(tmp_path)
    (folder / "2026-11-17.jsonl").mkdir(parents=True)  # a folder where the day's file goes
    sink = case.sink(Allowlist(), SinkRecorder(folder, FrozenClock(T)))

    with pytest.raises(AdapterError) as raised:
        sink.send(case.message("-100123"))

    assert raised.value.code == "sink_write_failed"
    assert case.fake.sent == []


def test_a_recipient_json_cant_hold_raises_sink_write_failed(tmp_path: Path) -> None:
    fake = InMemoryAlert()
    sink = AlertSink(fake, Allowlist(), SinkRecorder(tmp_path, FrozenClock(T)))

    with pytest.raises(AdapterError) as raised:
        sink.send(AlertMessage(object(), TEXT))  # type: ignore[arg-type]

    assert raised.value.code == "sink_write_failed"
    assert fake.sent == []


# The file -------------------------------------------------------------------------------------


def test_each_utc_day_has_its_own_file(case: Case, tmp_path: Path) -> None:
    folder = sink_folder(tmp_path)
    clock = FrozenClock(T)
    sink = case.sink(Allowlist(), SinkRecorder(folder, clock))

    sink.send(case.message("-1001"))
    sink.send(case.message("-1002"))
    clock.advance(timedelta(seconds=2))  # 2026-11-18T00:00:01Z
    sink.send(case.message("-1003"))

    assert sorted(path.name for path in folder.iterdir()) == [
        "2026-11-17.jsonl",
        "2026-11-18.jsonl",
    ]
    assert [json.loads(line)["recipient"] for line in lines(folder)] == ["-1001", "-1002"]
    [next_day] = lines(folder, "2026-11-18")
    assert json.loads(next_day)["time"] == "2026-11-18T00:00:01.000000Z"


def test_the_day_and_time_are_utc_whatever_the_clocks_offset(tmp_path: Path) -> None:
    class OffsetClock:
        def now(self) -> datetime:  # 2026-11-17T23:30:00Z, two hours ahead
            return datetime(2026, 11, 18, 1, 30, tzinfo=timezone(timedelta(hours=2)))

    sink = AlertSink(InMemoryAlert(), Allowlist(), SinkRecorder(tmp_path, OffsetClock()))

    sink.send(AlertMessage("-100123", TEXT))

    [line] = lines(tmp_path)
    assert json.loads(line)["time"] == "2026-11-17T23:30:00.000000Z"


def test_the_folder_is_created_and_the_file_appended(tmp_path: Path) -> None:
    folder = tmp_path / "LocalAppData" / "Nextron" / "test" / "sink"
    recorder = SinkRecorder(folder, FrozenClock(T))
    first = AlertSink(InMemoryAlert(), Allowlist(), recorder)
    second = MailSink(InMemoryMail(), Allowlist(), recorder)

    first.send(AlertMessage("-1001", "one"))
    second.send(MailMessage("a@b.co", "s", "two"))

    assert [json.loads(line)["port"] for line in lines(folder)] == ["alert", "mail"]


def test_lines_are_pure_ascii_so_a_line_separator_cant_split_a_record(tmp_path: Path) -> None:
    text = "café second line third\r\nfourth\x85 \U0001f511"
    sink = MailSink(InMemoryMail(), Allowlist(), SinkRecorder(tmp_path, FrozenClock(T)))

    sink.send(MailMessage("ann@prospect.example", "Grüße", text))

    data = (tmp_path / "2026-11-17.jsonl").read_bytes()
    assert data.isascii()
    assert data.count(b"\n") == 1 and data.endswith(b"\n")
    [line] = data.decode("ascii").splitlines()
    assert json.loads(line)["text"] == text
    assert json.loads(line)["subject"] == "Grüße"


def test_concurrent_records_never_interleave(tmp_path: Path) -> None:
    sinks = [
        AlertSink(InMemoryAlert(), Allowlist(), SinkRecorder(tmp_path, FrozenClock(T)))
        for _ in range(4)
    ]
    big = "x" * 20_000

    def send_many(sink: AlertSink, number: int) -> None:
        for count in range(25):
            sink.send(AlertMessage(f"-{number}{count:03d}", big))

    threads = [
        threading.Thread(target=send_many, args=(sink, number))
        for number, sink in enumerate(sinks, start=1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = [json.loads(line) for line in lines(tmp_path)]
    assert len(records) == 100
    assert all(record["text"] == big for record in records)
    assert len({record["recipient"] for record in records}) == 100


def test_the_allowlist_is_read_on_every_send(case: Case, tmp_path: Path) -> None:
    folder = sink_folder(tmp_path)
    allowlist = Allowlist()
    sink = case.sink(allowlist, SinkRecorder(folder, FrozenClock(T)))

    first = sink.send(case.message("mac@example.com"))
    allowlist.entries.append("mac@example.com")  # Mac saves the Settings page
    second = sink.send(case.message("mac@example.com"))
    allowlist.entries.clear()
    third = sink.send(case.message("mac@example.com"))

    assert (first.sink, second.sink, third.sink) == (True, False, True)
    assert allowlist.calls == 3
    assert len(case.fake.sent) == 1
    assert len(lines(folder)) == 2


def test_a_messages_repr_leaves_out_its_recipient_and_text() -> None:
    alert = AlertMessage("mac@example.com", TEXT)
    mail = MailMessage("ann@prospect.example", SUBJECT, TEXT)

    for shown in (repr(alert), repr(mail), str(alert), str(mail)):
        for hidden in ("mac@example.com", "ann@prospect.example", SUBJECT, TEXT):
            assert hidden not in shown
