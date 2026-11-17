"""Read API of the ``playbook`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.playbook`` only. All read through the
current unit of work, read-only ones included.

- ``active_tuple(route) -> RouteTuple | None`` (FR-71, AD-8, Story 2.8): the tuple ``route``
  runs. It reads the route's active activation and the prompt snapshot it names, and builds the
  ``RouteTuple`` from the activation's model, effort, prompt version and schema version, with
  ``system_prompt`` set to the **snapshot's** text, so the gateway never sees the live prompt
  file. It raises ``ValueError`` when the snapshot is missing or its text's SHA-256 isn't the
  activation's ``prompt_sha256`` (a database edited by hand), and ``TypeError`` for a route that
  isn't a ``str``. It returns ``None`` when the route has no active activation (an unknown route
  included): the caller skips the call and records why. Stories 2.9, 2.10 and 2.16 call it
  before every extraction, and record ``tuple.actor`` (``ai:<route>@<prompt_version>``) on what
  they store.
"""

from nextron.db.repos.playbook import activations as activations_repo
from nextron.db.repos.playbook import snapshots as snapshots_repo
from nextron.domain.ai import Effort, RouteTuple

__all__ = ["active_tuple"]


def active_tuple(route: str) -> RouteTuple | None:
    """The pinned tuple of ``route``, with the snapshot's text, or ``None``."""
    if not isinstance(route, str):
        raise TypeError("route must be a str")
    activation = activations_repo.active(route)
    if activation is None:
        return None
    where = f"{activation.route} {activation.prompt_version}"
    snapshot = snapshots_repo.get(activation.route, activation.prompt_version)
    if snapshot is None:
        raise ValueError(f"the active tuple of {where} names a prompt snapshot that isn't stored")
    route_tuple = RouteTuple(
        route=activation.route,
        model=activation.model,
        effort=Effort(activation.effort),
        prompt_version=activation.prompt_version,
        schema_version=activation.schema_version,
        system_prompt=snapshot.text,
    )
    if not route_tuple.prompt_sha256 == snapshot.sha256 == activation.prompt_sha256:
        raise ValueError(f"the stored prompt of {where} doesn't match its SHA-256")
    return route_tuple
