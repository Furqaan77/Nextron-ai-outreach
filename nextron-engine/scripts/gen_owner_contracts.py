"""Generate import-linter contract group 7 from ``nextron/db/owners.py`` (AD-1, AD-3).

For each owner ``x`` the generated contract forbids ``nextron.app.x`` and
``nextron.app.queries.x`` from importing ``nextron.db.repos.y`` for every other owner ``y``.
The contracts live in ``pyproject.toml`` between the BEGIN and END GENERATED markers.

Usage, from the ``nextron-engine`` folder:

    uv run python scripts/gen_owner_contracts.py           # rewrite the generated block
    uv run python scripts/gen_owner_contracts.py --check   # exit 1 if the block is out of date
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ENGINE_ROOT / "pyproject.toml"

BEGIN_MARKER = (
    "# BEGIN GENERATED: contract group 7. Run scripts/gen_owner_contracts.py; do not edit by hand."
)
END_MARKER = "# END GENERATED: contract group 7"


def load_owners() -> tuple[str, ...]:
    sys.path.insert(0, str(ENGINE_ROOT))
    try:
        from nextron.db.owners import OWNERS
    finally:
        sys.path.remove(str(ENGINE_ROOT))
    return OWNERS


def _toml_list(key: str, values: Sequence[str]) -> list[str]:
    return [f"{key} = [", *(f'    "{value}",' for value in values), "]"]


def render_contracts(owners: Sequence[str]) -> str:
    """Return the generated TOML block, markers included."""
    if len(set(owners)) != len(owners):
        raise ValueError("OWNERS has duplicates")
    lines = [BEGIN_MARKER]
    for owner in owners:
        others = sorted(other for other in owners if other != owner)
        lines += [
            "[[tool.importlinter.contracts]]",
            f'id = "c7-owner-{owner}"',
            f'name = "7 forbidden: {owner} uses only its own repositories"',
            'type = "forbidden"',
            *_toml_list("source_modules", [f"nextron.app.{owner}", f"nextron.app.queries.{owner}"]),
            *_toml_list("forbidden_modules", [f"nextron.db.repos.{other}" for other in others]),
            "allow_indirect_imports = true",
            "",
        ]
    lines.append(END_MARKER)
    return "\n".join(lines)


def splice(pyproject_text: str, block: str) -> str:
    """Replace the text between (and including) the markers with ``block``."""
    start = pyproject_text.find(BEGIN_MARKER)
    end = pyproject_text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"{PYPROJECT.name} lacks the contract group 7 markers")
    return pyproject_text[:start] + block + pyproject_text[end + len(END_MARKER) :]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when pyproject.toml differs from the generated contracts; change nothing",
    )
    args = parser.parse_args(argv)
    current = PYPROJECT.read_text(encoding="utf-8")
    wanted = splice(current, render_contracts(load_owners()))
    if args.check:
        if current != wanted:
            print(
                "contract group 7 in pyproject.toml is out of date with nextron/db/owners.py; "
                "run: uv run python scripts/gen_owner_contracts.py",
                file=sys.stderr,
            )
            return 1
        print("contract group 7 is up to date")
        return 0
    if current != wanted:
        PYPROJECT.write_text(wanted, encoding="utf-8", newline="\n")
        print("rewrote contract group 7 in pyproject.toml")
    else:
        print("contract group 7 is already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
