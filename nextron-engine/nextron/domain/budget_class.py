"""Budget classes (AD-9, Story 8.1). Standard library only.

``BudgetClass`` says whether the budget may stop a piece of work: ``blockable`` (discovery,
enrichment, re-checks and their AI routes, plus AI Test Set runs) or ``essential`` (everything
else: sending, replies, unsubscribe polling, daily selection and re-contacts). Every
``nextron.domain.job.JobType`` declares one, ``essential`` by default; the budget never blocks
``essential`` work (``nextron.domain.budget.allow``).

It lives in its own module so ``nextron.domain.job`` can use it without importing
``nextron.domain.budget`` (which imports ``nextron.domain.health``, which imports the job
module); ``nextron.domain.budget`` re-exports it.
"""

from enum import StrEnum


class BudgetClass(StrEnum):
    """Whether the budget may stop a piece of work."""

    BLOCKABLE = "blockable"  # discovery, enrichment, re-checks and their AI routes
    ESSENTIAL = "essential"  # sending, replies, unsubscribe polling: never blocked
