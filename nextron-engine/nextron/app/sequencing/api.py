"""Public command API of the ``sequencing`` service.

Other services and the delivery layers change ``sequencing`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back. Empty until a
story adds a command.
"""
