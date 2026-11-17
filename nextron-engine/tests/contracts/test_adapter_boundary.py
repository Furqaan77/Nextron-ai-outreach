"""The adapter boundary at run time (AD-9, the *Errors* convention, NFR-18): whatever an HTTP
transport raises or answers, only ``AdapterError`` comes out of a port call, and no planted
secret is in the error.

- **Every HTTP adapter is driven.** Every class under ``nextron.adapters`` whose ``__init__``
  takes an ``http`` client (``TelegramAlert`` and ``GoogleOAuthClient`` today) has a driver in
  ``DRIVERS``: it builds the adapter on a client whose transport is the probe's, with secrets
  planted where the adapter reads them, and makes each of its port calls. The coverage check
  fails for a new such class until it gets one.
- **Only AdapterError.** The transport raises each ``httpx2`` exception class, ``OSError``,
  ``ssl.SSLError``, ``ValueError`` and ``RuntimeError``, and answers 200 with garbage, 400,
  401, 403, 404, 429, 500 and 503, every message and body holding the planted secrets. Each
  call either returns or raises an ``AdapterError``; anything else is a problem.
- **No secret in the error.** No planted secret appears in the error's ``str``, its ``repr``
  or its formatted traceback (its ``__cause__`` and ``__context__`` chain included, as logging
  would print it).
- **Every request is metered (FR-57).** Each such class takes a required keyword-only
  ``meter`` (the coverage check), and each attempted request records exactly one ``ApiCall``
  on the driver's ``InMemoryUsageMeter``, whatever the probe did: ``ok`` when the call returned,
  else ``ok=False`` with the ``AdapterError``'s code, recorded before it was raised.

The proofs run the same checks on probe adapters that break each rule.
"""

import inspect
import pkgutil
import ssl
import traceback
import types
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Final

import httpx2
import pytest

import nextron.adapters
from nextron.adapters.google_oauth import GoogleOAuthClient
from nextron.adapters.telegram import TelegramAlert
from nextron.domain.budget import ApiCall
from nextron.domain.google_oauth import ClientConfig
from nextron.ports.alert import AlertMessage
from nextron.ports.errors import AdapterError
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.usage_meter import InMemoryUsageMeter

# Planted secrets: 20 letters and digits each, which no redaction rule masks, so only their
# absence passes. The bot token has the shape Telegram's has.
BOT_TOKEN: Final = "1234567890:Qm7Zr2Vx9Lk4Tw8Np3Hs"
CLIENT_SECRET: Final = "Cs4Vq8Lp2Xz6Nw1Rt5Hy"
AUTH_CODE: Final = "Ac7Wm3Kq9Jz2Pv5Tx8Ns"
REFRESH_TOKEN: Final = "Rt8Gh2Mc5Vb9Xq3Nz7Lp"
REVOKED_TOKEN: Final = "Rv3Jd7Ks1Wq5Pz9Lm2Xc"
PLANTED: Final = (
    BOT_TOKEN,
    BOT_TOKEN.split(":")[1],
    CLIENT_SECRET,
    AUTH_CODE,
    REFRESH_TOKEN,
    REVOKED_TOKEN,
)
CHAT: Final = "-1001234567890"
ALERT_TEXT: Final = "Job dead: send_alert 01JD3QZ8X5V9K2M7N4P6R8T0WY, attempts: 6"
LEAK: Final = " ".join(PLANTED)  # what every transport error and response body carries

type Transport = Callable[[httpx2.Request], httpx2.Response]
type Call = Callable[[], object]
type Driver = Callable[[httpx2.Client, InMemoryUsageMeter], Iterable[tuple[str, Call]]]


# The transports ---------------------------------------------------------------------------------


def _exception(cls: type[BaseException], request: httpx2.Request) -> BaseException:
    """An instance of ``cls`` whose message holds every planted secret."""
    parameters = inspect.signature(cls.__init__).parameters
    message = f"failed: {LEAK} {request.url}"
    if "response" in parameters:
        response = httpx2.Response(500, text=LEAK, request=request)
        return cls(message, request=request, response=response)  # type: ignore[call-arg]
    if "request" in parameters:
        return cls(message, request=request)  # type: ignore[call-arg]
    takes_text = "message" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values()
    )
    return cls(message) if takes_text else cls()  # type: ignore[call-arg]


def httpx2_exceptions() -> list[type[BaseException]]:
    """Every exception class ``httpx2`` exports."""
    return [
        value
        for name in dir(httpx2)
        if isinstance(value := getattr(httpx2, name), type) and issubclass(value, BaseException)
    ]


RAISED: Final[list[type[BaseException]]] = [
    *httpx2_exceptions(),
    OSError,
    ssl.SSLError,
    ValueError,
    RuntimeError,
]
# The httpx2 exceptions that take no argument at all, so carry no text.
NO_TEXT: Final = frozenset(
    {httpx2.RequestNotRead, httpx2.ResponseNotRead, httpx2.StreamClosed, httpx2.StreamConsumed}
)
GARBAGE: Final = b"\x00\xff<html>" + LEAK.encode() + b"</html>"
STATUSES: Final = (400, 401, 403, 404, 429, 500, 503)


@dataclass(frozen=True)
class Probe:
    """One way the transport behaves: ``label`` names it in a problem."""

    label: str
    transport: Transport


def _raising(cls: type[BaseException]) -> Transport:
    def transport(request: httpx2.Request) -> httpx2.Response:
        raise _exception(cls, request)

    return transport


def _answering(status: int, body: bytes) -> Transport:
    def transport(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, content=body, request=request)

    return transport


def probes() -> list[Probe]:
    found = [Probe(f"raises {cls.__name__}", _raising(cls)) for cls in RAISED]
    found.append(Probe("answers 200 garbage", _answering(200, GARBAGE)))
    for status in STATUSES:
        body = (
            b'{"ok":false,"error":"invalid_grant","description":"' + LEAK.encode() + b'"}'
        )
        found.append(Probe(f"answers {status}", _answering(status, body)))
    return found


def client_on(transport: Transport) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(transport))


# The drivers ------------------------------------------------------------------------------------


def telegram_calls(http: httpx2.Client, meter: InMemoryUsageMeter) -> Iterator[tuple[str, Call]]:
    secrets = InMemorySecrets()
    secrets.write("telegram/bot_token", BOT_TOKEN)
    adapter = TelegramAlert(secrets, meter=meter, http=http)
    yield "send", lambda: adapter.send(AlertMessage(CHAT, ALERT_TEXT))


def google_calls(http: httpx2.Client, meter: InMemoryUsageMeter) -> Iterator[tuple[str, Call]]:
    adapter = GoogleOAuthClient(meter=meter, http=http)
    client = ClientConfig("1234-abc.apps.googleusercontent.com", CLIENT_SECRET)
    verifier = "Vf2Bn6Qr9Lk3Hs7Wd1Zm" * 3
    redirect = "http://127.0.0.1:53682/"
    yield "exchange", lambda: adapter.exchange(client, AUTH_CODE, verifier, redirect)
    yield "refresh", lambda: adapter.refresh(client, REFRESH_TOKEN)
    yield "revoke", lambda: adapter.revoke(REVOKED_TOKEN)


# Every adapter class whose __init__ takes an http client, with its driver.
DRIVERS: Final[dict[type, Driver]] = {
    TelegramAlert: telegram_calls,
    GoogleOAuthClient: google_calls,
}


# The checks -------------------------------------------------------------------------------------


def adapter_modules() -> list[types.ModuleType]:
    """Every module under ``nextron.adapters``, imported."""
    return [
        nextron.adapters,
        *(
            __import__(info.name, fromlist=["_"])
            for info in pkgutil.walk_packages(nextron.adapters.__path__, "nextron.adapters.")
        ),
    ]


def http_adapter_classes(modules: Iterable[types.ModuleType]) -> set[type]:
    """Each class defined in ``modules`` whose ``__init__`` takes an ``http`` parameter."""
    found = set()
    for module in modules:
        for value in vars(module).values():
            if not (isinstance(value, type) and value.__module__ == module.__name__):
                continue
            try:
                parameters = inspect.signature(value.__init__).parameters
            except (TypeError, ValueError):
                continue
            if "http" in parameters:
                found.add(value)
    return found


def unmetered_classes(classes: Iterable[type]) -> set[str]:
    """Each class whose ``__init__`` doesn't take a required keyword-only ``meter``."""
    found = set()
    for cls in classes:
        meter = inspect.signature(cls.__init__).parameters.get("meter")
        if (
            meter is None
            or meter.kind is not inspect.Parameter.KEYWORD_ONLY
            or meter.default is not inspect.Parameter.empty
        ):
            found.add(cls.__name__)
    return found


def shown(error: BaseException) -> str:
    """Everything a log record or a stored error could show of ``error``."""
    return "\n".join([str(error), repr(error), *traceback.format_exception(error)])


def boundary_problems(driver: Driver, planted: Iterable[str] = PLANTED) -> list[str]:
    """Each probe and call that lets anything but ``AdapterError`` out, or a planted secret
    into the error."""
    problems = []
    secrets = tuple(planted)
    for probe in probes():
        for name, call in driver(client_on(probe.transport), InMemoryUsageMeter()):
            try:
                call()
            except AdapterError as error:
                text = shown(error)
                if any(secret in text for secret in secrets):
                    problems.append(f"{name}, {probe.label}: a planted secret is in the error")
            except BaseException as error:  # noqa: BLE001 - what the boundary must stop
                problems.append(f"{name}, {probe.label}: {type(error).__name__} came out")
    return problems


def metering_problems(driver: Driver) -> list[str]:
    """Each probe and call that didn't record exactly one ``ApiCall`` for its one request, or
    recorded the wrong outcome: ``ok`` when it returned, else the raised error's code."""
    problems = []
    for probe in probes():
        requests: list[httpx2.Request] = []

        def transport(request: httpx2.Request, probe: Probe = probe) -> httpx2.Response:
            requests.append(request)
            return probe.transport(request)

        meter = InMemoryUsageMeter()
        for name, call in driver(client_on(transport), meter):
            before, sent = len(meter.records), len(requests)
            try:
                call()
                expected: tuple[bool, str | None] = (True, None)
            except AdapterError as error:
                expected = (False, error.code)
            except BaseException:  # noqa: BLE001 - boundary_problems reports these
                continue
            recorded = meter.calls[before:]
            label = f"{name}, {probe.label}"
            if len(requests) - sent != 1:
                problems.append(f"{label}: {len(requests) - sent} requests were made")
            if len(recorded) != 1 or not isinstance(recorded[0], ApiCall):
                problems.append(f"{label}: {len(recorded)} calls were recorded")
            elif (recorded[0].ok, recorded[0].error_code) != expected:
                problems.append(f"{label}: the recorded outcome is not the call's")
    return problems


def test_every_http_adapter_has_a_driver_and_takes_a_meter() -> None:
    classes = http_adapter_classes(adapter_modules())

    assert classes == set(DRIVERS)
    assert unmetered_classes(classes) == set()


@pytest.mark.parametrize("adapter", list(DRIVERS), ids=lambda cls: cls.__name__)
def test_only_adapter_errors_without_a_secret_come_out(adapter: type) -> None:
    assert boundary_problems(DRIVERS[adapter]) == []


@pytest.mark.parametrize("adapter", list(DRIVERS), ids=lambda cls: cls.__name__)
def test_every_attempted_request_records_exactly_one_api_call(adapter: type) -> None:
    assert metering_problems(DRIVERS[adapter]) == []


def test_the_probes_cover_the_specified_failures_and_answers() -> None:
    labels = [probe.label for probe in probes()]

    for name in (
        "ConnectError",
        "ReadTimeout",
        "RemoteProtocolError",
        "HTTPStatusError",
        "StreamClosed",
        "InvalidURL",
        "OSError",
        "SSLError",
        "ValueError",
        "RuntimeError",
    ):
        assert f"raises {name}" in labels
    assert [label for label in labels if label.startswith("answers")] == [
        "answers 200 garbage",
        *(f"answers {status}" for status in STATUSES),
    ]
    assert len(RAISED) >= 30  # every httpx2 exception class, and the four others


def test_every_raised_exception_carries_the_planted_secrets_unless_it_takes_no_argument() -> None:
    request = httpx2.Request("POST", "https://api.example.com/x")
    built = {cls: _exception(cls, request) for cls in RAISED}

    assert {cls for cls, error in built.items() if LEAK not in str(error)} == NO_TEXT
    for cls in (OSError, ssl.SSLError, ValueError, RuntimeError, httpx2.ConnectError):
        assert LEAK in str(built[cls])


# The proofs -------------------------------------------------------------------------------------

LEAKY_ADAPTERS: Final = '''
import httpx2

from nextron.domain.budget import ApiCall
from nextron.ports.errors import AdapterError


class Unwrapped:
    """Lets the transport's exception out."""

    def __init__(self, *, http):
        self._http = http

    def call(self):
        response = self._http.post("https://api.example.com/x")
        return response.status_code


class Chatty:
    """Wraps it, but puts its text in the message."""

    def __init__(self, token, *, http):
        self._http = http
        self._token = token

    def call(self):
        try:
            self._http.post(f"https://api.example.com/bot{self._token}/x")
        except Exception as exc:
            raise AdapterError(True, "probe_failed", "the call failed: " + str(exc)) from None


class Chained:
    """Its own text, but the transport's exception chained as the cause."""

    def __init__(self, *, http):
        self._http = http

    def call(self):
        try:
            self._http.post("https://api.example.com/x")
        except Exception as exc:
            raise AdapterError(True, "probe_failed", "the call failed") from exc


class Clean:
    """What the real adapters do."""

    def __init__(self, *, meter, http=None):
        self._meter = meter
        self._http = http

    def call(self):
        try:
            try:
                self._http.post("https://api.example.com/x")
            except Exception:
                raise AdapterError(True, "probe_failed", "the call failed") from None
        except AdapterError as failed:
            self._meter.record(ApiCall("probe", "call", ok=False, error_code=failed.code))
            raise
        self._meter.record(ApiCall("probe", "call", ok=True))


class Unmetered:
    """Records nothing."""

    def __init__(self, *, meter=None, http=None):
        self._http = http

    def call(self):
        try:
            self._http.post("https://api.example.com/x")
        except Exception:
            raise AdapterError(True, "probe_failed", "the call failed") from None


class OnlySuccesses:
    """Records only the calls that return."""

    def __init__(self, meter, *, http=None):
        self._meter = meter
        self._http = http

    def call(self):
        try:
            self._http.post("https://api.example.com/x")
        except Exception:
            raise AdapterError(True, "probe_failed", "the call failed") from None
        self._meter.record(ApiCall("probe", "call", ok=True))


class AlwaysOk:
    """Records every request as a success, failures included."""

    def __init__(self, *, meter, http=None):
        self._meter = meter
        self._http = http

    def call(self):
        self._meter.record(ApiCall("probe", "call", ok=True))
        try:
            self._http.post("https://api.example.com/x")
        except Exception:
            raise AdapterError(True, "probe_failed", "the call failed") from None


class Twice:
    """Records each request twice, the second time as a success."""

    def __init__(self, *, meter, http=None):
        self._meter = meter
        self._http = http

    def call(self):
        self._meter.record(ApiCall("probe", "call", ok=True))
        try:
            self._http.post("https://api.example.com/x")
        except Exception:
            raise AdapterError(True, "probe_failed", "the call failed") from None
        self._meter.record(ApiCall("probe", "call", ok=True))


class NoClient:
    def __init__(self, secrets):
        self._secrets = secrets
'''


def probe_module() -> types.ModuleType:
    module = types.ModuleType("nextron.adapters.probe_http")
    exec(compile(LEAKY_ADAPTERS, "<probe_http>", "exec"), module.__dict__)  # noqa: S102
    return module


def test_the_coverage_check_finds_every_class_that_takes_http() -> None:
    module = probe_module()

    found = {cls.__name__ for cls in http_adapter_classes([module])}

    assert found == {
        "Unwrapped",
        "Chatty",
        "Chained",
        "Clean",
        "Unmetered",
        "OnlySuccesses",
        "AlwaysOk",
        "Twice",
    }


def test_the_meter_coverage_check_finds_every_class_without_a_required_keyword_meter() -> None:
    module = probe_module()

    found = unmetered_classes(http_adapter_classes([module]))

    assert found == {"Unwrapped", "Chatty", "Chained", "Unmetered", "OnlySuccesses"}


def test_the_boundary_check_catches_an_exception_let_out() -> None:
    module = probe_module()

    def driver(http: httpx2.Client, meter: InMemoryUsageMeter) -> Iterator[tuple[str, Call]]:
        yield "call", module.Unwrapped(http=http).call

    problems = boundary_problems(driver)

    assert "call, raises ConnectError: ConnectError came out" in problems
    assert "call, raises OSError: OSError came out" in problems
    assert not [problem for problem in problems if "answers" in problem]  # it returns


@pytest.mark.parametrize("name", ["Chatty", "Chained"])
def test_the_boundary_check_catches_a_secret_in_the_error(name: str) -> None:
    module = probe_module()

    def driver(http: httpx2.Client, meter: InMemoryUsageMeter) -> Iterator[tuple[str, Call]]:
        if name == "Chatty":
            yield "call", module.Chatty(BOT_TOKEN, http=http).call
        else:
            yield "call", module.Chained(http=http).call

    problems = boundary_problems(driver)

    assert "call, raises ConnectError: a planted secret is in the error" in problems
    assert not [problem for problem in problems if "came out" in problem]


def test_the_boundary_check_passes_a_clean_adapter() -> None:
    module = probe_module()

    def driver(http: httpx2.Client, meter: InMemoryUsageMeter) -> Iterator[tuple[str, Call]]:
        yield "call", module.Clean(meter=meter, http=http).call

    assert boundary_problems(driver) == []
    assert metering_problems(driver) == []


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("Unmetered", "call, raises ConnectError: 0 calls were recorded"),
        ("OnlySuccesses", "call, raises ConnectError: 0 calls were recorded"),
        ("Twice", "call, answers 200 garbage: 2 calls were recorded"),
        ("Twice", "call, raises ConnectError: the recorded outcome is not the call's"),
    ],
)
def test_the_metering_check_catches_a_request_recorded_wrongly(name: str, problem: str) -> None:
    module = probe_module()

    def driver(http: httpx2.Client, meter: InMemoryUsageMeter) -> Iterator[tuple[str, Call]]:
        cls = getattr(module, name)
        if name == "OnlySuccesses":  # its meter is positional
            yield "call", cls(meter, http=http).call
        else:
            yield "call", cls(meter=meter, http=http).call

    problems = metering_problems(driver)

    assert problem in problems


def test_the_metering_check_catches_a_wrong_outcome() -> None:
    module = probe_module()

    def driver(http: httpx2.Client, meter: InMemoryUsageMeter) -> Iterator[tuple[str, Call]]:
        yield "call", module.AlwaysOk(meter=meter, http=http).call

    problems = metering_problems(driver)

    assert "call, raises ConnectError: the recorded outcome is not the call's" in problems
    assert not [problem for problem in problems if "answers" in problem]  # it returns


def test_the_metering_check_catches_a_request_never_made() -> None:
    def driver(http: httpx2.Client, meter: InMemoryUsageMeter) -> Iterator[tuple[str, Call]]:
        yield "call", lambda: meter.record(ApiCall("probe", "call", ok=True))

    problems = metering_problems(driver)

    assert "call, answers 400: 0 requests were made" in problems
