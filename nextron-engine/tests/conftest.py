"""Shared pytest setup.

- The ``ai_eval`` marker is opt-in: its tests are skipped unless ``-m`` selects ``ai_eval``.
- Every test runs with no ``NEXTRON_*`` variables and an empty temporary ``%LOCALAPPDATA%``, so
  nothing reads the real environment or writes under the real ``%LOCALAPPDATA%\\Nextron``.
"""

import os
from pathlib import Path

import pytest

AI_EVAL = "ai_eval"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if AI_EVAL in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="ai_eval is opt-in: run pytest -m ai_eval")
    for item in items:
        if AI_EVAL in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def local_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty ``%LOCALAPPDATA%`` and no ``NEXTRON_*`` variables; returns the folder."""
    for name in list(os.environ):
        if name.upper().startswith("NEXTRON_"):
            monkeypatch.delenv(name)
    folder = tmp_path / "LocalAppData"
    folder.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(folder))
    return folder
