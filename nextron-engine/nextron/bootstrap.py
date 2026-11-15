"""Bootstrap configuration (AD-12, AD-14). Composition-root helper: only ``nextron.main`` uses it.

pydantic-settings reads exactly five environment variables and nothing else (no ``.env`` file):

==========================  ===============================================================
Variable                    Default
==========================  ===============================================================
``NEXTRON_ENV``             none: required, one of ``dev``, ``test``, ``prod``
``NEXTRON_DATA_DIR``        ``%LOCALAPPDATA%\\Nextron\\<env>``
``NEXTRON_BACKUP_DIR``      ``%LOCALAPPDATA%\\Nextron\\backups\\<env>``
``NEXTRON_PORT``            8765 (``prod``), 8766 (``dev``), 8767 (``test``)
``NEXTRON_PLAYBOOK_PATH``   ``../docs/playbook/sales-playbook.md``
==========================  ===============================================================

Path values expand ``%VAR%``, ``$VAR`` and ``~``; relative paths then resolve against the engine
project directory (the folder holding ``pyproject.toml``). Every other setting lives in the
``settings`` table (Story 1.6).
"""

import os
from pathlib import Path
from typing import Any, Final, Literal, get_args

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "test", "prod"]
ENVIRONMENTS: Final[tuple[str, ...]] = get_args(Env)
DEFAULT_PORTS: Final[dict[str, int]] = {"prod": 8765, "dev": 8766, "test": 8767}
DEFAULT_PLAYBOOK_PATH: Final = Path("..") / "docs" / "playbook" / "sales-playbook.md"
ENGINE_ROOT: Final = Path(__file__).resolve().parent.parent

ENV_ERROR: Final = "NEXTRON_ENV must be set to dev, test or prod"


class BootstrapError(Exception):
    """The bootstrap configuration is missing or invalid; the message is safe to print."""


def local_app_data() -> Path:
    """Return ``%LOCALAPPDATA%``, falling back to ``~/AppData/Local`` when it is unset."""
    value = os.environ.get("LOCALAPPDATA")
    return Path(value) if value else Path.home() / "AppData" / "Local"


class BootstrapConfig(BaseSettings):
    """The only configuration read before the database exists."""

    model_config = SettingsConfigDict(
        env_prefix="NEXTRON_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    env: Env
    data_dir: Path
    backup_dir: Path
    port: int = Field(ge=1, le=65535)
    playbook_path: Path

    @model_validator(mode="before")
    @classmethod
    def _apply_environment_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict) or data.get("env") not in ENVIRONMENTS:
            return data
        env = data["env"]
        nextron_dir = local_app_data() / "Nextron"
        defaults = {
            "data_dir": nextron_dir / env,
            "backup_dir": nextron_dir / "backups" / env,
            "port": DEFAULT_PORTS[env],
            "playbook_path": DEFAULT_PLAYBOOK_PATH,
        }
        return defaults | data

    @field_validator("data_dir", "backup_dir", "playbook_path")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        expanded = Path(os.path.expanduser(os.path.expandvars(value)))
        path = expanded if expanded.is_absolute() else ENGINE_ROOT / expanded
        return Path(os.path.normpath(path))


def load_bootstrap() -> BootstrapConfig:
    """Read the bootstrap configuration from the environment.

    Raises ``BootstrapError`` with ``ENV_ERROR`` when ``NEXTRON_ENV`` is unset or invalid, and
    with a short summary for any other invalid value. Creates no file or directory.
    """
    try:
        return BootstrapConfig()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as exc:
        errors = exc.errors()
        if any(error["loc"][:1] == ("env",) for error in errors):
            raise BootstrapError(ENV_ERROR) from None
        problems = "; ".join(
            f"NEXTRON_{str(error['loc'][0]).upper() if error['loc'] else 'CONFIG'}: "
            f"{error['msg']}"
            for error in errors
        )
        raise BootstrapError(f"invalid bootstrap configuration: {problems}") from None
