"""Repositories of the ``sending`` service: the only writers of its tables (AD-3).

Only ``nextron.app.sending`` and ``nextron.app.queries.sending`` import this package (contract
group 7).

- ``domains``: the ``sending_domains`` table (Story 4.7);
- ``inboxes``: the ``sending_inboxes`` table (Story 4.7);
- ``checks``: the ``domain_checks`` table (Story 6.5), append-only;
- ``outbound_messages``: the ``outbound_messages`` table (Story 5.2): the copy and snapshot
  fixed, only the status, its times and the erasure tombstone change.
"""
