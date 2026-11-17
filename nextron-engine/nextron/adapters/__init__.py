"""Adapters: implementations of the ports for each external system (AD-9).

Import rule: ``nextron.ports``, ``nextron.domain`` and third-party SDKs. Adapters never import
``nextron.app`` or ``nextron.db`` (contract 3), and only ``nextron.adapters.ai`` imports
``anthropic`` (contract 5).

When you add an adapter subpackage, add it to contract 5's ``source_modules`` in
``pyproject.toml`` and give its port an in-memory fake in ``tests/fakes`` (listed in
``tests.fakes.PORT_FAKES``); ``tests/contracts/test_contract_coverage.py`` fails until contract 5
lists it. Classify it in ``tests/live_guard.py`` too: a live adapter registers the constructor
that reaches its external system (such as ``nextron.adapters.secrets.default_backend``), so no
test can build it, and ``tests/contracts/test_test_isolation.py`` fails until every adapter
package is classified. A ``mail`` or ``alert`` adapter is built only through
``nextron.main.ROLE_ADAPTERS``, which wraps it in the dev and test sink (``adapters.sink``).
"""
