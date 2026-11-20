"""In-memory fakes, one per port (AD-9), plus fakes for the startup-check probes.

``InMemoryUsageMeter`` (``usage_meter``) fakes ``UsageMeterPort``: every HTTP adapter takes a
``meter``, so a test that builds one passes it. ``ScriptedLlm`` (``llm``) fakes ``LlmPort``:
it answers each ``run`` with the next scripted ``LlmResult`` or ``AdapterError`` and records
every call. ``FakeEmailSyntax`` (``email_syntax``) fakes ``EmailSyntaxPort``: it refuses the
addresses it is given and any address holding ``,;<>()[]\\"`` or a space.
``ScriptedCrawler`` (``crawler``) fakes ``CrawlerPort``: it answers each fetch by its URL from
a script, after the real adapter's checks on the first URL, and records every call.
``FakeTokenCodec`` (``token_codec``) fakes ``TokenCodecPort``: it encodes under the kids it is
given (kid 1 by default) and decodes only the tokens it encoded.
``ScriptedRenderer`` (``renderer``) fakes ``RendererPort`` the same way, by URL;
``ScriptedRegistry`` (``registry``) fakes ``RegistryPort`` by company number and search name,
recording every call;
``ScriptedUnsubscribe`` (``unsubscribe``) fakes ``UnsubscribePort``: it answers each
``fetch_pending`` with the next scripted ``PendingFetch`` or ``AdapterError`` (the last one
repeats) and records every host; ``ScriptedPlaces`` (``places``) fakes ``PlacesPort`` by
``(query, location, page_token)``, recording every call;
``nextron.adapters.renderer`` itself is tested on the fake Playwright objects of ``browser``.
``FakeDns`` (``dns``) fakes ``DnsPort``: it answers TXT, MX and A queries from scripts keyed
by name (``()`` for a name not scripted), raises the scripted ``AdapterError`` for a name in
``errors`` and records every query. ``FakePostmaster`` (``postmaster``) fakes
``PostmasterPort``: it answers each domain's scripted spam rate (no data when not scripted),
raises the scripted ``AdapterError`` for a domain in ``errors`` and records every query.

``ScriptedFinder``, ``ScriptedDomainSearch`` and ``ScriptedVerifier`` (``email_finder``) fake
``EmailFinderPort``, ``DomainSearchPort`` and ``EmailVerifierPort``: each answers with the next scripted answer or ``AdapterError``, and with
nothing found once the script is exhausted, and records every call.

``PORT_FAKES`` maps every port Protocol in ``nextron/ports`` to its fake; each fake can be
built with no arguments. ``tests/contracts/test_test_isolation.py`` fails when a port has no
fake here, or a fake doesn't satisfy its port. Tests use these, never a live adapter
(``tests/live_guard.py``).
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from nextron.ports.alert import AlertPort
from nextron.ports.backup_target import BackupTargetPort
from nextron.ports.clock import Clock
from nextron.ports.crawler import CrawlerPort
from nextron.ports.dns import DnsPort
from nextron.ports.email_finder import DomainSearchPort, EmailFinderPort, EmailVerifierPort
from nextron.ports.email_syntax import EmailSyntaxPort
from nextron.ports.google_oauth import ClientFilePort, ConsentListener, GoogleOAuthPort
from nextron.ports.job_board import JobBoardPort
from nextron.ports.llm import LlmPort
from nextron.ports.mail import MailPort
from nextron.ports.places import PlacesPort
from nextron.ports.postmaster import PostmasterPort
from nextron.ports.registry import RegistryPort
from nextron.ports.renderer import RendererPort
from nextron.ports.secrets import SecretsPort
from nextron.ports.sink import SinkFilesPort
from nextron.ports.token_codec import TokenCodecPort
from nextron.ports.unsubscribe import UnsubscribePort
from nextron.ports.usage_meter import UsageMeterPort
from tests.fakes.alert import InMemoryAlert
from tests.fakes.backup_target import InMemoryBackupTarget
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import ScriptedCrawler
from tests.fakes.dns import FakeDns
from tests.fakes.email_finder import ScriptedDomainSearch, ScriptedFinder, ScriptedVerifier
from tests.fakes.email_syntax import FakeEmailSyntax
from tests.fakes.google_oauth import FakeClientFiles, FakeConsentListener, FakeGoogleOAuth
from tests.fakes.job_board import ScriptedJobBoards
from tests.fakes.llm import ScriptedLlm
from tests.fakes.mail import InMemoryMail
from tests.fakes.places import ScriptedPlaces
from tests.fakes.postmaster import FakePostmaster
from tests.fakes.registry import ScriptedRegistry
from tests.fakes.renderer import ScriptedRenderer
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.sink import InMemorySinkFiles
from tests.fakes.token_codec import FakeTokenCodec
from tests.fakes.unsubscribe import ScriptedUnsubscribe
from tests.fakes.usage_meter import InMemoryUsageMeter

PORT_FAKES: Final[Mapping[type, type]] = MappingProxyType(
    {
        Clock: FrozenClock,
        SecretsPort: InMemorySecrets,
        AlertPort: InMemoryAlert,
        MailPort: InMemoryMail,
        GoogleOAuthPort: FakeGoogleOAuth,
        ConsentListener: FakeConsentListener,
        ClientFilePort: FakeClientFiles,
        UsageMeterPort: InMemoryUsageMeter,
        BackupTargetPort: InMemoryBackupTarget,
        LlmPort: ScriptedLlm,
        EmailSyntaxPort: FakeEmailSyntax,
        CrawlerPort: ScriptedCrawler,
        TokenCodecPort: FakeTokenCodec,
        RendererPort: ScriptedRenderer,
        DnsPort: FakeDns,
        JobBoardPort: ScriptedJobBoards,
        RegistryPort: ScriptedRegistry,
        SinkFilesPort: InMemorySinkFiles,
        EmailFinderPort: ScriptedFinder,
        DomainSearchPort: ScriptedDomainSearch,
        UnsubscribePort: ScriptedUnsubscribe,
        PlacesPort: ScriptedPlaces,
        EmailVerifierPort: ScriptedVerifier,
        PostmasterPort: FakePostmaster,
    }
)
