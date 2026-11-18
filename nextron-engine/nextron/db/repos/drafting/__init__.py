"""Repositories of the ``drafting`` service: the only writers of its tables (AD-3).

Only ``nextron.app.drafting`` and ``nextron.app.queries.drafting`` import this package (contract
group 7).

- ``footer_snapshots`` (Story 4.11): each distinct footer text of a Market, keyed by its
  SHA-256, with ``ensure`` (reuse on identical content) and ``by_id``; no update or delete.
- ``drafts`` (Story 4.9): the Draft slots' content group.
- ``revisions`` (Story 4.12): each Draft's immutable text revisions; ``insert`` and reads, and
  only the erasure's ``tombstone`` besides.
"""
