"""Repositories of the ``sequencing`` service: the only writers of its tables (AD-3).

Only ``nextron.app.sequencing`` and ``nextron.app.queries.sequencing`` import this package (contract
group 7). ``enrollments`` writes the ``enrollments`` table and ``variant_counters`` the
``variant_counters`` table (Story 4.8).
"""
