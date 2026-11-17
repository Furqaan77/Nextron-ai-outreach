"""Ports: the Protocols through which app services reach the outside world (AD-9).

Import rule: ``nextron.domain`` and the standard library only. Adapters in ``nextron.adapters``
implement these Protocols, ``nextron.main`` injects them, and every port has an in-memory fake
in ``tests/fakes``. ``UsageMeterPort`` (``usage_meter``) is the exception: ``nextron.db.metering``
implements it, because metering writes the database, and every HTTP adapter takes one.

``LlmPort`` (``llm``) is the one way to call Claude: ``run(route_tuple, inputs, urgency)``
returns a typed ``LlmResult`` (ok, refused, truncated, invalid or failed) carrying the call's
metered usage. Its values live in ``nextron.domain.ai``; ``nextron.adapters.ai`` implements it.
"""
