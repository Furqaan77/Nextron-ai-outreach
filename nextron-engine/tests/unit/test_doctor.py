"""``doctor``: every check as PASS or FAIL, no lock, no server, nothing created."""

import io
from pathlib import Path

import pytest

from nextron import main
from nextron.bootstrap import ENV_ERROR
from nextron.lock import InstanceLock
from tests.fakes.probes import PortProbe, fake_probes

CHECKS = [
    "sqlite_version",
    "tzdata",
    "data_dir_outside_onedrive",
    "prod_env_not_persistent",
    "port_free",
]


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "data" / "dev"
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(folder))

    def no_server(*args: object, **kwargs: object) -> None:
        raise AssertionError("doctor must not start a server")

    monkeypatch.setattr(main.uvicorn, "run", no_server)
    return folder


def run_doctor(**probe_overrides: object) -> tuple[int, list[str]]:
    out = io.StringIO()
    code = main.doctor(probes=fake_probes(**probe_overrides), out=out, err=io.StringIO())
    return code, out.getvalue().splitlines()


def test_all_pass_exits_0_and_creates_nothing(data_dir: Path) -> None:
    code, lines = run_doctor()

    assert code == 0
    assert [line.split()[:2] for line in lines] == [["PASS", name] for name in CHECKS]
    assert not data_dir.exists()


def test_a_failure_is_printed_and_exits_1(data_dir: Path) -> None:
    code, lines = run_doctor(sqlite_version="3.40.0", port=PortProbe(free=False))

    assert code == 1
    statuses = {line.split()[1]: line.split()[0] for line in lines}
    assert statuses == {
        "sqlite_version": "FAIL",
        "tzdata": "PASS",
        "data_dir_outside_onedrive": "PASS",
        "prod_env_not_persistent": "PASS",
        "port_free": "FAIL",
    }


def _raising(name: str) -> dict[str, str]:
    raise PermissionError("registry access denied")


# Row: Doctor (a persistent NEXTRON_ENV)
@pytest.mark.parametrize(
    ("persistent", "status", "code"),
    [
        (lambda name: {"user": "prod"}, "FAIL", 1),
        (lambda name: {"machine": "PROD "}, "FAIL", 1),
        (lambda name: {"user": "dev"}, "PASS", 0),
        (lambda name: {}, "PASS", 0),
        (_raising, "FAIL", 1),
    ],
    ids=["user-prod", "machine-PROD", "only-dev", "none", "probe-raises"],
)
def test_a_persistent_prod_env_fails_doctor(
    persistent: object, status: str, code: int, data_dir: Path
) -> None:
    exit_code, lines = run_doctor(persistent_variable=persistent)

    assert exit_code == code
    statuses = {line.split()[1]: line.split()[0] for line in lines}
    assert statuses == {name: "PASS" for name in CHECKS} | {"prod_env_not_persistent": status}
    [line] = [line for line in lines if line.split()[1] == "prod_env_not_persistent"]
    if status == "FAIL" and persistent is not _raising:
        assert "NEXTRON_ENV=prod must only be set by the logon task" in line
    assert not data_dir.exists()


def test_doctor_asks_only_for_nextron_env(data_dir: Path) -> None:
    asked: list[str] = []

    def persistent(name: str) -> dict[str, str]:
        asked.append(name)
        return {}

    assert run_doctor(persistent_variable=persistent)[0] == 0
    assert asked == ["NEXTRON_ENV"]


def test_serve_does_not_check_the_persistent_env(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing a persistent value at ``serve`` would also stop the logon task's own ``prod``
    start, which inherits it."""
    started: list[bool] = []

    def persistent(name: str) -> dict[str, str]:
        raise AssertionError("serve must not read the persistent environment")

    code = main.serve(
        probes=fake_probes(persistent_variable=persistent),
        run_server=lambda config, reload, units: started.append(True),
        err=io.StringIO(),
    )

    assert code == 0 and started == [True]


def test_doctor_takes_no_lock(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    with InstanceLock(data_dir / "nextron.lock"):  # a running engine holds it
        code, _ = run_doctor()

    assert code == 0


def test_doctor_with_env_unset_exits_2(
    local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEXTRON_ENV")
    err = io.StringIO()

    code = main.doctor(probes=fake_probes(), out=io.StringIO(), err=err)

    assert code == 2
    assert err.getvalue().strip() == ENV_ERROR
    assert list(local_app_data.iterdir()) == []


def test_cli_dispatches_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron import __main__ as cli

    monkeypatch.setattr(cli.engine, "doctor", lambda: 1)

    assert cli.main(["doctor"]) == 1
