"""Ports: the Protocols through which app services reach the outside world (AD-9).

Import rule: ``nextron.domain`` and the standard library only. Adapters in ``nextron.adapters``
implement these Protocols, ``nextron.main`` injects them, and every port has an in-memory fake
in ``tests/fakes``.
"""
