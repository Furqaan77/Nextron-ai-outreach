"""``nextron.web.forms.upload_form`` (AD-13, Story 2.3): the strict ``multipart/form-data``
parser: the ``Content-Type`` and boundary rules, the body layout, the part headers, text and
file parts, repeated names, and the size limit's drain."""

import asyncio
from typing import Any

import pytest
from fastapi import Request

from nextron.web.forms import (
    BAD_BODY,
    MAX_UPLOAD_BODY,
    NOT_MULTIPART,
    REPEATED_FIELD,
    MalformedForm,
    UploadForm,
    multipart_boundary,
    parse_multipart,
    upload_form,
)
from tests.dashboard import UPLOAD_BOUNDARY, upload

B = b"XyZ"
CT = b"multipart/form-data; boundary=XyZ"


def run(headers: list[tuple[bytes, bytes]], chunks: list[bytes]) -> tuple[UploadForm, int]:
    """``upload_form`` on a request whose body arrives in ``chunks``; also how many receive
    calls it made."""
    sent = list(chunks)
    calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        body = sent.pop(0) if sent else b""
        return {"type": "http.request", "body": body, "more_body": bool(sent)}

    scope = {"type": "http", "method": "POST", "headers": headers, "path": "/x"}
    return asyncio.run(upload_form(Request(scope, receive))), calls


def body(*parts: bytes, boundary: bytes = B, final_crlf: bool = True) -> bytes:
    delimiter = b"--" + boundary
    text = delimiter + b"\r\n" + (b"\r\n" + delimiter + b"\r\n").join(parts)
    text += b"\r\n" + delimiter + b"--"
    return text + b"\r\n" if final_crlf else text


def text_part(name: str, value: bytes) -> bytes:
    return f'Content-Disposition: form-data; name="{name}"'.encode() + b"\r\n\r\n" + value


def file_part(name: str, value: bytes, filename: str = "a.csv") -> bytes:
    head = f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
    return head + b"\r\nContent-Type: text/csv\r\n\r\n" + value


def parsed(data: bytes) -> UploadForm:
    return parse_multipart(data, B)


def refused(data: bytes) -> str:
    with pytest.raises(MalformedForm) as caught:
        parsed(data)
    return caught.value.reason


# The Content-Type -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("multipart/form-data; boundary=XyZ", b"XyZ"),
        ("Multipart/Form-Data;boundary=XyZ", b"XyZ"),
        ('multipart/form-data; boundary="a b:c"', b"a b:c"),
        (
            "multipart/form-data; BOUNDARY=----WebKitFormBoundary7MA4YWxkTrZu0gW",
            b"----WebKitFormBoundary7MA4YWxkTrZu0gW",
        ),
        ("multipart/form-data; charset=utf-8; boundary=x", b"x"),
        ("multipart/form-data; boundary=" + "b" * 70, b"b" * 70),
    ],
)
def test_the_boundary(value: str, expected: bytes) -> None:
    assert multipart_boundary(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "multipart/form-data; boundary=",
        'multipart/form-data; boundary=""',
        "multipart/form-data; boundary=" + "b" * 71,
        'multipart/form-data; boundary="ends in space "',
        "multipart/form-data; boundary=a\x00b",
        "multipart/form-data; boundary=a;boundary=b",
        "multipart/form-data; boundary=x;",
        "multipart/form-data; boundary=x; junk",
        "multipart/mixed; boundary=x",
        'multipart/form-data; boundary="a;b"',
    ],
)
def test_a_wrong_content_type_is_not_multipart(value: str) -> None:
    with pytest.raises(MalformedForm) as caught:
        multipart_boundary(value)

    assert caught.value.reason == NOT_MULTIPART


@pytest.mark.parametrize(
    "headers",
    [[], [(b"content-type", CT), (b"content-type", CT)], [(b"content-type", b"text/plain")]],
)
def test_upload_form_needs_exactly_one_multipart_content_type(
    headers: list[tuple[bytes, bytes]],
) -> None:
    with pytest.raises(MalformedForm) as caught:
        run(headers, [body(text_part("a", b"1"))])

    assert caught.value.reason == NOT_MULTIPART


# The body ---------------------------------------------------------------------------------------


def test_texts_and_files() -> None:
    form = parsed(
        body(
            text_part("csrf_token", b"tok"),
            text_part("origin", "Café list".encode()),
            text_part("default_segment", b""),
            file_part("file", b"a,b\r\n-XyZ--\r\n\r\nc"),
        )
    )

    assert dict(form.texts) == {
        "csrf_token": "tok",
        "origin": "Café list",
        "default_segment": "",
    }
    assert dict(form.files) == {"file": b"a,b\r\n-XyZ--\r\n\r\nc"}
    assert not form.too_large


def test_a_file_part_keeps_its_bytes_exactly() -> None:
    content = b"\xef\xbb\xbfName,Website\r\n\x00\xff binary\r\n\r\n"
    form = parsed(body(file_part("file", content), text_part("x", b"")))

    assert form.files["file"] == content
    assert form.texts["x"] == ""


def test_an_empty_file_name_is_still_a_file() -> None:
    form = parsed(body(file_part("file", b"", filename="")))

    assert dict(form.files) == {"file": b""}


def test_without_the_final_crlf() -> None:
    form = parsed(body(text_part("a", b"1"), final_crlf=False))

    assert dict(form.texts) == {"a": "1"}


def test_the_browser_body_the_test_dashboard_builds() -> None:
    data, headers = upload(b"x,y\r\n", origin="List", default_segment="")
    [(_, content_type)] = headers

    form = parse_multipart(data, multipart_boundary(content_type.decode()))

    assert UPLOAD_BOUNDARY.encode() in data
    assert dict(form.texts) == {"origin": "List", "default_segment": ""}
    assert dict(form.files) == {"file": b"x,y\r\n"}


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"--XyZ--\r\n",  # no part
        b"--XyZ\r\n--XyZ--",  # the opening and closing overlap
        body(text_part("a", b"1"))[:-3],  # cut
        b"preamble\r\n" + body(text_part("a", b"1")),
        body(text_part("a", b"1")) + b"epilogue",
        body(text_part("a", b"1"), boundary=b"Other"),
        body(text_part("a", b"1\r\n--XyZ-- early")),  # a part holding a delimiter
        body(text_part("a", b"1\r\n--XyZx")),
        body(b'Content-Disposition: form-data; name="a"\r\n1'),  # no blank line
        body(b'Content-Disposition: form-data; name="a"; filename="f"; x="y"\r\n\r\n1'),
        body(b'Content-Disposition: attachment; name="a"\r\n\r\n1'),
        body(b"Content-Disposition: form-data; name=a\r\n\r\n1"),
        body(b'Content-Disposition: form-data; name=""\r\n\r\n1'),
        body(b'Content-Type: text/plain\r\n\r\n1'),  # no disposition
        body(
            b'Content-Disposition: form-data; name="a"\r\n'
            b'Content-Disposition: form-data; name="b"\r\n\r\n1'
        ),
        body(
            b'Content-Disposition: form-data; name="a"\r\n'
            b"Content-Type: text/plain\r\nContent-Type: text/csv\r\n\r\n1"
        ),
        body(b'Content-Disposition: form-data; name="a"\r\nX-Other: 1\r\n\r\n1'),
        body(b'Content-Disposition: form-data; name="a"\r\nno colon\r\n\r\n1'),
        body(b'Content-Disposition : form-data; name="a"\r\n\r\n1'),
        body(b'Content-Disposition: form-data; name="\xff"\r\n\r\n1'),  # header not UTF-8
        body(text_part("a", b"\xff\xfe")),  # a text part that isn't UTF-8
    ],
)
def test_a_malformed_body_is_bad_body(data: bytes) -> None:
    assert refused(data) == BAD_BODY


@pytest.mark.parametrize(
    "parts",
    [
        (text_part("a", b"1"), text_part("a", b"2")),
        (text_part("file", b"1"), file_part("file", b"2")),
        (file_part("file", b"1"), file_part("file", b"2")),
    ],
)
def test_a_repeated_name_is_refused(parts: tuple[bytes, ...]) -> None:
    assert refused(body(*parts)) == REPEATED_FIELD


def test_header_names_are_case_insensitive() -> None:
    form = parsed(body(b'content-disposition: form-data; name="a"\r\nCONTENT-TYPE: x\r\n\r\n1'))

    assert dict(form.texts) == {"a": "1"}


# Streaming and the size limit -------------------------------------------------------------------


def test_upload_form_reads_a_body_sent_in_chunks() -> None:
    data = body(text_part("origin", b"List"), file_part("file", b"x" * 1000))
    chunks = [data[i : i + 97] for i in range(0, len(data), 97)]

    form, _ = run([(b"content-type", CT)], chunks)

    assert form.texts["origin"] == "List"
    assert form.files["file"] == b"x" * 1000


def test_a_body_at_the_limit_is_parsed() -> None:
    head = body(text_part("a", b"1"), file_part("file", b""))
    padding = MAX_UPLOAD_BODY - len(head)
    data = body(text_part("a", b"1"), file_part("file", b"x" * padding))

    assert len(data) == MAX_UPLOAD_BODY
    form, _ = run([(b"content-type", CT)], [data])
    assert len(form.files["file"]) == padding


def test_a_body_over_the_limit_is_drained_and_discarded() -> None:
    chunk = b"x" * (1024 * 1024)
    chunks = [chunk] * 8  # 8 MiB, well past the limit, and not even multipart

    form, calls = run([(b"content-type", CT)], chunks)

    assert form == UploadForm(too_large=True)
    assert dict(form.texts) == {} and dict(form.files) == {}
    assert calls == len(chunks)  # every chunk was read, so the client isn't left hanging


def test_one_byte_over_the_limit_is_too_large() -> None:
    head = body(text_part("a", b"1"), file_part("file", b""))
    data = body(text_part("a", b"1"), file_part("file", b"x" * (MAX_UPLOAD_BODY - len(head) + 1)))

    form, _ = run([(b"content-type", CT)], [data])

    assert form.too_large
