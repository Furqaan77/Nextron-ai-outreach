"""The Google OAuth adapters (AD-9, AD-12, INT-2, INT-3): Google's token endpoints over
``httpx2``, the one-shot consent listener on ``127.0.0.1`` and the downloaded client file.

Import rule: ``nextron.ports``, ``nextron.domain``, ``httpx2`` and the standard library.

Every error is an ``AdapterError`` whose message is this module's own text: never a response
body, an exception's text, a token, a code, a URL with a query or a path. Nothing here logs.

**``GoogleOAuthClient(*, meter, http=None)``** implements ``GoogleOAuthPort``. Without ``http`` it
builds ``default_http_client()`` (the live constructor: an ``httpx2.Client`` with a 15 s timeout
that never follows redirects) on its first call, not when it is constructed, so building the
dashboard builds no HTTP client. Each call is one form-encoded POST:

- ``exchange``: ``code``, ``client_id``, ``client_secret``, ``redirect_uri``,
  ``grant_type=authorization_code`` and ``code_verifier`` to ``TOKEN_URI``;
- ``refresh``: ``refresh_token``, ``client_id``, ``client_secret`` and
  ``grant_type=refresh_token`` to ``TOKEN_URI``;
- ``revoke``: ``token`` to ``REVOKE_URI``.

A 2xx token answer must be a JSON object with a non-empty ``access_token`` and an integer
``expires_in`` from 1 to ``MAX_EXPIRES_IN`` (86,400 s, a day; Google gives 3,599) (``scope``
defaults to empty; ``refresh_token`` and ``id_token`` are kept
when they are non-empty strings). The codes are those of ``nextron.ports.google_oauth``: a 4xx
whose JSON ``error`` is ``invalid_grant`` is ``google_invalid_grant``; another 4xx is
``google_rejected``; a failed request (network, timeout), a 429 or a 5xx is
``google_unavailable`` (retryable); anything else is ``google_bad_response``. ``revoke`` takes
200 and a 400 whose ``error`` is ``invalid_token`` as done.

**Metering (FR-57).** Every request is recorded through ``meter`` (``UsageMeterPort``, through
``Metered``) as exactly one ``ApiCall("google_oauth", <operation>)``, whatever the outcome:
``code_exchange``, ``token_refresh`` or ``token_revocation``. It is ``ok=True`` when the call
returns (a revoke answered ``invalid_token`` included), else ``ok=False`` with the
``AdapterError``'s code, recorded before the error propagates.

**``LoopbackListener()``** implements ``ConsentListener``. It binds ``127.0.0.1:0`` (Windows
``SO_EXCLUSIVEADDRUSE``, so no other process can bind the same port; an ``OSError`` is
``listener_failed``) and serves from a daemon thread; ``redirect_uri()`` is
``http://127.0.0.1:<port>/``. Only a GET whose ``Host`` is exactly ``127.0.0.1:<port>`` and
whose path is exactly ``/`` counts: another ``Host`` gets 400, another path 404. The first such
request has its query recorded (``parse_qs`` with blank values kept and at most 20 fields; more
records an empty query, which the flow refuses) and gets a fixed page, sent with
``Cache-Control: no-store`` and a ``Content-Security-Policy`` that allows nothing: "Nextron
received Google's answer. You can close this tab and return to the dashboard." Every later
request gets 404. The request line holds the authorization code, so ``log_message`` is silenced,
and the server's ``handle_error`` prints nothing.

**``LocalClientFiles()``** implements ``ClientFilePort`` with ``os.lstat``: only a regular file
counts (a folder, a link or any reparse point is ``client_file_not_regular``), of at most
``limit`` bytes; the file read must still be the one ``lstat`` saw.
"""

import os
import socket
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import MappingProxyType
from typing import Final
from urllib.parse import parse_qs

import httpx2

from nextron.domain.google_oauth import REVOKE_URI, TOKEN_URI, ClientConfig
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import (
    CLIENT_FILE_MISSING,
    CLIENT_FILE_NOT_DELETED,
    CLIENT_FILE_NOT_REGULAR,
    CLIENT_FILE_TOO_LARGE,
    CLIENT_FILE_UNREADABLE,
    GOOGLE_BAD_RESPONSE,
    GOOGLE_INVALID_GRANT,
    GOOGLE_REJECTED,
    GOOGLE_UNAVAILABLE,
    LISTENER_FAILED,
    Query,
    TokenSet,
)
from nextron.ports.usage_meter import Metered, UsageMeterPort

HTTP_TIMEOUT_S: Final = 15.0
MAX_EXPIRES_IN: Final = 86_400  # seconds: a longer lifetime is not an answer Google gives
HOST: Final = "127.0.0.1"
MAX_QUERY_FIELDS: Final = 20
REQUEST_TIMEOUT_S: Final = 10.0  # a connection that sends nothing is dropped after this
POLL_INTERVAL_S: Final = 0.1
INVALID_GRANT: Final = "invalid_grant"
INVALID_TOKEN: Final = "invalid_token"
PROVIDER: Final = "google_oauth"  # the usage meter's provider and operations
CODE_EXCHANGE: Final = "code_exchange"
TOKEN_REFRESH: Final = "token_refresh"
TOKEN_REVOCATION: Final = "token_revocation"

RECEIVED_PAGE: Final = (
    b'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    b"<title>Nextron</title>\n</head>\n<body>\n"
    b"<p>Nextron received Google's answer. You can close this tab and return to the "
    b"dashboard.</p>\n</body>\n</html>\n"
)
BAD_HOST_TEXT: Final = b"Bad request\n"
NOT_FOUND_TEXT: Final = b"Not found\n"
PAGE_HEADERS: Final = (
    ("Cache-Control", "no-store"),
    (
        "Content-Security-Policy",
        "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Connection", "close"),
)


# Google's token endpoints ---------------------------------------------------------------------


def default_http_client() -> httpx2.Client:
    """The live HTTP client: a 15 s timeout, and redirects never followed."""
    return httpx2.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=False)


def _error_field(response: httpx2.Response) -> str | None:
    """The ``error`` field of a JSON error answer, or ``None``."""
    try:
        document = response.json()
    except Exception:  # noqa: BLE001 - not JSON: there is no error field
        return None
    error = document.get("error") if isinstance(document, dict) else None
    return error if isinstance(error, str) else None


def _non_empty(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class GoogleOAuthClient:
    """``GoogleOAuthPort`` over ``httpx2``; see the module docstring."""

    def __init__(self, *, meter: UsageMeterPort, http: httpx2.Client | None = None) -> None:
        self._meter = meter
        self._http = http
        self._lock = threading.Lock()

    def _client(self) -> httpx2.Client:
        with self._lock:
            if self._http is None:
                self._http = default_http_client()  # looked up now, so tests can refuse it
            return self._http

    @staticmethod
    def _post(
        http: httpx2.Client, url: str, form: dict[str, str], operation: str
    ) -> httpx2.Response:
        try:
            return http.post(url, data=form)
        except Exception:  # noqa: BLE001 - a network failure or a timeout, by any name
            raise AdapterError(
                True, GOOGLE_UNAVAILABLE, f"the Google {operation} request failed"
            ) from None

    @staticmethod
    def _check(response: httpx2.Response, operation: str) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if 400 <= status < 500 and _error_field(response) == INVALID_GRANT:
            raise AdapterError(
                False, GOOGLE_INVALID_GRANT, f"Google refused the {operation}: invalid_grant"
            )
        if status == 429 or 500 <= status < 600:
            raise AdapterError(
                True, GOOGLE_UNAVAILABLE, f"Google answered the {operation} with HTTP {status}"
            )
        if 400 <= status < 500:
            raise AdapterError(
                False, GOOGLE_REJECTED, f"Google rejected the {operation} with HTTP {status}"
            )
        raise AdapterError(
            False, GOOGLE_BAD_RESPONSE, f"Google answered the {operation} with HTTP {status}"
        )

    @staticmethod
    def _tokens(response: httpx2.Response, operation: str) -> TokenSet:
        try:
            document = response.json()
        except Exception:  # noqa: BLE001 - any body that isn't JSON
            document = None
        if not isinstance(document, dict):
            raise AdapterError(
                False, GOOGLE_BAD_RESPONSE, f"Google's {operation} answer is not a JSON object"
            )
        access_token = _non_empty(document.get("access_token"))
        expires_in = document.get("expires_in")
        if (
            access_token is None
            or isinstance(expires_in, bool)
            or not isinstance(expires_in, int)
            or not 0 < expires_in <= MAX_EXPIRES_IN
        ):
            raise AdapterError(
                False,
                GOOGLE_BAD_RESPONSE,
                f"Google's {operation} answer has no access token or expiry",
            )
        scope = document.get("scope")
        return TokenSet(
            access_token=access_token,
            expires_in=expires_in,
            scope=scope if isinstance(scope, str) else "",
            refresh_token=_non_empty(document.get("refresh_token")),
            id_token=_non_empty(document.get("id_token")),
        )

    def exchange(
        self, client: ClientConfig, code: str, verifier: str, redirect_uri: str
    ) -> TokenSet:
        operation = "code exchange"
        form = {
            "code": code,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
        http = self._client()
        with Metered(self._meter, PROVIDER, CODE_EXCHANGE):  # one ApiCall, whatever the outcome
            response = self._post(http, TOKEN_URI, form, operation)
            self._check(response, operation)
            return self._tokens(response, operation)

    def refresh(self, client: ClientConfig, refresh_token: str) -> TokenSet:
        operation = "token refresh"
        form = {
            "refresh_token": refresh_token,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "grant_type": "refresh_token",
        }
        http = self._client()
        with Metered(self._meter, PROVIDER, TOKEN_REFRESH):  # one ApiCall, whatever the outcome
            response = self._post(http, TOKEN_URI, form, operation)
            self._check(response, operation)
            return self._tokens(response, operation)

    def revoke(self, token: str) -> None:
        operation = "token revocation"
        http = self._client()
        with Metered(self._meter, PROVIDER, TOKEN_REVOCATION):  # one ApiCall, whatever the outcome
            response = self._post(http, REVOKE_URI, {"token": token}, operation)
            if response.status_code == 400 and _error_field(response) == INVALID_TOKEN:
                return  # already revoked or expired: done, and recorded ok
            self._check(response, operation)


# The consent listener -------------------------------------------------------------------------


class _ConsentHandler(BaseHTTPRequestHandler):
    """One request to the consent listener; see the module docstring."""

    server: "_ConsentServer"
    timeout = REQUEST_TIMEOUT_S
    server_version = "Nextron"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler calls
        self.server.listener._answer(self)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silenced: the request line holds the authorization code."""


class _ConsentServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, listener: "LoopbackListener") -> None:
        self.listener = listener
        super().__init__((HOST, 0), _ConsentHandler)

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:  # Windows: no other socket may bind this port
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        # TCPServer's bind only: HTTPServer's would look the host name up for nothing.
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = HOST
        self.server_port = self.server_address[1]

    def handle_error(self, request: object, client_address: object) -> None:
        """Print nothing: a traceback could hold the request line."""


def _send(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    for name, value in PAGE_HEADERS:
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


class LoopbackListener:
    """``ConsentListener`` on ``127.0.0.1:<ephemeral port>``; see the module docstring."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._answered = threading.Event()
        self._query: Query | None = None
        self._closed = False
        try:
            server = _ConsentServer(self)
        except OSError:
            raise AdapterError(
                False, LISTENER_FAILED, "the consent listener could not bind 127.0.0.1"
            ) from None
        self._server = server
        self._port = int(server.server_address[1])
        self._host = f"{HOST}:{self._port}"
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": POLL_INTERVAL_S},
            name="google-consent-listener",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:  # noqa: BLE001 - no thread, no listener
            server.server_close()
            raise AdapterError(
                False, LISTENER_FAILED, "the consent listener could not start"
            ) from None

    @property
    def port(self) -> int:
        return self._port

    def redirect_uri(self) -> str:
        return f"http://{self._host}/"

    def _answer(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.headers.get_all("Host") != [self._host]:
            _send(handler, 400, BAD_HOST_TEXT, "text/plain; charset=utf-8")
            return
        # The request target as sent: http.server turns a leading "//" into "/" in .path.
        words = handler.requestline.split()
        target = words[1] if len(words) >= 2 else ""
        path, _, query = target.partition("?")
        with self._lock:
            counts = path == "/" and self._query is None and not self._closed
            if counts:
                try:
                    fields = parse_qs(
                        query, keep_blank_values=True, max_num_fields=MAX_QUERY_FIELDS
                    )
                except ValueError:  # too many fields: recorded empty, so the flow refuses it
                    fields = {}
                self._query = MappingProxyType({k: tuple(v) for k, v in fields.items()})
        if not counts:
            _send(handler, 404, NOT_FOUND_TEXT, "text/plain; charset=utf-8")
            return
        try:
            _send(handler, 200, RECEIVED_PAGE, "text/html; charset=utf-8")
        finally:
            self._answered.set()  # after the page went out, so the tab shows it

    def wait(self, timeout: float) -> Query | None:
        self._answered.wait(timeout)
        with self._lock:
            return self._query

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._answered.set()  # wakes a waiter: it gets the query, if one came, else None
        self._server.shutdown()
        self._server.server_close()


# The client file ------------------------------------------------------------------------------


def _is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class LocalClientFiles:
    """``ClientFilePort`` on this PC's file system; see the module docstring."""

    def read(self, path: str, limit: int) -> bytes:
        try:
            seen = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            raise AdapterError(False, CLIENT_FILE_MISSING, "there is no client file") from None
        except OSError:
            raise AdapterError(
                False, CLIENT_FILE_UNREADABLE, "the client file could not be read"
            ) from None
        if not stat.S_ISREG(seen.st_mode) or _is_reparse_point(seen):
            raise AdapterError(
                False, CLIENT_FILE_NOT_REGULAR, "the client file is not a regular file"
            )
        if seen.st_size > limit:
            raise AdapterError(False, CLIENT_FILE_TOO_LARGE, "the client file is too large")
        try:
            with open(path, "rb") as file:
                opened = os.fstat(file.fileno())
                data = file.read(limit + 1)
        except OSError:
            raise AdapterError(
                False, CLIENT_FILE_UNREADABLE, "the client file could not be read"
            ) from None
        if (opened.st_dev, opened.st_ino) != (seen.st_dev, seen.st_ino):
            raise AdapterError(
                False, CLIENT_FILE_NOT_REGULAR, "the client file changed while it was read"
            )
        if len(data) > limit:
            raise AdapterError(False, CLIENT_FILE_TOO_LARGE, "the client file is too large")
        return data

    def delete(self, path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            return  # already gone
        except OSError:
            raise AdapterError(
                False, CLIENT_FILE_NOT_DELETED, "the client file could not be deleted"
            ) from None
