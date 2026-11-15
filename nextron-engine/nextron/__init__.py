"""Nextron Outreach Engine: a hexagonal modular monolith (architecture spine, AD-1).

Layers from the bottom up, and what each may import:

- ``nextron.domain``: the standard library only.
- ``nextron.ports``: domain.
- ``nextron.db``: domain, SQLAlchemy, python-ulid.
- ``nextron.app``: domain, ports, the unit of work and event log, its own repositories,
  any ``nextron.app.queries.*`` module and the ``api`` of owners below it in the app DAG.
- ``nextron.adapters``: ports, domain and third-party SDKs (``anthropic`` only in
  ``nextron.adapters.ai``).
- ``nextron.web`` and ``nextron.jobs``: ``nextron.app.*.api``, ``nextron.app.queries.*`` and
  domain types.
- ``nextron.main`` and ``nextron.__main__``: the composition root; may import everything.
  ``nextron.bootstrap``, ``nextron.startup_checks`` and ``nextron.lock`` are its helpers.

The import-linter contracts in ``pyproject.toml`` enforce the boundaries between these packages
and ``tests/contracts`` runs them; each package docstring says which of its rules are enforced
today and which are still conventions.
"""

from importlib import metadata

try:
    __version__ = metadata.version("nextron-engine")
except metadata.PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+unknown"
