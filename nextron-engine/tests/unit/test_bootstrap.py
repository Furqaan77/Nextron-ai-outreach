"""Bootstrap configuration (AD-14): five variables, per-environment defaults, no default env."""

import sys
from pathlib import Path

import pytest

from nextron.bootstrap import (
    ENGINE_ROOT,
    ENV_ERROR,
    BootstrapConfig,
    BootstrapError,
    load_bootstrap,
)

PLAYBOOK = ENGINE_ROOT.parent / "docs" / "playbook" / "sales-playbook.md"


def test_reads_only_the_five_bootstrap_settings() -> None:
    assert set(BootstrapConfig.model_fields) == {
        "env",
        "data_dir",
        "backup_dir",
        "port",
        "playbook_path",
    }
    assert BootstrapConfig.model_config["env_prefix"] == "NEXTRON_"
    assert BootstrapConfig.model_config.get("env_file") is None


@pytest.mark.parametrize(
    ("env", "port"),
    [("prod", 8765), ("dev", 8766), ("test", 8767)],
)
def test_defaults_per_environment(
    env: str, port: int, local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", env)

    config = load_bootstrap()

    assert config.env == env
    assert config.port == port
    assert config.data_dir == local_app_data / "Nextron" / env
    assert config.backup_dir == local_app_data / "Nextron" / "backups" / env
    assert config.playbook_path == PLAYBOOK


def test_default_playbook_is_the_repository_playbook() -> None:
    assert PLAYBOOK.is_file()


@pytest.mark.parametrize("value", [None, "staging", "DEV", "prod "])
def test_env_unset_or_invalid_is_refused(
    value: str | None, local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if value is None:
        monkeypatch.delenv("NEXTRON_ENV")
    else:
        monkeypatch.setenv("NEXTRON_ENV", value)

    with pytest.raises(BootstrapError) as raised:
        load_bootstrap()

    assert str(raised.value) == ENV_ERROR
    assert list(local_app_data.iterdir()) == []


def test_each_setting_can_be_overridden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NEXTRON_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("NEXTRON_PORT", "9123")
    monkeypatch.setenv("NEXTRON_PLAYBOOK_PATH", str(tmp_path / "playbook.md"))

    config = load_bootstrap()

    assert config.data_dir == tmp_path / "data"
    assert config.backup_dir == tmp_path / "backups"
    assert config.port == 9123
    assert config.playbook_path == tmp_path / "playbook.md"


def test_relative_paths_resolve_against_the_engine_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", "var/data")

    assert load_bootstrap().data_dir == ENGINE_ROOT / "var" / "data"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(
            r"%LOCALAPPDATA%\Custom",
            marks=pytest.mark.skipif(sys.platform != "win32", reason="%VAR% is Windows syntax"),
        ),
        "${LOCALAPPDATA}/Custom",
    ],
)
def test_path_settings_expand_environment_variables(
    value: str, local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DATA_DIR", value)

    assert load_bootstrap().data_dir == local_app_data / "Custom"


def test_path_settings_expand_the_home_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_BACKUP_DIR", "~/nextron-backups")

    assert load_bootstrap().backup_dir == Path.home() / "nextron-backups"


def test_empty_values_fall_back_to_defaults(
    local_app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_PORT", "")

    assert load_bootstrap().port == 8766


def test_unknown_nextron_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_DAILY_CAP", "999")

    assert "daily_cap" not in load_bootstrap().model_dump()


@pytest.mark.parametrize("port", ["0", "70000", "http"])
def test_invalid_port_names_the_variable(port: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    monkeypatch.setenv("NEXTRON_PORT", port)

    with pytest.raises(BootstrapError, match="NEXTRON_PORT"):
        load_bootstrap()


def test_config_is_immutable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")
    config = load_bootstrap()

    with pytest.raises(ValueError):
        config.port = 1  # type: ignore[misc]
