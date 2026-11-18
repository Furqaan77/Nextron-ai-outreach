"""The Reply vocabulary (FR-40, Sales Playbook §5, Story 4.6). Standard library only.

``ReplyClass`` is the ten Glossary Reply Classes: ``interested``, ``question``, ``objection``,
``referral``, ``not_now``, ``not_interested``, ``unsubscribe``, ``data_question``,
``out_of_office`` and ``other``. An *Objection* carries an ``ObjectionType``: one of the
Playbook §5 objections, ``send_information`` ("Send me some information"), ``no_budget`` and
``uses_other_tool`` ("We already use [tool]"). The Playbook's "Not a priority now" row is the
*Not Now* class, not an objection: its own response sets the Lead to Not Now (Story 4.6,
decision 2).

The Reply set (``nextron.domain.ai_test_sets``) labels its cases with these values, and Story
7.5's classifier schema uses them.
"""

from enum import StrEnum


class ReplyClass(StrEnum):
    """A Reply Class (FR-40)."""

    INTERESTED = "interested"
    QUESTION = "question"
    OBJECTION = "objection"
    REFERRAL = "referral"
    NOT_NOW = "not_now"
    NOT_INTERESTED = "not_interested"
    UNSUBSCRIBE = "unsubscribe"
    DATA_QUESTION = "data_question"
    OUT_OF_OFFICE = "out_of_office"
    OTHER = "other"


class ObjectionType(StrEnum):
    """The type of an *Objection* Reply (Sales Playbook §5)."""

    SEND_INFORMATION = "send_information"
    NO_BUDGET = "no_budget"
    USES_OTHER_TOOL = "uses_other_tool"
