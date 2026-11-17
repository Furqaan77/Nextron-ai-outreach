"""``GoogleOAuthClient`` on ``httpx2.MockTransport`` and ``LocalClientFiles`` on ``tmp_path``
(``nextron.adapters.google_oauth``): the exact form fields, every status mapping, no response
text in any error, the lazy default client (refused in tests) and the file rules. Nothing here
reaches Google."""

import logging
import os
import stat
from pathlib import Path
from urllib.parse import parse_qsl

import httpx2
import pytest

import nextron.adapters.google_oauth as adapter
from nextron.adapters.google_oauth import GoogleOAuthClient, LocalClientFiles
from nextron.domain.google_oauth import REVOKE_URI, TOKEN_URI, ClientConfig
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import (
    CLIENT_FILE_MISSING,
    CLIENT_FILE_NOT_DELETED,
    CLIENT_FILE_NOT_REGULAR,
    CLIENT_FILE_TOO_LARGE,
    ClientFilePort,
    GoogleOAuthPort,
    TokenSet,
)
from tests.fakes.google_oauth import CLIENT_ID
from tests.live_guard import LiveAdapterInTests, allow_live_constructor

SECRET = "Cs4Vq8Lp2Xz6Nw1Rt5Hy"
CODE = "Ac7Wm3Kq9Jz2Pv5Tx8Ns"
VERIFIER = "Vf2Bn6Qr9Lk3Hs7Wd1Zm" * 3
REFRESH = "Rt8Gh2Mc5Vb9Xq3Nz7Lp"
ACCESS = "At4Yk8Ws2Pd6Fj9Hc3Qv"
LEAK = "Gx5Ld8Rb2Tw7Mk4Ns9Vy"  # text in a response body that must never reach an error
REDIRECT = "http://127.0.0.1:53682/"
CLIENT = ClientConfig(CLIENT_ID, SECRET)


class Recorder:
    """A ``MockTransport`` handler that records each request and answers ``response``."""

    def __init__(self, response: httpx2.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def form(self) -> list[tuple[str, str]]:
        [request] = self.requests
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        return parse_qsl(request.content.decode("ascii"), keep_blank_values=True)


def client_on(recorder: Recorder) -> GoogleOAuthClient:
    return GoogleOAuthClient(httpx2.Client(transport=httpx2.MockTransport(recorder)))


def tokens_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "access_token": ACCESS,
        "expires_in": 3599,
        "scope": "https://www.googleapis.com/auth/gmail.send openid email",
        "token_type": "Bearer",
        "refresh_token": REFRESH,
        "id_token": "a.b.c",
    }
    body.update(overrides)
    return {name: value for name, value in body.items() if value is not None}


def ok(body: object) -> httpx2.Response:
    return httpx2.Response(200, json=body)


def assert_no_leak(error: AdapterError) -> None:
    for text in (str(error), repr(error), error.message):
        for value in (LEAK, SECRET, CODE, VERIFIER, REFRESH, ACCESS, TOKEN_URI, REVOKE_URI):
            assert value not in text, (value, text)
    # no chained exception carries the response or its text into a logged traceback
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__


# The port and the lazy default ------------------------------------------------------------------


def test_the_adapters_implement_their_ports() -> None:
    assert isinstance(GoogleOAuthClient(), GoogleOAuthPort)
    assert isinstance(LocalClientFiles(), ClientFilePort)


def test_the_default_http_client_is_built_on_the_first_call_and_refused_in_tests() -> None:
    client = GoogleOAuthClient()  # builds nothing yet

    with pytest.raises(LiveAdapterInTests):
        client.revoke(REFRESH)
    with pytest.raises(LiveAdapterInTests):
        client.refresh(CLIENT, REFRESH)


def test_the_default_http_client_has_a_15_second_timeout_and_follows_no_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[dict[str, object]] = []

    class Spy:
        def __init__(self, **kwargs: object) -> None:
            built.append(kwargs)

    monkeypatch.setattr(adapter.httpx2, "Client", Spy)
    allow_live_constructor(monkeypatch, "nextron.adapters.google_oauth", "default_http_client")

    adapter.default_http_client()

    assert built == [{"timeout": 15.0, "follow_redirects": False}]


# The form fields ----------------------------------------------------------------------------------


def test_exchange_posts_exactly_these_fields() -> None:
    recorder = Recorder(ok(tokens_body()))

    tokens = client_on(recorder).exchange(CLIENT, CODE, VERIFIER, REDIRECT)

    [request] = recorder.requests
    assert (request.method, str(request.url)) == ("POST", TOKEN_URI)
    assert recorder.form() == [
        ("code", CODE),
        ("client_id", CLIENT_ID),
        ("client_secret", SECRET),
        ("redirect_uri", REDIRECT),
        ("grant_type", "authorization_code"),
        ("code_verifier", VERIFIER),
    ]
    assert tokens == TokenSet(
        access_token=ACCESS,
        expires_in=3599,
        scope="https://www.googleapis.com/auth/gmail.send openid email",
        refresh_token=REFRESH,
        id_token="a.b.c",
    )
    for value in (ACCESS, REFRESH, "a.b.c"):
        assert value not in repr(tokens)


def test_refresh_posts_exactly_these_fields() -> None:
    recorder = Recorder(ok(tokens_body(refresh_token=None, id_token=None)))

    tokens = client_on(recorder).refresh(CLIENT, REFRESH)

    [request] = recorder.requests
    assert (request.method, str(request.url)) == ("POST", TOKEN_URI)
    assert recorder.form() == [
        ("refresh_token", REFRESH),
        ("client_id", CLIENT_ID),
        ("client_secret", SECRET),
        ("grant_type", "refresh_token"),
    ]
    assert (tokens.access_token, tokens.refresh_token, tokens.id_token) == (ACCESS, None, None)


def test_revoke_posts_only_the_token() -> None:
    recorder = Recorder(httpx2.Response(200))

    client_on(recorder).revoke(REFRESH)

    [request] = recorder.requests
    assert (request.method, str(request.url)) == ("POST", REVOKE_URI)
    assert recorder.form() == [("token", REFRESH)]


def test_no_request_follows_a_redirect() -> None:
    recorder = Recorder(httpx2.Response(302, headers={"location": "https://evil.example/"}))

    with pytest.raises(AdapterError) as caught:
        client_on(recorder).refresh(CLIENT, REFRESH)

    assert len(recorder.requests) == 1
    assert (caught.value.code, caught.value.retryable) == ("google_bad_response", False)


# Status mappings ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx2.Response(400, json={"error": "invalid_grant", "error_description": LEAK}), "google_invalid_grant", False),
        (httpx2.Response(401, json={"error": "invalid_grant"}), "google_invalid_grant", False),
        (httpx2.Response(400, json={"error": "invalid_request", "error_description": LEAK}), "google_rejected", False),
        (httpx2.Response(401, json={"error": "invalid_client"}), "google_rejected", False),
        (httpx2.Response(403, text=LEAK), "google_rejected", False),
        (httpx2.Response(404, text=LEAK), "google_rejected", False),
        (httpx2.Response(429, text=LEAK), "google_unavailable", True),
        (httpx2.Response(500, text=LEAK), "google_unavailable", True),
        (httpx2.Response(503, json={"error": "invalid_grant"}), "google_unavailable", True),
        (httpx2.Response(301, text=LEAK), "google_bad_response", False),
        (httpx2.Response(200, text=LEAK), "google_bad_response", False),
        (httpx2.Response(200, json=[LEAK]), "google_bad_response", False),
        (ok(tokens_body(access_token="")), "google_bad_response", False),
        (ok(tokens_body(access_token=None)), "google_bad_response", False),
        (ok(tokens_body(access_token=7)), "google_bad_response", False),
        (ok(tokens_body(expires_in=0)), "google_bad_response", False),
        (ok(tokens_body(expires_in=86_401)), "google_bad_response", False),
        (ok(tokens_body(expires_in=10**400)), "google_bad_response", False),
        (ok(tokens_body(expires_in="3599")), "google_bad_response", False),
        (ok(tokens_body(expires_in=True)), "google_bad_response", False),
        (ok(tokens_body(expires_in=35.5)), "google_bad_response", False),
        (ok(tokens_body(expires_in=None)), "google_bad_response", False),
    ],
)
@pytest.mark.parametrize("operation", ["exchange", "refresh"])
def test_every_token_answer_maps_to_its_code_without_its_text(
    response: httpx2.Response, code: str, retryable: bool, operation: str
) -> None:
    client = client_on(Recorder(response))

    with pytest.raises(AdapterError) as caught:
        if operation == "exchange":
            client.exchange(CLIENT, CODE, VERIFIER, REDIRECT)
        else:
            client.refresh(CLIENT, REFRESH)

    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert_no_leak(caught.value)


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ConnectError(LEAK),
        httpx2.ReadTimeout(LEAK),
        httpx2.ConnectTimeout(LEAK),
        httpx2.RemoteProtocolError(LEAK),
        OSError(LEAK),
    ],
    ids=["connect", "read_timeout", "connect_timeout", "protocol", "os"],
)
@pytest.mark.parametrize("operation", ["exchange", "refresh", "revoke"])
def test_a_failed_request_is_google_unavailable_and_retryable(
    failure: Exception, operation: str
) -> None:
    client = client_on(Recorder(failure))

    with pytest.raises(AdapterError) as caught:
        if operation == "exchange":
            client.exchange(CLIENT, CODE, VERIFIER, REDIRECT)
        elif operation == "refresh":
            client.refresh(CLIENT, REFRESH)
        else:
            client.revoke(REFRESH)

    assert (caught.value.code, caught.value.retryable) == ("google_unavailable", True)
    assert_no_leak(caught.value)


def test_a_lifetime_of_a_day_is_the_longest_accepted() -> None:
    tokens = client_on(Recorder(ok(tokens_body(expires_in=86_400)))).refresh(CLIENT, REFRESH)

    assert tokens.expires_in == 86_400


def test_the_optional_fields_are_kept_only_as_non_empty_strings() -> None:
    body = tokens_body(scope=None, refresh_token="", id_token=7)

    tokens = client_on(Recorder(ok(body))).exchange(CLIENT, CODE, VERIFIER, REDIRECT)

    assert (tokens.scope, tokens.refresh_token, tokens.id_token) == ("", None, None)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200),
        httpx2.Response(200, json={}),
        httpx2.Response(400, json={"error": "invalid_token", "error_description": LEAK}),
    ],
)
def test_revoke_takes_200_and_an_invalid_token_400_as_done(response: httpx2.Response) -> None:
    client_on(Recorder(response)).revoke(REFRESH)


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx2.Response(400, json={"error": "invalid_request"}), "google_rejected", False),
        (httpx2.Response(401, json={"error": "invalid_token"}), "google_rejected", False),
        (httpx2.Response(400, text=LEAK), "google_rejected", False),
        (httpx2.Response(429), "google_unavailable", True),
        (httpx2.Response(502, text=LEAK), "google_unavailable", True),
    ],
)
def test_any_other_revoke_answer_fails(
    response: httpx2.Response, code: str, retryable: bool
) -> None:
    with pytest.raises(AdapterError) as caught:
        client_on(Recorder(response)).revoke(REFRESH)

    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert_no_leak(caught.value)


def test_the_adapter_logs_nothing_that_holds_a_token_or_a_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    for response in (
        ok(tokens_body()),
        httpx2.Response(400, json={"error": "invalid_grant"}),
        httpx2.Response(500, text=LEAK),
    ):
        client = client_on(Recorder(response))
        for call in (
            lambda: client.exchange(CLIENT, CODE, VERIFIER, REDIRECT),
            lambda: client.refresh(CLIENT, REFRESH),
            lambda: client.revoke(REFRESH),
        ):
            try:
                call()
            except AdapterError:
                pass

    assert [r for r in caplog.records if r.name.startswith("nextron")] == []
    for record in caplog.records:
        text = f"{record.getMessage()} {vars(record)!r}"
        for value in (SECRET, CODE, VERIFIER, REFRESH, ACCESS, LEAK):
            assert value not in text


# LocalClientFiles -------------------------------------------------------------------------------


def test_a_regular_file_is_read_whole(tmp_path: Path) -> None:
    path = tmp_path / "client_secret.json"
    path.write_bytes(b'{"installed": {}}')

    assert LocalClientFiles().read(str(path), 65_536) == b'{"installed": {}}'


def test_a_file_of_exactly_the_limit_is_read_and_one_byte_more_is_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "client_secret.json"
    path.write_bytes(b"x" * 100)

    assert LocalClientFiles().read(str(path), 100) == b"x" * 100
    with pytest.raises(AdapterError) as caught:
        LocalClientFiles().read(str(path), 99)
    assert caught.value.code == CLIENT_FILE_TOO_LARGE


def test_a_missing_file_or_folder_is_refused(tmp_path: Path) -> None:
    files = LocalClientFiles()

    with pytest.raises(AdapterError) as missing:
        files.read(str(tmp_path / "nope.json"), 100)
    with pytest.raises(AdapterError) as missing_folder:
        files.read(str(tmp_path / "no" / "nope.json"), 100)
    (tmp_path / "folder.json").mkdir()
    with pytest.raises(AdapterError) as folder:
        files.read(str(tmp_path / "folder.json"), 100)

    assert missing.value.code == missing_folder.value.code == CLIENT_FILE_MISSING
    assert folder.value.code == CLIENT_FILE_NOT_REGULAR
    for error in (missing.value, missing_folder.value, folder.value):
        assert str(tmp_path) not in str(error)
        assert error.retryable is False


def test_a_link_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_bytes(b"{}")
    link = tmp_path / "link.json"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("this account may not create symbolic links")

    with pytest.raises(AdapterError) as caught:
        LocalClientFiles().read(str(link), 100)

    assert caught.value.code == CLIENT_FILE_NOT_REGULAR


def test_a_reparse_point_is_refused_even_when_it_looks_like_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction, an app-execution alias or a OneDrive placeholder is a reparse point; only
    ``st_file_attributes`` says so."""
    path = tmp_path / "client_secret.json"
    path.write_bytes(b"{}")
    seen = os.lstat(path)
    reparse = os.stat_result(
        tuple(seen)[:10], {"st_file_attributes": stat.FILE_ATTRIBUTE_REPARSE_POINT}
    )
    assert stat.S_ISREG(reparse.st_mode)
    monkeypatch.setattr(adapter.os, "lstat", lambda name: reparse)

    with pytest.raises(AdapterError) as caught:
        LocalClientFiles().read(str(path), 100)

    assert caught.value.code == CLIENT_FILE_NOT_REGULAR


def test_a_file_swapped_between_lstat_and_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "client_secret.json"
    path.write_bytes(b"{}")
    other = tmp_path / "other.json"
    other.write_bytes(b"{}")
    real_fstat = os.fstat
    other_fd = os.open(other, os.O_RDONLY)
    try:
        # the file opened is another one than lstat saw
        monkeypatch.setattr(adapter.os, "fstat", lambda fd: real_fstat(other_fd))
        with pytest.raises(AdapterError) as caught:
            LocalClientFiles().read(str(path), 100)
    finally:
        monkeypatch.undo()
        os.close(other_fd)

    assert caught.value.code == CLIENT_FILE_NOT_REGULAR


def test_delete_removes_the_file_and_a_missing_one_is_fine(tmp_path: Path) -> None:
    path = tmp_path / "client_secret.json"
    path.write_bytes(b"{}")

    LocalClientFiles().delete(str(path))
    LocalClientFiles().delete(str(path))

    assert not path.exists()


def test_a_delete_that_fails_says_so_without_the_path(tmp_path: Path) -> None:
    folder = tmp_path / "folder.json"
    folder.mkdir()

    with pytest.raises(AdapterError) as caught:
        LocalClientFiles().delete(str(folder))

    assert caught.value.code == CLIENT_FILE_NOT_DELETED
    assert str(tmp_path) not in str(caught.value)

