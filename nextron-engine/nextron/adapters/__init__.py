"""Adapters: implementations of the ports for each external system (AD-9).

Import rule: ``nextron.ports``, ``nextron.domain`` and third-party SDKs. Adapters never import
``nextron.app`` or ``nextron.db`` (contract 3), and only ``nextron.adapters.ai`` imports
``anthropic`` (contract 5).

When you add an adapter subpackage, add it to contract 5's ``source_modules`` in
``pyproject.toml`` and give its port an in-memory fake in ``tests/fakes`` (listed in
``tests.fakes.PORT_FAKES``); ``tests/contracts/test_contract_coverage.py`` fails until contract 5
lists it. Classify it in ``tests/live_guard.py`` too: a live adapter registers the constructor
that reaches its external system (such as ``nextron.adapters.secrets.default_backend``), so no
test can build it, and ``tests/contracts/test_test_isolation.py`` fails until every adapter
package is classified. A ``mail`` or ``alert`` adapter is built only through
``nextron.main.ROLE_ADAPTERS``, which wraps it in the dev and test sink (``adapters.sink``).

The adapters so far: ``clock`` (the system clock), ``secrets`` (Windows Credential Manager;
live constructor ``default_backend``), ``sink`` (the dev and test sink), ``google_oauth``
(Google's OAuth endpoints over ``httpx2``, the one-shot consent listener on ``127.0.0.1`` and the
downloaded client file; live constructor ``default_http_client``), ``telegram`` (ops alerts
through the Telegram Bot API's ``sendMessage`` over ``httpx2``, the ``alert`` role's first
adapter; live constructor ``default_http_client``), ``crypto`` (the backup file format: scrypt
and AES-256-GCM over ``cryptography``, pure computation with no port of its own; and the
unsubscribe token codec ``TokenCodec``, the ``TokenCodecPort``: AES-256-GCM-SIV and HMAC-SHA256
under the per-kid keys it reads through the ``SecretsPort`` it is given),
``backup_target`` (``FolderBackupTarget``, the ``BackupTargetPort``: encrypted backup copies in
the off-PC folder, through ``crypto``) and ``ai`` (the Claude gateway, ``ClaudeGateway``, the
``LlmPort`` over the ``anthropic`` SDK on ``httpx2``, with its route candidates, output schemas,
request builder and prices; live constructor ``default_http_client``), ``crawler``
(``HttpCrawler``, the ``CrawlerPort``: robots.txt and pages inside the egress filter, over
``httpx2``, ``selectolax`` and ``protego``; live constructors ``default_http_client``,
``default_resolver`` and ``default_own_addresses``), ``companies_house``
(``CompaniesHouseRegistry``, the ``RegistryPort``: company search and lookup through the
Companies House API over ``httpx2``, self rate-limited to 600 requests per 5 minutes; live
constructor ``default_http_client``), ``hunter`` (``HunterFinder``, the ``EmailFinderPort`` and
the ``DomainSearchPort``: Hunter's Email Finder and Domain Search over ``httpx2``; live
constructor ``default_http_client``) and ``prospeo`` (``ProspeoFinder``, the
``EmailFinderPort``: Prospeo's Enrich Person over ``httpx2``; live constructor
``default_http_client``).

Every adapter whose ``__init__`` takes an ``http`` client lets only ``AdapterError`` out of its
port calls, whatever the transport raises or answers, and never carries a secret into an error.
It also takes a required keyword-only ``meter`` (``UsageMeterPort``, FR-57) and records exactly
one ``ApiCall`` for every HTTP request it attempts, whatever the outcome (``ok``, or the
``AdapterError``'s code, recorded before the error is re-raised); a call refused before any
request records nothing. The AI gateway records an ``AiCall`` per request instead of an
``ApiCall`` (its route, tuple, served model, tokens and cost; the error code of the raised
error or of the ``LlmFailed``, ``LlmInvalid`` or ``LlmTruncated`` it returns).
``tests/contracts/test_adapter_boundary.py`` proves both for each one.
"""
