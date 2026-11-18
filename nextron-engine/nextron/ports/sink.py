"""The ``SinkFilesPort`` (AD-14, AD-17, NFR-17, Story 3.14): erasing a person's recorded messages
from the dev and test sink files.

Outside ``prod``, the sink (``nextron.adapters.sink``) records each mail or alert it doesn't
deliver as one JSON line of ``<sink folder>\\<UTC date>.jsonl``, with the recipient's address,
the subject and the text. An erasure must reach those lines too.
``nextron.adapters.sink.SinkRecorder`` implements this port; tests use
``tests.fakes.sink.InMemorySinkFiles``. ``nextron.main`` passes none in ``prod``, where nothing
is recorded.

**``erase_recipient(address_hash) -> SinkErasure(files, lines)``**: in every sink file, each
``mail`` line whose recipient's ``nextron.domain.erasure.tombstone_hash("address", recipient)``
equals ``address_hash`` gets its ``recipient``, ``subject`` and ``text`` replaced with
``[erased]`` (its ``time`` and ``port`` are kept). ``files`` is how many files changed and
``lines`` how many lines. A file that can't be read or written raises the retryable
``AdapterError`` ``SINK_ERASE_FAILED`` (``sink_erase_failed``), whose message holds no address.
"""

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

SINK_ERASE_FAILED: Final = "sink_erase_failed"


@dataclass(frozen=True, slots=True)
class SinkErasure:
    """How many sink files and recorded messages an erasure changed."""

    files: int
    lines: int


@runtime_checkable
class SinkFilesPort(Protocol):
    """Erases a person's recorded messages from the sink files."""

    def erase_recipient(self, address_hash: str) -> SinkErasure:
        """Tombstone every recorded mail to the address whose hash is ``address_hash``; raise
        ``AdapterError`` when a file can't be rewritten."""
        ...
