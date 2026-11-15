"""Public command API of the ``scoring`` service.

Other services and the delivery layers change ``scoring`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back. Empty until a
story adds a command.
"""
