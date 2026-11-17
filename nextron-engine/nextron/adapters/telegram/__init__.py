"""The Telegram adapter (AD-9, AD-15, INT-8): plain-text alerts through the Bot API's
``sendMessage``, over ``httpx2``.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2`` and the standard library. Nothing
here logs.

**``TelegramAlert(secrets, *, http=None)``** implements ``AlertPort``. Without ``http`` it builds
``default_http_client()`` (the live constructor: an ``httpx2.Client`` with a 10 s connect
timeout and 15 s for each other phase, that never follows redirects) on its first send, looked
up when it is called (so a test can refuse it) and under a lock. ``nextron.main`` registers it
in ``ROLE_ADAPTERS`` for the ``alert`` role, so outside ``prod`` it is wrapped in the sink.

``send(message)``:

1. Reads the bot token with ``read_credential(secrets, "telegram_bot")`` on every send, and
   never keeps it; its ``AdapterError``s (``credential_not_set``, ``keyring_failed``)
   propagate.
2. A token that doesn't fullmatch ``[0-9]+:[A-Za-z0-9_-]+`` (exactly what ``redact`` masks in a
   ``/bot<token>`` path) is ``telegram_bad_token``; a recipient that isn't a numeric chat id
   (``nextron.domain.alert.is_telegram_chat_id``) is ``telegram_bad_chat_id``. Both are
   non-retryable, and no request is made.
3. POSTs ``https://api.telegram.org/bot<token>/sendMessage`` with the JSON body ``{"chat_id":
   <int>, "text": <text>}``: never a ``parse_mode`` or any other field, so the text is plain.

=====================================================  =========================================
Result                                                 Outcome
=====================================================  =========================================
2xx, JSON ``{"ok": true, "result": {"message_id":      ``AlertDelivery(str(message_id))``
<int, not bool>}}``
any other 2xx                                          ``alert_delivery_unknown``
429 or 5xx                                             retryable ``telegram_unavailable``
any other status (400, 401, 403, 404, a 3xx)           ``telegram_refused``, naming the status
nothing reached Telegram (``NOT_SENT``: a connect      retryable ``telegram_unavailable``
error or timeout, a pool timeout, a proxy error, an
unsupported protocol, a local protocol error, a
write error or timeout)
a read timeout or error, a remote protocol error, or   ``alert_delivery_unknown``
any other exception (the request may have been sent)
=====================================================  =========================================

``alert_delivery_unknown`` (``nextron.ports.alert.DELIVERY_UNKNOWN``) is never retryable: the
``notify`` service re-sends such an alert once, 60 s later. Every error is an ``AdapterError``
whose message is this module's own text, raised ``from None``: none carries the token, the URL,
the text, the chat id or a response body, in its message, ``repr`` or traceback.
"""

import re
import threading
from typing import Final

import httpx2

from nextron.domain.alert import is_telegram_chat_id
from nextron.domain.credentials import CredentialKey
from nextron.ports.alert import DELIVERY_UNKNOWN, AlertDelivery, AlertMessage
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort, read_credential

API_BASE: Final = "https://api.telegram.org"
TIMEOUT_S: Final = 15.0
CONNECT_TIMEOUT_S: Final = 10.0
TOKEN_PATTERN: Final = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")  # matched with fullmatch

TELEGRAM_BAD_TOKEN: Final = "telegram_bad_token"
TELEGRAM_BAD_CHAT_ID: Final = "telegram_bad_chat_id"
TELEGRAM_UNAVAILABLE: Final = "telegram_unavailable"
TELEGRAM_REFUSED: Final = "telegram_refused"

# The failures that mean nothing reached Telegram, so a retry can't send the alert twice.
NOT_SENT: Final = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.PoolTimeout,
    httpx2.ProxyError,
    httpx2.UnsupportedProtocol,
    httpx2.LocalProtocolError,
    httpx2.WriteError,
    httpx2.WriteTimeout,
)


def default_http_client() -> httpx2.Client:
    """The live HTTP client: 10 s to connect, 15 s for each other phase, and redirects never
    followed."""
    return httpx2.Client(
        timeout=httpx2.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S), follow_redirects=False
    )


def _message_id(response: httpx2.Response) -> int | None:
    """The ``message_id`` of a successful ``sendMessage`` answer, or ``None``."""
    try:
        document = response.json()
    except Exception:  # noqa: BLE001 - any body that isn't JSON has no message id
        return None
    if not isinstance(document, dict) or document.get("ok") is not True:
        return None
    result = document.get("result")
    if not isinstance(result, dict):
        return None
    message_id = result.get("message_id")
    if isinstance(message_id, bool) or not isinstance(message_id, int):
        return None
    return message_id


class TelegramAlert:
    """``AlertPort`` over the Telegram Bot API; see the module docstring."""

    def __init__(self, secrets: SecretsPort, *, http: httpx2.Client | None = None) -> None:
        self._secrets = secrets
        self._http = http
        self._lock = threading.Lock()

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    def send(self, message: AlertMessage) -> AlertDelivery:
        token = read_credential(self._secrets, CredentialKey.TELEGRAM_BOT)
        if not TOKEN_PATTERN.fullmatch(token):
            raise AdapterError(
                False, TELEGRAM_BAD_TOKEN, "the Telegram bot token is not <digits>:<key>"
            ) from None
        recipient = message.recipient
        if not is_telegram_chat_id(recipient):
            raise AdapterError(
                False, TELEGRAM_BAD_CHAT_ID, "the alert's chat id is not a numeric Telegram id"
            ) from None
        http = self._client()
        try:
            response = http.post(
                f"{API_BASE}/bot{token}/sendMessage",
                json={"chat_id": int(recipient), "text": message.text},
            )
        except NOT_SENT:
            raise AdapterError(
                True, TELEGRAM_UNAVAILABLE, "the Telegram request could not be sent"
            ) from None
        except Exception:  # noqa: BLE001 - the request may have gone out: the outcome is unknown
            raise AdapterError(
                False, DELIVERY_UNKNOWN, "the Telegram request was sent, but no answer came back"
            ) from None
        return self._delivery(response)

    @staticmethod
    def _delivery(response: httpx2.Response) -> AlertDelivery:
        status = response.status_code
        if 200 <= status < 300:
            message_id = _message_id(response)
            if message_id is None:
                raise AdapterError(
                    False, DELIVERY_UNKNOWN, f"Telegram answered HTTP {status} without a message id"
                ) from None
            return AlertDelivery(str(message_id))
        if status == 429 or 500 <= status < 600:
            raise AdapterError(
                True, TELEGRAM_UNAVAILABLE, f"Telegram answered with HTTP {status}"
            ) from None
        raise AdapterError(
            False, TELEGRAM_REFUSED, f"Telegram refused the alert with HTTP {status}"
        ) from None
