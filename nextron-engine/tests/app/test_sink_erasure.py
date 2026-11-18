"""Erasing a recipient from the dev and test sink files (``SinkRecorder.erase_recipient``,
``nextron.ports.sink.SinkFilesPort``; AD-14, AD-17, NFR-17, Story 3.14) on a temporary folder:
the mails to the address tombstoned with ``time`` and ``port`` kept, every other line kept byte
for byte, the counts, a leftover ``.erasing`` file deleted first, the write lock held, and an
unreadable file a retryable ``sink_erase_failed``. Nothing here reaches a real service."""

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nextron.adapters import sink as sink_adapter
from nextron.adapters.sink import MailSink, SinkRecorder
from nextron.domain.contactability import key_hash
from nextron.domain.erasure import REDACTED, tombstone_hash
from nextron.ports.errors import AdapterError
from nextron.ports.mail import MailMessage
from nextron.ports.sink import SINK_ERASE_FAILED, SinkErasure, SinkFilesPort
from tests.fakes.clock import FrozenClock
from tests.fakes.mail import InMemoryMail

T = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
EMAIL = "jane.doe@firm.co.uk"
OTHER = "bob@firm.co.uk"
ADDRESS = key_hash("address", EMAIL)


def recorded(folder: Path, clock: FrozenClock) -> SinkRecorder:
    """A recorder whose sink recorded two mails to Jane, one to Bob and an alert, over two
    days."""
    recorder = SinkRecorder(folder, clock)
    mail = MailSink(InMemoryMail(), lambda: (), recorder)
    mail.send(MailMessage(EMAIL, "Quick question", "Hi Jane, about your intake."))
    mail.send(MailMessage(OTHER, "Quick question", "Hi Bob."))
    alert = {"recipient": "-100123", "text": "Hot Lead 01JD3QZ8X5V9K2M7N4P6R8T0WY"}
    recorder.record("alert", alert)
    clock.set(T + timedelta(days=1))
    mail.send(MailMessage(EMAIL, "Following up", "Jane, a follow-up."))
    return recorder


def lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


def test_the_recorder_is_the_sink_files_port(tmp_path: Path) -> None:
    assert isinstance(SinkRecorder(tmp_path, FrozenClock(T)), SinkFilesPort)


def test_the_mails_to_the_address_are_tombstoned_and_nothing_else_changes(tmp_path: Path) -> None:
    clock = FrozenClock(T)
    recorder = recorded(tmp_path, clock)
    first, second = sorted(tmp_path.glob("*.jsonl"))
    before = lines(first)

    assert recorder.erase_recipient(ADDRESS) == SinkErasure(files=2, lines=2)

    after = lines(first)
    assert after[0] == {
        "time": before[0]["time"],
        "port": "mail",
        "recipient": REDACTED,
        "subject": REDACTED,
        "text": REDACTED,
    }
    assert after[1:] == before[1:]  # Bob's mail and the alert, unchanged
    assert [line["recipient"] for line in lines(second)] == [REDACTED]
    assert "jane" not in (first.read_text("ascii") + second.read_text("ascii")).lower()
    assert not list(tmp_path.glob("*.erasing"))


def test_a_second_erasure_finds_nothing_more(tmp_path: Path) -> None:
    recorder = recorded(tmp_path, FrozenClock(T))
    recorder.erase_recipient(ADDRESS)
    contents = {path: path.read_bytes() for path in tmp_path.glob("*.jsonl")}

    assert recorder.erase_recipient(ADDRESS) == SinkErasure(0, 0)
    assert {path: path.read_bytes() for path in tmp_path.glob("*.jsonl")} == contents


def test_no_match_rewrites_no_file(tmp_path: Path) -> None:
    recorder = recorded(tmp_path, FrozenClock(T))
    contents = {path: path.read_bytes() for path in tmp_path.glob("*.jsonl")}

    assert recorder.erase_recipient(key_hash("address", "ann@other.co.uk")) == SinkErasure(0, 0)
    assert {path: path.read_bytes() for path in tmp_path.glob("*.jsonl")} == contents


def test_a_missing_folder_changes_nothing(tmp_path: Path) -> None:
    recorder = SinkRecorder(tmp_path / "never-made", FrozenClock(T))

    assert recorder.erase_recipient(ADDRESS) == SinkErasure(0, 0)
    assert not (tmp_path / "never-made").exists()


def test_other_lines_are_kept_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "2026-11-18.jsonl"
    mail = {"time": "t", "port": "mail", "recipient": EMAIL, "subject": "s", "text": "x"}
    jane = json.dumps(mail)
    kept = [
        b"not json at all\r\n",
        b'["a", "list"]\n',
        b'{"port": "mail", "recipient": 7}\n',
        b'{"time":"t","port":"alert","recipient":"' + EMAIL.encode() + b'","text":"x"}\n',
    ]
    path.write_bytes(b"".join(kept) + jane.encode("ascii") + b"\r\n" + b"\n")

    assert SinkRecorder(tmp_path, FrozenClock(T)).erase_recipient(ADDRESS) == SinkErasure(1, 1)

    content = path.read_bytes()
    assert content.startswith(b"".join(kept))
    erased = content[len(b"".join(kept)) :]
    assert erased.endswith(b"\r\n\n")  # its line ending and the blank line kept
    assert json.loads(erased) == {
        "time": "t",
        "port": "mail",
        "recipient": REDACTED,
        "subject": REDACTED,
        "text": REDACTED,
    }


def test_the_hash_is_the_erased_contacts_stored_address_hash(tmp_path: Path) -> None:
    recorder = recorded(tmp_path, FrozenClock(T))

    assert tombstone_hash("address", EMAIL) == ADDRESS
    assert recorder.erase_recipient(tombstone_hash("address", OTHER)) == SinkErasure(1, 1)


def test_a_leftover_erasing_file_is_deleted_first(tmp_path: Path) -> None:
    recorder = recorded(tmp_path, FrozenClock(T))
    leftover = tmp_path / "2026-11-18.jsonl.erasing"
    leftover.write_bytes(b'{"recipient": "jane.doe@firm.co.uk"}\n')

    recorder.erase_recipient(key_hash("address", "ann@other.co.uk"))

    assert not leftover.exists()


def test_an_unreadable_file_is_a_retryable_sink_erase_failed(tmp_path: Path) -> None:
    (tmp_path / "2026-11-19.jsonl").mkdir()  # can't be read as a file

    with pytest.raises(AdapterError) as failed:
        SinkRecorder(tmp_path, FrozenClock(T)).erase_recipient(ADDRESS)

    assert (failed.value.retryable, failed.value.code) == (True, SINK_ERASE_FAILED)
    assert EMAIL not in str(failed.value) and ADDRESS not in str(failed.value)


def test_the_erasure_holds_the_write_lock(tmp_path: Path) -> None:
    recorder = recorded(tmp_path, FrozenClock(T))
    results: list[SinkErasure] = []
    with sink_adapter._WRITE_LOCK:
        worker = threading.Thread(target=lambda: results.append(recorder.erase_recipient(ADDRESS)))
        worker.start()
        worker.join(0.2)
        assert worker.is_alive() and results == []  # waiting for the lock
    worker.join(5)
    assert results == [SinkErasure(2, 2)]
