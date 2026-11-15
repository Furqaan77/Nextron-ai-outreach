"""Domain layer: pure rules, state machines and value types (AD-1, AD-4, AD-11).

Import rule: the standard library only. Nothing here does I/O; a function that needs the time
takes ``now`` as a UTC-aware ``datetime``. ``zoneinfo`` reads its IANA data from ``tzdata``,
which is a data dependency, not an import.

Enforced by import-linter contract 1 (lowest layer) and by
``tests/contracts/test_domain_stdlib_only.py``.
"""
