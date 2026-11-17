"""In-memory fakes, one per port (AD-9), plus fakes for the startup-check probes.

``PORT_FAKES`` maps every port Protocol in ``nextron/ports`` to its fake; each fake can be
built with no arguments. ``tests/contracts/test_test_isolation.py`` fails when a port has no
fake here, or a fake doesn't satisfy its port. Tests use these, never a live adapter
(``tests/live_guard.py``).
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from nextron.ports.alert import AlertPort
from nextron.ports.clock import Clock
from nextron.ports.google_oauth import ClientFilePort, ConsentListener, GoogleOAuthPort
from nextron.ports.mail import MailPort
from nextron.ports.secrets import SecretsPort
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.google_oauth import FakeClientFiles, FakeConsentListener, FakeGoogleOAuth
from tests.fakes.mail import InMemoryMail
from tests.fakes.secrets import InMemorySecrets

PORT_FAKES: Final[Mapping[type, type]] = MappingProxyType(
    {
        Clock: FrozenClock,
        SecretsPort: InMemorySecrets,
        AlertPort: InMemoryAlert,
        MailPort: InMemoryMail,
        GoogleOAuthPort: FakeGoogleOAuth,
        ConsentListener: FakeConsentListener,
        ClientFilePort: FakeClientFiles,
    }
)
