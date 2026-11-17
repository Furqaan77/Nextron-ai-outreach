"""Read API of the ``pipeline`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.pipeline`` only. All read through the
current unit of work, read-only ones included.

- ``stage(lead_id)``: the Lead's ``PipelineStage``, or ``None`` when no Lead has that id (a
  text that isn't a ULID included).
"""

from nextron.db.repos.pipeline import stages as stages_repo
from nextron.domain.ids import is_ulid
from nextron.domain.pipeline_stage import PipelineStage


def stage(lead_id: str) -> PipelineStage | None:
    """The Pipeline Stage of the Lead ``lead_id``, or ``None``."""
    if not is_ulid(lead_id):
        return None
    stored = stages_repo.stage(lead_id)
    return None if stored is None else PipelineStage(stored)
