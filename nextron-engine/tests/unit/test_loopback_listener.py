"""``LoopbackListener`` (``nextron.adapters.google_oauth``) on real loopback sockets: the first
GET of ``/`` with the right ``Host`` is recorded, other paths get 404 and a wrong ``Host`` 400,
later requests 404; a timeout gives ``None``; ``close`` is idempotent and stops the port; and
nothing reaches stderr or the logs (the request line holds the authorization code)."""

import http.client
import logging
import socket
import threading
from collections.abc import Iterator

import pytest

import nextron.adapters.google_oauth as adapter
from nextron.adapters.google_oauth import RECEIVED_PAGE, LoopbackListener
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import ConsentListener

CODE = "Ac7Wm3Kq9Jz2Pv5Tx8Ns"
STATE = "St3Lm8Qw2Zn6Vb9Hx4Ck"


@pytest.fixture
def listener() -> Iterator[LoopbackListener]:
    opened = LoopbackListener()
    try:
        yield opened
    finally:
        opened.close()


def send(
    listener: LoopbackListener,
    target: str,
    *,
    host: str | None = None,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    """Send one request to ``listener`` with exactly this request target and ``Host``."""
    connection = http.client.HTTPConnection("127.0.0.1", listener.port, timeout=5)
    try:
        connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", host if host is not None else f"127.0.0.1:{listener.port}")
        connection.endheaders()
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


def test_it_implements_the_port_on_an_ephemeral_loopback_port(listener: LoopbackListener) -> None:
    assert isinstance(listener, ConsentListener)
    assert listener.port > 0
    assert listener.redirect_uri() == f"http://127.0.0.1:{listener.port}/"


def test_the_first_request_to_slash_is_recorded_and_answered_with_the_fixed_page(
    listener: LoopbackListener,
) -> None:
    status, headers, body = send(listener, f"/?state={STATE}&code={CODE}&scope=a%20b&blank=")

    assert status == 200
    assert body == RECEIVED_PAGE
    assert b"Nextron received Google's answer. You can close this tab and return to the " in body
    assert CODE.encode() not in body and STATE.encode() not in body
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert headers["content-security-policy"] == (
        "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert dict(listener.wait(1)) == {  # type: ignore[arg-type]
        "state": (STATE,),
        "code": (CODE,),
        "scope": ("a b",),
        "blank": ("",),
    }


def test_a_repeated_field_keeps_every_value(listener: LoopbackListener) -> None:
    send(listener, "/?state=a&state=b&error=access_denied")

    assert dict(listener.wait(1)) == {  # type: ignore[arg-type]
        "state": ("a", "b"),
        "error": ("access_denied",),
    }


@pytest.mark.parametrize(
    "target", ["/callback?code=x", "/favicon.ico", "//?code=x/..", "/x/?code=x", "*"]
)
def test_any_other_path_gets_404_and_is_not_recorded(
    target: str, listener: LoopbackListener
) -> None:
    status, headers, body = send(listener, target)

    assert status in (400, 404)
    assert headers.get("cache-control", "no-store") == "no-store"
    assert b"code" not in body
    assert listener.wait(0.05) is None


@pytest.mark.parametrize(
    "host", ["localhost:{port}", "127.0.0.1", "127.0.0.1:1", "evil.example", "127.0.0.1:{port}x"]
)
def test_a_wrong_host_gets_400_and_is_not_recorded(host: str, listener: LoopbackListener) -> None:
    status, _, _ = send(listener, "/?code=x&state=y", host=host.format(port=listener.port))

    assert status == 400
    assert listener.wait(0.05) is None


def test_a_post_is_not_recorded(listener: LoopbackListener) -> None:
    status, _, _ = send(listener, "/?code=x&state=y", method="POST")

    assert status == 501
    assert listener.wait(0.05) is None


def test_later_requests_get_404_and_the_first_answer_stays(listener: LoopbackListener) -> None:
    send(listener, "/?code=first&state=s")

    status, _, body = send(listener, "/?code=second&state=s")

    assert (status, body) == (404, b"Not found\n")
    assert dict(listener.wait(1))["code"] == ("first",)  # type: ignore[index]


def test_more_than_20_fields_are_recorded_as_an_empty_answer(listener: LoopbackListener) -> None:
    send(listener, "/?" + "&".join(f"f{n}=x" for n in range(21)))

    assert dict(listener.wait(1)) == {}  # type: ignore[arg-type]


def test_twenty_fields_are_recorded(listener: LoopbackListener) -> None:
    send(listener, "/?" + "&".join(f"f{n}=x" for n in range(20)))

    assert len(listener.wait(1)) == 20  # type: ignore[arg-type]


def test_a_timeout_gives_none(listener: LoopbackListener) -> None:
    assert listener.wait(0.1) is None


def test_close_is_idempotent_wakes_a_waiter_and_stops_the_port() -> None:
    listener = LoopbackListener()
    port = listener.port
    answers: list[object] = []
    waiter = threading.Thread(target=lambda: answers.append(listener.wait(30)))
    waiter.start()

    listener.close()
    waiter.join(timeout=5)
    listener.close()

    assert not waiter.is_alive() and answers == [None]
    assert listener.wait(0) is None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_an_answer_that_came_before_close_is_still_returned() -> None:
    listener = LoopbackListener()
    send(listener, "/?code=x&state=y")
    listener.close()

    assert dict(listener.wait(0)) == {"code": ("x",), "state": ("y",)}  # type: ignore[arg-type]


def test_two_listeners_get_different_ports() -> None:
    first, second = LoopbackListener(), LoopbackListener()
    try:
        assert first.port != second.port
    finally:
        first.close()
        second.close()


def test_a_bind_failure_is_listener_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: object) -> None:
        raise OSError(10048, "address in use: 127.0.0.1")

    monkeypatch.setattr(adapter._ConsentServer, "server_bind", refuse)

    with pytest.raises(AdapterError) as caught:
        LoopbackListener()

    assert (caught.value.code, caught.value.retryable) == ("listener_failed", False)
    assert "address in use" not in str(caught.value)


def test_nothing_reaches_stderr_or_the_logs(
    capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    listener = LoopbackListener()
    try:
        send(listener, f"/?code={CODE}&state={STATE}")
        send(listener, f"/?code={CODE}&state={STATE}")  # 404
        send(listener, f"/other?code={CODE}", host="evil.example")  # 400
        send(listener, f"/?code={CODE}", method="PUT")  # 501
        with socket.create_connection(("127.0.0.1", listener.port), timeout=5) as raw:
            raw.sendall(f"BAD LINE /?code={CODE}\r\n\r\n".encode())  # a malformed request
            raw.recv(4096)
        with socket.create_connection(("127.0.0.1", listener.port), timeout=5):
            pass  # a connection that sends nothing
    finally:
        listener.close()

    captured = capfd.readouterr()
    assert captured.err == "" and captured.out == ""
    for record in caplog.records:
        assert CODE not in f"{record.getMessage()} {vars(record)!r}"
