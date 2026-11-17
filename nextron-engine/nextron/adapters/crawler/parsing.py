"""Decoding and parsing a fetched page (AD-18): the visible text and the links, with hidden
content stripped before anything is read.

Import rule: ``nextron.ports``, ``nextron.domain``, ``selectolax`` and the standard library.
Nothing here reaches the network or logs.

- ``content_type(header) -> (media_type, charset)``: the lowercased media type of a
  ``Content-Type`` header (``""`` when there is none) and its ``charset`` parameter (``None``
  when absent).
- ``decode(body, charset)``: the body as text. The charset is the header's, else the first
  ``<meta charset>`` (or ``<meta http-equiv="Content-Type" content="...; charset=...">``) in the
  first 4,096 bytes, else UTF-8. A label Python doesn't know, or one naming a codec that isn't a
  text encoding (``base64``, ``hex``, ``rot13``) or ``undefined``, is skipped; bytes the
  charset can't decode become U+FFFD, and a decode that still raises falls back to UTF-8 (a
  domain-name codec named as a charset passes plain ASCII through and raises on anything else,
  so it falls back too).
- ``parse_html(document, page_url) -> ParsedHtml(text, links)`` with selectolax's
  ``LexborHTMLParser``, in this order:

  1. ``<base href>`` (the first, resolved against ``page_url``) is read; links resolve against
     it, else against ``page_url``.
  2. Every ``iframe[src]`` and ``script[src]`` is collected, in document order, hidden or not
     (an ATS board is often embedded in a hidden or script-built block).
  3. One walk (an explicit stack, never recursion) removes what a browser never renders
     (``REMOVED_TAGS``: ``head``, ``title``, ``script``, ``style``, ``noscript``, ``template``,
     ``iframe`` content, ``noembed``, ``noframes``, ``datalist`` and SVG ``desc`` and
     ``metadata``), a ``dialog`` without ``open``, comments, elements with a ``hidden``
     attribute or ``aria-hidden="true"`` (any case), and elements whose inline ``style`` sets
     ``display: none`` or ``visibility: hidden`` or ``collapse`` (CSS comments removed and CSS
     escapes such as ``n\\6f ne`` read first; ``!important`` ignored). The ``html`` and
     ``body`` elements themselves are never removed (an anti-flicker snippet hides them while a
     page loads); their descendants are checked. Opacity, off-screen positioning and
     stylesheet rules are not read.
  4. Every remaining ``a[href]`` is a link, its text the anchor's text with whitespace
     collapsed.
  5. The body's text: text nodes are joined as they are, with a line break only at the
     boundaries of block elements (``BLOCK_TAGS``, ``br`` and ``hr`` included); a line break
     inside a text node is a space, except within ``pre``. Then
     ``nextron.domain.crawl.visible_text``.

  A link's URL is the ``href`` or ``src`` with ASCII whitespace stripped and tabs and newlines
  removed, resolved with ``urllib.parse.urljoin`` and without its fragment. Only ``http`` and
  ``https`` links are kept; one that can't be resolved is dropped.
"""

import codecs
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Final

from selectolax.lexbor import LexborHTMLParser, LexborNode

from nextron.domain.crawl import visible_text
from nextron.ports.crawler import PageLink

# Elements whose content a browser never renders: removed with everything in them
REMOVED_TAGS: Final = frozenset(
    {
        "head", "title", "script", "style", "noscript", "template", "iframe", "noembed",
        "noframes", "datalist", "desc", "metadata",
    }
)  # fmt: skip
DIALOG_TAG: Final = "dialog"  # rendered only while it has an open attribute
ROOT_TAGS: Final = frozenset({"html", "body"})  # never hidden: their descendants are checked
REFUSED_CODECS: Final = frozenset({"undefined"})
BLOCK_TAGS: Final = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "br", "caption", "center", "dd",
        "details", "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure",
        "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "html",
        "legend", "li", "main", "menu", "nav", "ol", "optgroup", "option", "p", "pre",
        "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)  # fmt: skip
PRE_TAG: Final = "pre"
TEXT_NODE: Final = "-text"
WEB_SCHEMES: Final = frozenset({"http", "https"})
SNIFF_BYTES: Final = 4096
DEFAULT_CHARSET: Final = "utf-8"
CSS_COMMENT: Final = re.compile(r"/\*.*?(?:\*/|$)", re.DOTALL)
IMPORTANT: Final = re.compile(r"!\s*important\s*$")
# A CSS escape: up to six hex digits and one optional whitespace, else any one character
CSS_ESCAPE: Final = re.compile(r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|(.))", re.DOTALL)
MAX_CODE_POINT: Final = 0x10FFFF
REPLACEMENT: Final = "\ufffd"
META_CHARSET: Final = re.compile(
    rb"""<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE
)
URL_NOISE: Final = re.compile("[\t\n\r]")
ASCII_WHITESPACE: Final = "\t\n\f\r "
LINE_BREAKS: Final = re.compile(r"[\r\n\f\v\x1c-\x1e\x85\u2028\u2029]")


@dataclass(frozen=True, slots=True)
class ParsedHtml:
    """A page's visible text and links; neither shows in the ``repr``."""

    text: str = field(repr=False)
    links: tuple[PageLink, ...] = field(repr=False)


# Decoding -------------------------------------------------------------------------------------


def content_type(header: str | None) -> tuple[str, str | None]:
    """The media type (lowercased, ``""`` without a header) and ``charset`` of a
    ``Content-Type`` header."""
    if not header:
        return "", None
    media_type, *parameters = header.split(";")
    charset = None
    for parameter in parameters:
        name, sep, value = parameter.partition("=")
        if sep and name.strip().lower() == "charset":
            charset = value.strip().strip("\"'").strip() or None
    return media_type.strip().lower(), charset


def _known(charset: str | None) -> str | None:
    """The codec ``charset`` names, when it is a text encoding a page may use; else ``None``."""
    if not charset:
        return None
    try:
        info = codecs.lookup(charset)
    except LookupError:
        return None
    if not getattr(info, "_is_text_encoding", True) or info.name in REFUSED_CODECS:
        return None  # base64, hex, rot13 and the like transform bytes, not decode text
    return info.name


def decode(body: bytes, charset: str | None) -> str:
    """``body`` as text; see the module docstring."""
    chosen = _known(charset)
    if chosen is None:
        found = META_CHARSET.search(body[:SNIFF_BYTES])
        chosen = _known(found.group(1).decode("ascii")) if found else None
    try:
        return body.decode(chosen or DEFAULT_CHARSET, errors="replace")
    except (LookupError, UnicodeError, ValueError, TypeError):
        return body.decode(DEFAULT_CHARSET, errors="replace")


# Parsing --------------------------------------------------------------------------------------


def _children(node: LexborNode) -> list[LexborNode]:
    found = []
    child = node.child
    while child is not None:
        found.append(child)
        child = child.next
    return found


def _unescaped(match: re.Match[str]) -> str:
    hex_digits, other = match.groups()
    if hex_digits is None:
        return str(other)
    code = int(hex_digits, 16)
    if code == 0 or code > MAX_CODE_POINT or 0xD800 <= code <= 0xDFFF:
        return REPLACEMENT
    return chr(code)


def _style_hides(style: str) -> bool:
    text = CSS_ESCAPE.sub(_unescaped, CSS_COMMENT.sub("", style)).lower()
    for declaration in text.split(";"):
        name, sep, value = declaration.partition(":")
        if not sep:
            continue
        name = name.strip()
        value = IMPORTANT.sub("", value.strip()).strip()
        if name == "display" and value == "none":
            return True
        if name == "visibility" and value in ("hidden", "collapse"):
            return True
    return False


def _is_hidden(node: LexborNode) -> bool:
    if node.is_comment_node:
        return True
    if not node.is_element_node or node.tag in ROOT_TAGS:
        return False
    if node.tag in REMOVED_TAGS:
        return True
    attributes = node.attributes
    if node.tag == DIALOG_TAG and "open" not in attributes:
        return True
    if "hidden" in attributes:
        return True
    aria = attributes.get("aria-hidden")
    if aria is not None and aria.strip().lower() == "true":
        return True
    style = attributes.get("style")
    return style is not None and _style_hides(style)


def _strip_hidden(root: LexborNode) -> None:
    """Remove every hidden node under ``root``; none removed is inside another."""
    removed = []
    stack = [root]
    while stack:
        node = stack.pop()
        if _is_hidden(node):
            removed.append(node)
            continue
        stack.extend(_children(node))
    for node in removed:
        node.decompose()


def _resolve(base: str, reference: str | None) -> str | None:
    """``reference`` resolved against ``base``, without its fragment; ``None`` unless it is an
    ``http`` or ``https`` URL."""
    if reference is None:
        return None
    cleaned = URL_NOISE.sub("", reference.strip(ASCII_WHITESPACE))
    try:
        resolved, _ = urllib.parse.urldefrag(urllib.parse.urljoin(base, cleaned))
        scheme = urllib.parse.urlsplit(resolved).scheme.lower()
    except ValueError:
        return None
    return resolved if scheme in WEB_SCHEMES else None


def _base(tree: LexborHTMLParser, page_url: str) -> str:
    node = tree.css_first("base[href]")
    if node is None:
        return page_url
    resolved = _resolve(page_url, node.attributes.get("href"))
    return resolved if resolved is not None else page_url


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def _body_text(body: LexborNode | None) -> str:
    """The body's text with line breaks at block boundaries only; see the module docstring."""
    if body is None:
        return ""
    parts: list[str] = []
    stack: list[tuple[LexborNode | None, bool]] = [(body, False)]
    while stack:
        node, in_pre = stack.pop()
        if node is None:  # the end of a block
            parts.append("\n")
            continue
        if node.tag == TEXT_NODE:
            text = node.text_content or ""
            parts.append(text if in_pre else LINE_BREAKS.sub(" ", text))
            continue
        if not node.is_element_node:
            continue
        inside_pre = in_pre or node.tag == PRE_TAG
        if node.tag in BLOCK_TAGS:
            parts.append("\n")
            stack.append((None, False))
        stack.extend((child, inside_pre) for child in reversed(_children(node)))
    return "".join(parts)


def parse_html(document: str, page_url: str) -> ParsedHtml:
    """The visible text and links of ``document``, fetched from ``page_url``; see the module
    docstring."""
    tree = LexborHTMLParser(document)
    base = _base(tree, page_url)
    embeds = []
    for node in tree.css("iframe[src], script[src]"):
        url = _resolve(base, node.attributes.get("src"))
        if url is not None:
            embeds.append(PageLink(url=url, text="", tag=node.tag or ""))
    root = tree.root
    if root is not None:
        _strip_hidden(root)
    anchors = []
    for node in tree.css("a[href]"):
        url = _resolve(base, node.attributes.get("href"))
        if url is not None:
            text = _collapsed(node.text(deep=True, separator=" ", strip=False))
            anchors.append(PageLink(url=url, text=text, tag="a"))
    text = visible_text(_body_text(tree.body))
    return ParsedHtml(text=text, links=(*anchors, *embeds))
