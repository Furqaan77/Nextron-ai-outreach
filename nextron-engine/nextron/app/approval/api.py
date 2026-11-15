"""Public command API of the ``approval`` service.

Other services and the delivery layers change ``approval`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back. Empty until a
story adds a command.
"""
