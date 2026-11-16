"""Rendering helpers that keep untrusted text inert (AD-13, AD-18, NFR-19, UX-DR3, UX-DR4).

Crawled pages, imported rows, Replies and AI output are untrusted: they may hold ``<script>``,
``onerror=``, ``hx-post=`` or ``javascript:`` text written to act through the dashboard.
Autoescaping is on in every template; these helpers, which ``install`` adds to the environment
(``nextron.web.app.template_environment`` calls it), cover the rest:

- ``{{ value|plain }}``: ``None`` renders empty; anything else renders as the escaped text of
  ``str(value)``. Any ``__html__`` is dropped first, so even ``Markup`` input is escaped.
- ``{{ safe_link(url, text) }}``: a link only when, after stripping surrounding ASCII
  whitespace, the URL has no ASCII control character, space or backslash, and no Unicode
  control, format (bidi overrides, zero-width), space or line/paragraph separator character
  (categories ``Cc``, ``Cf``, ``Zs``, ``Zl``, ``Zp``); ``urlsplit`` gives an ``http`` or
  ``https`` scheme (any case) and a hostname, with no user name or password (which could make
  ``https://nextronai.com@evil.example/`` look like a trusted link); and the port (if any)
  parses. The link is ``<a href="..." rel="noopener noreferrer">...</a>``. Anything else
  (``javascript:``, ``data:``, ``//host``, ``http:\\\\host``, an out-of-range port) renders
  ``plain(text or url)``.
- ``{{ csrf_field() }}``: the hidden ``csrf_token`` input every ``<form>`` carries, from the
  ``csrf_token`` the page was rendered with; a missing one raises.
- ``untrusted_block(text)`` in ``templates/macros.html`` (``{% from "macros.html" import
  untrusted_block %}``): a ``<div class="untrusted" hx-disable>`` around ``text|plain``, or
  around the body of a ``{% call untrusted_block() %}`` block. ``hx-disable`` stops HTMX acting
  on any ``hx-*`` attribute inside it.

The ``safe`` filter (in a template, a filter call or a ``filter`` block), ``autoescape false``
and ``Markup`` are allowed only where ``tests/contracts/safe_allowlist.txt`` lists them;
``tests/contracts/test_web_contracts.py`` fails on any other use. The three lines below that
name ``Markup`` are on that list.
"""

import re
import unicodedata
from typing import Final
from urllib.parse import urlsplit

from jinja2 import Environment, pass_context
from jinja2.runtime import Context
from markupsafe import Markup, escape

LINK_SCHEMES: Final = frozenset({"http", "https"})
ASCII_WHITESPACE: Final = "\t\n\f\r "  # the WHATWG set, stripped from both ends of a URL
UNSAFE_URL_CHARACTERS: Final = re.compile(r"[\x00-\x20\x7f\\]")
# control, format (bidi, zero-width), space, line and paragraph separator characters
UNSAFE_URL_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zs", "Zl", "Zp"})
_LINK: Final = Markup('<a href="{}" rel="noopener noreferrer">{}</a>')
_CSRF_FIELD: Final = Markup('<input type="hidden" name="csrf_token" value="{}">')


def _text(value: object) -> str:
    """``str(value)`` as a plain ``str``: without ``__html__``, even when ``__str__`` returned
    markup."""
    text = str(value)
    return text if type(text) is str else str.__str__(text)


def plain(value: object) -> str:
    """The escaped text of ``value`` (markup-safe HTML); ``None`` renders as nothing."""
    if value is None:
        return escape("")
    return escape(_text(value))


def _is_link(url: str) -> bool:
    if UNSAFE_URL_CHARACTERS.search(url):
        return False
    if any(unicodedata.category(character) in UNSAFE_URL_CATEGORIES for character in url):
        return False
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError when the port doesn't parse
    except ValueError:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    return parts.scheme.lower() in LINK_SCHEMES and bool(parts.hostname)


def safe_link(url: object, text: object = None) -> str:
    """An ``http``/``https`` link to ``url`` showing ``text`` (default: the stripped URL), with
    ``rel="noopener noreferrer"``; anything else renders as ``plain(text or url)``."""
    candidate = "" if url is None else _text(url).strip(ASCII_WHITESPACE)
    if not candidate or not _is_link(candidate):
        return plain(text or url)
    # Markup.format escapes the plain-str href; the text is already escaped by plain().
    return _LINK.format(candidate, plain(text or candidate))


@pass_context
def csrf_field(context: Context) -> str:
    """The hidden ``csrf_token`` input of a form; raises without a ``csrf_token``."""
    token = context.get("csrf_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(
            "csrf_field() needs the session's csrf_token in the template context; render the "
            "page with csrf_token=request.scope['nextron.csrf_token']"
        )
    return _CSRF_FIELD.format(_text(token))


def install(environment: Environment) -> Environment:
    """Add the ``plain`` filter and the ``safe_link`` and ``csrf_field`` globals."""
    environment.filters["plain"] = plain
    environment.globals["safe_link"] = safe_link
    environment.globals["csrf_field"] = csrf_field
    return environment
