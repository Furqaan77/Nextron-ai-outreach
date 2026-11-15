"""Read APIs: one module per owner (AD-3).

Other services read an owner's data only through ``nextron.app.queries.<owner>``. Every service
may import every queries module, so this package is the bottom layer of the app DAG.

Import rule for ``nextron.app.queries.<owner>``: ``nextron.domain`` and that owner's own
``nextron.db.repos.<owner>`` only.

Enforced by contracts today: queries import no other ``nextron.app`` module (contract 6), and
``nextron.app.queries.<owner>`` imports no other owner's repositories (contract group 7).

Convention, not yet enforced: no other imports from ``nextron.ports`` or ``nextron.db``.
"""
