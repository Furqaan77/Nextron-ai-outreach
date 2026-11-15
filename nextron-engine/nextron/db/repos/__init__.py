"""Per-owner repository packages (AD-3).

``nextron.db.repos.<owner>`` is the only code that writes ``<owner>``'s tables. Contract group 7
forbids every other owner's commands and queries from importing it.
"""
