"""``doctor``: every check as PASS or FAIL, no lock, no server, nothing created."""

import io
from pathlib import Path

import pytest

from nextron import main
from nextron.bootstrap import ENV_ERROR
from nextron.lock import InstanceLock
from tests.fakes.probes import PortProbe, fake_probes

CHECKS = ["sqlite_version", "tzdata", "data_dir_outside_onedrive", "port_free"]


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
        "port_free": "FAIL",
    }


def test_doctor_takes_no_lock(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    with InstanceLock(data_dir / "nextron.lock"):  # a running engine holds it
        code, _ = run_doctor()

    assert code == 0


def test_doctor_with_env_unset_exits_2(local_app_data: Path) -> None:
    err = io.StringIO()

    code = main.doctor(probes=fake_probes(), out=io.StringIO(), err=err)

    assert code == 2
    assert err.getvalue().strip() == ENV_ERROR
    assert list(local_app_data.iterdir()) == []


def test_cli_dispatches_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron import __main__ as cli

    monkeypatch.setattr(cli.engine, "doctor", lambda: 1)

    assert cli.main(["doctor"]) == 1
