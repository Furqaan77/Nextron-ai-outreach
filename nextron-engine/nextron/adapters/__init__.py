"""Adapters: implementations of the ports for each external system (AD-9).

Import rule: ``nextron.ports``, ``nextron.domain`` and third-party SDKs. Adapters never import
``nextron.app`` or ``nextron.db`` (contract 3), and only ``nextron.adapters.ai`` imports
``anthropic`` (contract 5).

When you add an adapter subpackage, add it to contract 5's ``source_modules`` in
``pyproject.toml`` and give its port an in-memory fake in ``tests/fakes``;
``tests/contracts/test_contract_coverage.py`` fails until contract 5 lists it.
"""
