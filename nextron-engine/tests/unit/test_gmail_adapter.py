"""The Gmail adapter (``nextron.adapters.gmail``, Story 6.2): the live constructor is refused in
tests, ``error_for_status`` maps each ``HttpError`` status as a pure function, and
``GmailProfiles.get_profile`` runs over injected fake service objects: the address, every
error row, one ``ApiCall`` per request, no request for a token it refuses, and one service
object per thread and token. No test reaches Google."""

import threading
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from nextron.adapters import gmail
from nextron.adapters.gmail import GmailProfiles, error_for_status
from nextron.domain.budget import ApiCall
from nextron.ports.errors import AdapterError
from nextron.ports.gmail import (
    GMAIL_BAD_RESPONSE,
    GMAIL_UNAUTHORIZED,
    GMAIL_UNAVAILABLE,
    GmailPort,
)
from tests.fakes.gmail import FakeGmail
from tests.fakes.usage_meter import InMemoryUsageMeter
from tests.live_guard import LiveAdapterInTests

TOKEN = "ya29.a0Fake-Access_Token.123"
ADDRESS = "furqaan@nextron-mail.com"


class _Request:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.retries: list[int] = []

    def execute(self, num_retries: int = 0) -> object:
        self.retries.append(num_retries)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Users:
    def __init__(self, service: "FakeService") -> None:
        self._service = service

    def getProfile(self, userId: str) -> _Request:  # noqa: N802 - Google's name
        self._service.user_ids.append(userId)
        return _Request(self._service.outcome)


class FakeService:
    """A Gmail service object whose ``users().getProfile().execute()`` answers ``outcome``."""

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.user_ids: list[str] = []

    def users(self) -> _Users:
        return _Users(self)


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": str(status)}), b"{}")


def profiles(
    outcome: object, built: list[str] | None = None
) -> tuple[GmailProfiles, InMemoryUsageMeter]:
    meter = InMemoryUsageMeter()

    def service(token: str) -> FakeService:
        if built is not None:
            built.append(token)
        return FakeService(outcome)

    return GmailProfiles(meter=meter, service=service), meter


def test_the_adapter_and_the_fake_satisfy_the_port() -> None:
    adapter, _ = profiles({"emailAddress": ADDRESS})
    assert isinstance(adapter, GmailPort)
    assert isinstance(FakeGmail(), GmailPort)


def test_the_live_service_constructor_is_refused_in_tests() -> None:
    with pytest.raises(LiveAdapterInTests):
        gmail.default_service(TOKEN)
    meter = InMemoryUsageMeter()
    with pytest.raises(AdapterError) as caught:  # looked up at the first read, not at build
        GmailProfiles(meter=meter).get_profile(TOKEN)
    assert isinstance(caught.value.__context__, LiveAdapterInTests)  # the guard refused it
    assert caught.value.code == GMAIL_UNAVAILABLE
    assert meter.calls == [ApiCall("gmail", "get_profile", ok=False, error_code=GMAIL_UNAVAILABLE)]


def test_a_failure_to_build_the_service_is_mapped_metered_and_holds_no_token() -> None:
    meter = InMemoryUsageMeter()

    def broken(token: str) -> FakeService:
        raise ValueError(f"bad credentials {token}")

    adapter = GmailProfiles(meter=meter, service=broken)
    with pytest.raises(AdapterError) as caught:
        adapter.get_profile(TOKEN)

    assert (caught.value.retryable, caught.value.code) == (True, GMAIL_UNAVAILABLE)
    assert caught.value.__suppress_context__
    assert TOKEN not in str(caught.value)
    assert meter.calls == [ApiCall("gmail", "get_profile", ok=False, error_code=GMAIL_UNAVAILABLE)]


def test_get_profile_answers_the_address_and_meters_one_call() -> None:
    adapter, meter = profiles({"emailAddress": ADDRESS, "messagesTotal": 3})

    assert adapter.get_profile(TOKEN) == ADDRESS
    assert meter.calls == [ApiCall("gmail", "get_profile", ok=True)]


@pytest.mark.parametrize(
    ("status", "retryable", "code"),
    [
        (401, False, GMAIL_UNAUTHORIZED),
        (403, False, GMAIL_UNAUTHORIZED),
        (429, True, GMAIL_UNAVAILABLE),
        (500, True, GMAIL_UNAVAILABLE),
        (503, True, GMAIL_UNAVAILABLE),
        (599, True, GMAIL_UNAVAILABLE),
        (400, False, GMAIL_BAD_RESPONSE),
        (404, False, GMAIL_BAD_RESPONSE),
        (302, False, GMAIL_BAD_RESPONSE),
        (0, False, GMAIL_BAD_RESPONSE),
    ],
)
def test_error_for_status_maps_each_status(status: int, retryable: bool, code: str) -> None:
    error = error_for_status(status)
    assert (error.retryable, error.code) == (retryable, code)
    assert str(status) in error.message


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, GMAIL_UNAUTHORIZED), (503, GMAIL_UNAVAILABLE), (404, GMAIL_BAD_RESPONSE)],
)
def test_an_http_error_becomes_its_adapter_error_and_is_metered(status: int, code: str) -> None:
    adapter, meter = profiles(http_error(status))

    with pytest.raises(AdapterError) as caught:
        adapter.get_profile(TOKEN)

    assert caught.value.code == code
    assert caught.value.__suppress_context__  # raised from None: no SDK text rides along
    assert meter.calls == [ApiCall("gmail", "get_profile", ok=False, error_code=code)]


def test_any_other_failure_is_retryable_unavailable() -> None:
    adapter, meter = profiles(OSError("connection reset by 10.0.0.1"))

    with pytest.raises(AdapterError) as caught:
        adapter.get_profile(TOKEN)

    assert (caught.value.retryable, caught.value.code) == (True, GMAIL_UNAVAILABLE)
    assert "10.0.0.1" not in caught.value.message


@pytest.mark.parametrize(
    "answer",
    [{}, {"emailAddress": ""}, {"emailAddress": 7}, [ADDRESS], ADDRESS, None],
)
def test_an_answer_without_an_address_is_a_bad_response(answer: object) -> None:
    adapter, _ = profiles(answer)

    with pytest.raises(AdapterError) as caught:
        adapter.get_profile(TOKEN)

    assert (caught.value.retryable, caught.value.code) == (False, GMAIL_BAD_RESPONSE)


@pytest.mark.parametrize("token", ["", "with space", "x" * 4097, None, "naïve"])
def test_an_unusable_token_is_refused_before_any_request(token: Any) -> None:
    built: list[str] = []
    adapter, meter = profiles({"emailAddress": ADDRESS}, built)

    with pytest.raises(AdapterError) as caught:
        adapter.get_profile(token)

    assert caught.value.code == GMAIL_BAD_RESPONSE
    assert built == [] and meter.calls == []


def test_no_message_holds_the_token_or_the_address() -> None:
    adapter, _ = profiles(http_error(401))
    with pytest.raises(AdapterError) as caught:
        adapter.get_profile(TOKEN)
    assert TOKEN not in str(caught.value) and ADDRESS not in str(caught.value)


def test_one_service_object_per_thread_and_token() -> None:
    built: list[str] = []
    adapter, _ = profiles({"emailAddress": ADDRESS}, built)

    adapter.get_profile(TOKEN)
    adapter.get_profile(TOKEN)  # the same thread and token: reused
    adapter.get_profile("ya29.other")  # a new token: rebuilt
    thread = threading.Thread(target=adapter.get_profile, args=("ya29.other",))
    thread.start()
    thread.join()

    assert built == [TOKEN, "ya29.other", "ya29.other"]  # the other thread built its own
