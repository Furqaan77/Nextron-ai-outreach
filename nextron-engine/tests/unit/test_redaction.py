"""``nextron.domain.redaction.redact``: every rule, the rule order, ULIDs and idempotence.

Secret-shaped values are assembled at run time so the source holds no literal that a secret
scanner would flag; every value here is fake.
"""

import random
import time

import pytest

from nextron.domain.redaction import redact

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
ANTHROPIC_KEY = "sk-ant-" + "api03-" + "FAKE0" * 8
FORTY_CHAR_KEY = "Fk" * 20
BOT_TOKEN = "123456789:" + "AA" + "Fake_Token-" * 3
BASIC_CREDENTIAL = "dXNl" + "cjpwYXNz"
BASE64_BLOB = "QUJD" * 6 + "+" + "MTIz" * 3 + "=="


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Query strings and fragments: scheme, host and path stay.
        ("https://h/p?key=abc&e=a@b.co", "https://h/p"),
        ("GET https://x.io/a/b?t=1 ok", "GET https://x.io/a/b ok"),
        ("see https://x.io?token=abc now", "see https://x.io now"),
        ("https://x.io/cb#access_token=abc", "https://x.io/cb"),
        # The query runs to the next whitespace, past quotes.
        ("'https://x.io/p?k=1', \"http://y.io/q?k=2\"", "'https://x.io/p \"http://y.io/q"),
        ('GET "https://x.io/p?k=v" done', 'GET "https://x.io/p done'),
        # A bare dotted host name, with or without a port, loses its query too.
        ("call api.example.com?key=abc now", "call api.example.com now"),
        ("10.0.0.1:8080#frag x", "10.0.0.1:8080 x"),
        # A relative path keeps its path, as in Uvicorn's access log.
        (
            '127.0.0.1:50000 - "GET /enter?code=abc HTTP/1.1" 200',
            '127.0.0.1:50000 - "GET /enter HTTP/1.1" 200',
        ),
        # Prose question marks are not query strings.
        ("is it done? yes", "is it done? yes"),
        ("ready/steady?", "ready/steady?"),
        ("version 1.2? maybe", "version 1.2? maybe"),
        # URL userinfo.
        ("postgres://admin:pw@db.local:5432/x", "postgres://<redacted>@db.local:5432/x"),
        ("https://u:p@host.io/x", "https://<redacted>@host.io/x"),
        # Telegram bot tokens.
        (
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            "https://api.telegram.org/bot<redacted>/sendMessage",
        ),
        (
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/photos/f.jpg",
            "https://api.telegram.org/file/bot<redacted>/photos/f.jpg",
        ),
        # Bearer tokens, in any case.
        ("Authorization: Bearer xyz", "Authorization: Bearer <redacted>"),
        ("authorization: bearer abc.def-ghi", "authorization: Bearer <redacted>"),
        ("BEARER\tq1w2e3, then", "Bearer <redacted>, then"),
        # Basic credentials, in any case.
        (f"Authorization: Basic {BASIC_CREDENTIAL}", "Authorization: Basic <redacted>"),
        (f"authorization: BASIC {BASIC_CREDENTIAL}", "authorization: Basic <redacted>"),
        # Credential fields: the name stays and the value goes, at any length.
        ("x-api-key: abc123", "x-api-key: <redacted>"),
        ("X-Goog-Api-Key: zz", "X-Goog-Api-Key: <redacted>"),
        (
            "api_key=1 apikey:2 API-KEY = 3",
            "api_key=<redacted> apikey:<redacted> API-KEY = <redacted>",
        ),
        ('{"password": "hunter2", "lead_id": "x"}', '{"password": "<redacted>", "lead_id": "x"}'),
        ("{'token': 'x'}", "{'token': '<redacted>'}"),
        ("client_secret=s3&grant=code", "client_secret=<redacted>&grant=code"),
        (
            "access_token=a.b.c refresh_token: r",
            "access_token=<redacted> refresh_token: <redacted>",
        ),
        ("id_token=jwt secret=s", "id_token=<redacted> secret=<redacted>"),
        ("max_tokens=1024 token_count=5", "max_tokens=1024 token_count=5"),
        # Email addresses.
        ("from ann@x.co today", "from <email> today"),
        ("ann.lee+tag@mail.example.co.uk", "<email>"),
        ("<ann@x.co>", "<<email>>"),
        ("a@b.co_c@d.io", "<email><email>"),
        # URL-encoded (%40, any case) and Unicode addresses.
        ("ann%40x.co", "<email>"),
        ("ANN%40X.CO", "<email>"),
        ("/u/ann%40x.co/x", "/u/<email>/x"),
        ("josé@exämple.de", "<email>"),
        ("用户@例子.广告", "<email>"),
        # A local part over 64 characters leaves nothing in front of the placeholder.
        ("x" * 70 + "@x.co", "<email>"),
        ("to pre_" + "x" * 70 + "@x.co now", "to <email> now"),
        # Keys.
        (f"key {ANTHROPIC_KEY} used", "key <key> used"),
        ("sk-ant-short", "<key>"),
        (FORTY_CHAR_KEY, "<key>"),
        ("x" * 32, "<key>"),
        ("x" * 31, "x" * 31),
        # Standard base64 with "+" or "=" padding.
        (f"blob {BASE64_BLOB} end", "blob <key> end"),
        ("A" * 43 + "=", "<key>"),
        ("Zm9v" * 8 + "Zg==", "<key>"),
        # A slash-only run is a path: only a long segment goes.
        ("/files/" + "Fk" * 20 + "/x", "/files/<key>/x"),
        # The design-notes example.
        (
            f"GET https://x.io/a?t=1 by ann@x.co key {ANTHROPIC_KEY}",
            "GET https://x.io/a by <email> key <key>",
        ),
        ("", ""),
    ],
)
def test_each_rule(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_unsubscribe_tokens_and_keys_are_masked_whole() -> None:
    """Story 3.8: a token (108 base64url characters) and a token key (43, or 44 with "=") are
    always masked by rules 8 and 9, wherever their alphabet puts "-" or "_"."""
    token = "AXc6" + "DVMU-cvH_lYM" * 8 + "Oex6ezXn"  # 108 characters
    key = "m6wm" + "G91-MEwR" * 4 + "Pu_7A01"  # 43 characters

    assert len(token) == 108 and len(key) == 43
    assert redact(f"token {token} sent") == "token <key> sent"
    link = f"link https://u.example.com/u/{token} ok"
    assert redact(link) == "link https://u.example.com/u/<key> ok"
    assert redact(f"k_tok={key}") == "k_tok=<key>"
    assert redact(f"k_edge {key}=") == "k_edge <key>="  # the padding alone is no secret
    assert redact(f"/{token}/{key}") == "/<key>/<key>"
    # a log line holding both a token and the address it was issued for
    line = f"unsubscribe from jane.doe+news@firm.co.uk with token {token} done"
    assert redact(line) == "unsubscribe from <email> with token <key> done"
    assert redact(f"opt-out jane.doe@firm.co.uk/{token}") == "opt-out <email>/<key>"


def test_query_string_is_removed_before_its_email_and_key_are_seen() -> None:
    text = f"https://h.io/p?e=ann@x.co&k={FORTY_CHAR_KEY}"

    assert redact(text) == "https://h.io/p"


def test_ulids_survive_in_text_ids_and_paths() -> None:
    text = (
        f"lead {ULID} lead_id={ULID} /leads/{ULID} [{ULID}] /leads/{ULID}/evidence/{ULID} "
        f"https://x.io/v1/leads/{ULID}"
    )

    assert redact(text) == text


def test_mixed_text() -> None:
    text = (
        f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id=42 for lead {ULID} "
        f"(Authorization: Bearer abc123) failed; contact ann@x.co, key {ANTHROPIC_KEY}, "
        f"token {FORTY_CHAR_KEY}"
    )

    assert redact(text) == (
        f"POST https://api.telegram.org/bot<redacted>/sendMessage for lead {ULID} "
        "(Authorization: Bearer <redacted>) failed; contact <email>, key <key>, token <key>"
    )


def test_multiline_traceback_text() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "adapter.py", line 3, in call\n'
        f"RuntimeError: GET https://h.io/x?api_key=abc for ann@x.co with {FORTY_CHAR_KEY}\n"
    )

    assert redact(text) == (
        "Traceback (most recent call last):\n"
        '  File "adapter.py", line 3, in call\n'
        "RuntimeError: GET https://h.io/x for <email> with <key>\n"
    )


FRAGMENTS = [
    "https://", "h.io", "x.io", "api.example.com", "10.0.0.1", ":8080", "/", "//", "?", "#", "=",
    "==", "&", "a@b.co", "@", "%40", "ann%40x.co", ".", "bot", "/bot", "123", ":", "Bearer ",
    "bearer\t", "BEARER ", "Basic ", "basic ", "sk-ant-", "X" * 20, "Y" * 15, "q" * 40, " ", "\n",
    '"', "'", "`", "<", ">", "-", "_", ",", ";", "(", ")", "%", "+", ULID, "<key>", "<redacted>",
    "<email>", f"/bot{BOT_TOKEN}", "ann.lee+t@ex.co.uk", "x" * 64, "josé", "é", "token=",
    "api_key: ", "x-api-key: ", '"password": "', "secret=", "u:p@", "AAAA+BBBB/CCCC", "QUJD" * 9,
    "Zm9v=",
]  # fmt: skip


def test_redact_is_idempotent_on_random_mixtures() -> None:
    rng = random.Random(20261116)
    for _ in range(20_000):
        text = "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(1, 30)))
        once = redact(text)

        assert redact(once) == once, text


@pytest.mark.parametrize(
    "text",
    [
        "A" * 200_000,
        "A" * 200_000 + "@",
        "a@" * 100_000,
        "/" * 100_000 + "?x",
        ("x" * 63 + "@") * 3_000,
        "bearer " * 30_000,
        "a=" * 100_000,
        "token=" * 30_000,
        "//" * 100_000 + "@",
        "a%40" * 50_000,
        "A+" * 100_000,
        "x.io?" * 40_000,
        "é" * 200_000 + "@",
        ("a" * 300 + ".") * 500 + "?x",
    ],
)
def test_long_inputs_take_linear_time(text: str) -> None:
    started = time.perf_counter()
    redact(text)

    # Each case takes well under a second; a pattern that rescans from every position would
    # take minutes on these inputs.
    assert time.perf_counter() - started < 10
