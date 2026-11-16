"""``nextron open`` (``main.open_dashboard``) with fake secrets, a fake browser and a fake
engine probe, plus the real connect probe and the CLI parser. Nothing here opens a browser or
touches Credential Manager."""

import io
import json
import re
import socket
from pathlib import Path

import pytest

from nextron import __main__ as cli
from nextron import main
from nextron.app.ops.api import OPEN_CODE_SECRET
from nextron.bootstrap import ENV_ERROR
from nextron.startup_checks import engine_is_running
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

KEY = ("nextron-engine/dev", OPEN_CODE_SECRET)
URL = re.compile(r"http://127\.0\.0\.1:8766/enter\?t=(?P<code>[A-Za-z0-9_-]{43})")


class Browser:
    """A fake default browser: records each URL and returns ``result`` (or raises it)."""

    def __init__(self, result: bool | BaseException = True) -> None:
        self.result = result
        self.urls: list[str] = []

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class EngineProbe:
    def __init__(self, running: bool) -> None:
        self.running = running
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> bool:
        self.calls.append((host, port))
        return self.running


@pytest.fixture
def dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if ``open_dashboard`` builds the live keyring adapter."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("KeyringSecrets must not be built")

    monkeypatch.setattr(main, "KeyringSecrets", refuse)


def run_open(
    *,
    secrets: InMemorySecrets | None = None,
    running: bool = True,
    browser: Browser | None = None,
) -> tuple[int, str, str, EngineProbe, Browser]:
    probe = EngineProbe(running)
    browser = browser if browser is not None else Browser()
    out, err = io.StringIO(), io.StringIO()
    code = main.open_dashboard(
        secrets=secrets,
        clock=FrozenClock(),
        engine_running=probe,
        browser=browser,
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue(), probe, browser


def test_an_invalid_env_exits_2(no_keyring: None) -> None:
    code, out, err, probe, browser = run_open()

    assert code == 2
    assert err.strip() == ENV_ERROR
    assert probe.calls == [] and browser.urls == []


# Row: open, engine down
@pytest.mark.usefixtures("dev", "no_keyring")
def test_engine_down_exits_1_without_touching_secrets() -> None:
    secrets = InMemorySecrets("dev")

    code, out, err, probe, browser = run_open(secrets=secrets, running=False)

    assert (code, out, err) == (1, "", "engine not running\n")
    assert probe.calls == [("127.0.0.1", 8766)]
    assert secrets.calls == [] and secrets.store == {}
    assert browser.urls == []


@pytest.mark.usefixtures("dev", "no_keyring")
def test_engine_down_builds_no_secrets_object() -> None:
    code, _, err, _, browser = run_open(running=False)

    assert (code, err) == (1, "engine not running\n")
    assert browser.urls == []


# Row: open, engine up
@pytest.mark.usefixtures("dev")
def test_engine_up_stores_a_code_and_opens_the_browser_at_enter() -> None:
    secrets = InMemorySecrets("dev")

    code, out, err, _, browser = run_open(secrets=secrets)

    assert (code, err) == (0, "")
    assert out == "opened the dashboard in your browser; the link works once, within 5 minutes\n"
    [url] = browser.urls
    match = URL.fullmatch(url)
    assert match, url
    stored = json.loads(secrets.store[KEY])
    assert stored == {"code": match["code"], "issued_at": "2026-11-16T09:00:00.000000Z"}
    assert match["code"] not in out + err


@pytest.mark.usefixtures("dev")
@pytest.mark.parametrize(
    "result", [False, OSError("no browser for http://127.0.0.1/enter?t=x")], ids=["false", "raises"]
)
def test_a_browser_that_does_not_open_withdraws_the_code(result: bool | BaseException) -> None:
    secrets = InMemorySecrets("dev")

    code, out, err, _, browser = run_open(secrets=secrets, browser=Browser(result))

    assert (code, out, err) == (1, "", "could not open the default browser\n")
    assert secrets.store == {}
    assert secrets.calls[-1] == ("delete", OPEN_CODE_SECRET)
    [url] = browser.urls
    assert URL.fullmatch(url)["code"] not in out + err  # type: ignore[index]


@pytest.mark.usefixtures("dev")
def test_a_failed_withdrawal_still_reports_the_browser() -> None:
    secrets = InMemorySecrets("dev")
    secrets.failing.add("delete")

    code, _, err, _, _ = run_open(secrets=secrets, browser=Browser(False))

    assert (code, err) == (1, "could not open the default browser\n")


@pytest.mark.usefixtures("dev")
def test_a_code_that_cannot_be_stored_exits_1_without_a_browser() -> None:
    secrets = InMemorySecrets("dev")
    secrets.failing.add("write")

    code, out, err, _, browser = run_open(secrets=secrets)

    assert (code, out) == (1, "")
    assert err == "could not store the one-time code in Windows Credential Manager\n"
    assert browser.urls == []


@pytest.mark.usefixtures("dev")
def test_the_code_goes_to_the_dev_namespace() -> None:
    shared: dict[tuple[str, str], str] = {}

    assert run_open(secrets=InMemorySecrets("dev", shared))[0] == 0

    assert list(shared) == [KEY]


# engine_is_running: connect, don't bind
def test_engine_is_running_when_something_listens() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        assert engine_is_running("127.0.0.1", port) is True


def test_engine_is_not_running_on_a_free_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # nothing listens once the socket is closed

    assert engine_is_running("127.0.0.1", port) is False


def test_engine_probe_connects_with_a_one_second_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[tuple[str, int], float]] = []

    def connect(address: tuple[str, int], timeout: float) -> socket.socket:
        seen.append((address, timeout))
        raise TimeoutError

    monkeypatch.setattr(socket, "create_connection", connect)

    assert engine_is_running("127.0.0.1", 8766) is False
    assert seen == [(("127.0.0.1", 8766), 1.0)]


# The CLI
def test_the_parser_knows_open() -> None:
    assert cli.build_parser().parse_args(["open"]).command == "open"


def test_cli_dispatches_open(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_open(**kwargs: object) -> int:
        calls.append(kwargs)
        return 7

    monkeypatch.setattr(cli.engine, "open_dashboard", fake_open)

    assert cli.main(["open"]) == 7
    assert calls == [{}]
