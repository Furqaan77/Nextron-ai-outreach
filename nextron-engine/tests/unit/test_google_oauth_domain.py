"""The Google OAuth rules (``nextron.domain.google_oauth``): the grants and keyring names, the
client path and file, the PKCE challenge (RFC 7636 Appendix B), the consent URL's exact
parameters, the ID token's claims, the scope and account checks, and the grant metadata."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import pytest

from nextron.domain import google_oauth as g
from nextron.domain.google_oauth import (
    GRANTS,
    ClientConfig,
    ConsentFailure,
    GoogleGrant,
    GrantMeta,
    InvalidClientFile,
    UnknownGrant,
)
from nextron.ports.secrets import is_secret_name
from tests.fakes.google_oauth import CLIENT_ID, id_token

NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
SECRET = "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"
INTERNAL, POSTMASTER = GRANTS


def client_file(**installed: object) -> bytes:
    body = {"client_id": CLIENT_ID, "client_secret": SECRET, **installed}
    return json.dumps(
        {
            "installed": {
                **body,
                "project_id": "nextron",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "redirect_uris": ["http://localhost"],
            }
        }
    ).encode()


# Endpoints, names and grants --------------------------------------------------------------------


def test_the_endpoints_and_limits() -> None:
    assert g.AUTH_URI == "https://accounts.google.com/o/oauth2/v2/auth"
    assert g.TOKEN_URI == "https://oauth2.googleapis.com/token"
    assert g.REVOKE_URI == "https://oauth2.googleapis.com/revoke"
    assert g.ID_TOKEN_ISSUERS == {"https://accounts.google.com", "accounts.google.com"}
    assert g.CONSENT_TIMEOUT == timedelta(minutes=5)
    assert g.MAX_CLIENT_FILE_BYTES == 65_536
    assert g.INTERNAL_DOMAIN == "nextronai.com"
    assert g.IDENTITY_SCOPES == ("openid", "email")
    assert g.INTERNAL_NOTIFY_DOMAIN == "internal_notify must be a nextronai.com account"


def test_the_grants_in_order_with_their_keyring_names() -> None:
    assert [(spec.grant, spec.label, spec.scope) for spec in GRANTS] == [
        (
            "internal_notify",
            "Internal notifications (internal_notify)",
            "https://www.googleapis.com/auth/gmail.send",
        ),
        (
            "postmaster",
            "Postmaster Tools (postmaster)",
            "https://www.googleapis.com/auth/postmaster.readonly",
        ),
    ]
    assert [(s.secret_name, s.meta_name) for s in GRANTS] == [
        ("google/internal_notify", "meta/google/internal_notify"),
        ("google/postmaster", "meta/google/postmaster"),
    ]
    assert (g.CLIENT_SECRET_NAME, g.CLIENT_META_NAME) == (
        "google/client_config",
        "meta/google/client_config",
    )
    names = [g.CLIENT_SECRET_NAME, g.CLIENT_META_NAME]
    names += [name for spec in GRANTS for name in (spec.secret_name, spec.meta_name)]
    assert all(is_secret_name(name) for name in names)
    assert [str(grant) for grant in GoogleGrant] == ["internal_notify", "postmaster"]


def test_grant_spec_finds_a_grant_and_refuses_anything_else() -> None:
    assert g.grant_spec("postmaster") is POSTMASTER
    assert g.grant_spec(GoogleGrant.INTERNAL_NOTIFY) is INTERNAL
    for value in ("Postmaster", "gmail", "", None, 3, "x" * 40, "<b>"):
        with pytest.raises(UnknownGrant) as caught:
            g.grant_spec(value)
        assert caught.value.code == "unknown_grant"
    with pytest.raises(UnknownGrant, match="not a grant name"):
        g.grant_spec("<script>")


def test_the_client_config_keeps_its_secret_out_of_repr() -> None:
    config = ClientConfig(CLIENT_ID, SECRET)

    assert SECRET not in repr(config)
    assert CLIENT_ID in repr(config)


# The client path --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "path"),
    [
        (r"C:\Users\mac\Downloads\client_secret_1.json", r"C:\Users\mac\Downloads\client_secret_1.json"),
        (r'"C:\Users\mac\Downloads\client_secret_1.json"', r"C:\Users\mac\Downloads\client_secret_1.json"),
        (r'  "C:\x\a.json"  ', r"C:\x\a.json"),
        (r'" C:\x\a.json "', r"C:\x\a.json"),
        (r"d:\x\A.JSON", r"d:\x\A.JSON"),
        ("C:/Users/mac/a.Json", r"C:\Users\mac\a.Json"),
    ],
)
def test_a_full_drive_letter_json_path_is_accepted(typed: str, path: str) -> None:
    assert g.client_path(typed) == path


@pytest.mark.parametrize(
    ("typed", "reason"),
    [
        ("", g.PATH_EMPTY),
        ("   ", g.PATH_EMPTY),
        ('""', g.PATH_EMPTY),
        ('" "', g.PATH_EMPTY),
        (None, g.PATH_EMPTY),
        ("C:\\" + "x" * 1020 + ".json", g.PATH_TOO_LONG),
        ("C:\\x\ta.json", g.PATH_CONTROL),
        ("C:\\x\\a.json\n", g.PATH_CONTROL),
        ("C:\\x\x00.json", g.PATH_CONTROL),
        ("client_secret.json", g.PATH_NOT_LOCAL),
        ("Downloads\\client_secret.json", g.PATH_NOT_LOCAL),
        ("C:client_secret.json", g.PATH_NOT_LOCAL),
        ("\\x\\client_secret.json", g.PATH_NOT_LOCAL),
        ("\\\\srv\\share\\x.json", g.PATH_NOT_LOCAL),
        ("//srv/share/x.json", g.PATH_NOT_LOCAL),
        ("\\\\?\\C:\\x\\a.json", g.PATH_NOT_LOCAL),
        ("\\\\.\\C:\\x\\a.json", g.PATH_NOT_LOCAL),
        ("CC:\\x\\a.json", g.PATH_NOT_LOCAL),
        ("C:\\x\\client_secret.txt", g.PATH_NOT_JSON),
        ("C:\\x\\client_secret.json.exe", g.PATH_NOT_JSON),
        ("C:\\x\\client_secret.json.", g.PATH_NOT_JSON),
        ("C:\\x\\file.txt:s.json", g.PATH_COLON),
        ("C:\\x\\client_secret.json:stream.json", g.PATH_COLON),
        ("C:/x:/a.json", g.PATH_COLON),
        ("C:\\", g.PATH_NOT_JSON),
    ],
)
def test_any_other_path_is_refused_without_quoting_it(typed: object, reason: str) -> None:
    with pytest.raises(InvalidClientFile) as caught:
        g.client_path(typed)

    assert caught.value.reason == reason
    assert caught.value.code == "invalid_client_file"
    if isinstance(typed, str) and typed.strip(' "'):
        assert typed.strip(' "') not in str(caught.value)


def test_exactly_1024_characters_is_accepted() -> None:
    path = "C:\\" + "x" * (1024 - 8) + ".json"
    assert len(path) == 1024

    assert g.client_path(path) == path
    with pytest.raises(InvalidClientFile):
        g.client_path("C:\\" + "x" * (1024 - 7) + ".json")


# The client file --------------------------------------------------------------------------------


def test_an_installed_client_file_keeps_only_the_id_and_secret() -> None:
    config = g.parse_client_file(client_file())

    assert config == ClientConfig(CLIENT_ID, SECRET)
    assert g.encode_client_config(config) == (
        f'{{"client_id":"{CLIENT_ID}","client_secret":"{SECRET}"}}'
    )
    assert g.decode_client_config(g.encode_client_config(config)) == config


def test_a_utf8_bom_is_allowed() -> None:
    assert g.parse_client_file(b"\xef\xbb\xbf" + client_file()).client_id == CLIENT_ID


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"not json", g.NOT_JSON),
        (b"\xff\xfe{}", g.NOT_JSON),
        ("{}".encode("utf-16"), g.NOT_JSON),
        (b"[" * 100_000, g.NOT_JSON),
        (b"[]", g.NOT_INSTALLED),
        (b'"installed"', g.NOT_INSTALLED),
        (b"{}", g.NOT_INSTALLED),
        (b'{"installed": "x"}', g.NOT_INSTALLED),
        (b'{"installed": []}', g.NOT_INSTALLED),
        (json.dumps({"web": {"client_id": CLIENT_ID, "client_secret": SECRET}}).encode(), g.WEB_CLIENT),
        (client_file(client_id="123-abc.apps.example.com"), g.BAD_CLIENT_ID),
        (client_file(client_id="abc-def.apps.googleusercontent.com"), g.BAD_CLIENT_ID),
        (client_file(client_id="123-ABC.apps.googleusercontent.com"), g.BAD_CLIENT_ID),
        (client_file(client_id=f"{CLIENT_ID}\n"), g.BAD_CLIENT_ID),
        (client_file(client_id=7), g.BAD_CLIENT_ID),
        (client_file(client_id=None), g.BAD_CLIENT_ID),
        (client_file(client_secret=""), g.BAD_CLIENT_SECRET),
        (client_file(client_secret="has space"), g.BAD_CLIENT_SECRET),
        (client_file(client_secret="x" * 201), g.BAD_CLIENT_SECRET),
        (client_file(client_secret="caf\u00e9"), g.BAD_CLIENT_SECRET),
        (client_file(client_secret=None), g.BAD_CLIENT_SECRET),
    ],
)
def test_a_file_that_is_not_an_installed_client_is_refused(raw: bytes, reason: str) -> None:
    with pytest.raises(InvalidClientFile) as caught:
        g.parse_client_file(raw)

    assert caught.value.reason == reason
    assert SECRET not in str(caught.value)


def test_the_longest_valid_id_and_secret_are_accepted() -> None:
    long_id = "1" * 30 + "-" + "a" * 64 + ".apps.googleusercontent.com"
    raw = client_file(client_id=long_id, client_secret="~" * 200)

    config = g.parse_client_file(raw)

    assert (config.client_id, config.client_secret) == (long_id, "~" * 200)


@pytest.mark.parametrize(
    "text",
    [
        None,
        7,
        "",
        "not json",
        "[]",
        '{"client_id": "x"}',
        json.dumps({"client_id": CLIENT_ID, "client_secret": SECRET, "extra": 1}),
        json.dumps({"client_id": "bad", "client_secret": SECRET}),
        json.dumps({"client_id": CLIENT_ID, "client_secret": "has space"}),
    ],
)
def test_decoding_anything_else_gives_none(text: object) -> None:
    assert g.decode_client_config(text) is None


# PKCE and the consent URL -----------------------------------------------------------------------


def test_the_challenge_matches_rfc_7636_appendix_b() -> None:
    assert (
        g.code_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_the_consent_url_has_exactly_these_parameters_in_order() -> None:
    url = g.authorization_url(
        CLIENT_ID, INTERNAL, "http://127.0.0.1:53682/", "the-state", "the-challenge"
    )

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == g.AUTH_URI
    assert parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True) == [
        ("response_type", "code"),
        ("client_id", CLIENT_ID),
        ("redirect_uri", "http://127.0.0.1:53682/"),
        ("scope", "https://www.googleapis.com/auth/gmail.send openid email"),
        ("state", "the-state"),
        ("code_challenge", "the-challenge"),
        ("code_challenge_method", "S256"),
        ("access_type", "offline"),
        ("prompt", "select_account consent"),
    ]
    assert "include_granted_scopes" not in url
    assert "+" not in parts.query  # spaces are %20


def test_the_postmaster_url_asks_for_its_scope() -> None:
    url = g.authorization_url(CLIENT_ID, POSTMASTER, "http://127.0.0.1:1/", "s", "c")

    assert dict(parse_qsl(urlsplit(url).query))["scope"] == (
        "https://www.googleapis.com/auth/postmaster.readonly openid email"
    )


def test_the_scope_only_url_asks_for_exactly_its_scope_with_the_same_parameters() -> None:
    gmail = "https://www.googleapis.com/auth/gmail.modify"
    url = g.authorization_url_for_scope(
        CLIENT_ID, gmail, "http://127.0.0.1:53682/", "the-state", "the-challenge"
    )

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == g.AUTH_URI
    assert parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True) == [
        ("response_type", "code"),
        ("client_id", CLIENT_ID),
        ("redirect_uri", "http://127.0.0.1:53682/"),
        ("scope", gmail),  # no openid email: the address comes from users.getProfile
        ("state", "the-state"),
        ("code_challenge", "the-challenge"),
        ("code_challenge_method", "S256"),
        ("access_type", "offline"),
        ("prompt", "select_account consent"),
    ]
    assert "openid" not in url and "include_granted_scopes" not in url


def test_has_scope_finds_one_scope_among_the_granted_ones() -> None:
    gmail = "https://www.googleapis.com/auth/gmail.modify"
    assert g.has_scope(f"openid {gmail}", gmail)
    assert not g.has_scope("https://www.googleapis.com/auth/gmail.readonly", gmail)
    assert not g.has_scope(f"{gmail}x", gmail)
    assert not g.has_scope(None, gmail)


# The ID token -----------------------------------------------------------------------------------


def test_the_id_token_gives_the_verified_email() -> None:
    assert g.id_token_account(id_token("mac@nextronai.com"), CLIENT_ID, NOW) == "mac@nextronai.com"
    assert (
        g.id_token_account(id_token("x@gmail.com", iss="accounts.google.com"), CLIENT_ID, NOW)
        == "x@gmail.com"
    )


@pytest.mark.parametrize(
    "token",
    [
        id_token(aud="other.apps.googleusercontent.com"),
        id_token(aud=[CLIENT_ID]),
        id_token(aud=None),
        id_token(iss="https://evil.example"),
        id_token(iss=None),
        id_token(exp=int(NOW.timestamp())),
        id_token(exp=int(NOW.timestamp()) - 1),
        id_token(exp="9999999999"),
        id_token(exp=True),
        id_token(exp=10**400),  # too large for a float
        id_token(email_verified=False),
        id_token(email_verified="true"),
        id_token(email_verified=None),
        id_token(email=None),
        id_token("-1001234567"),  # a chat id is a sink recipient, but not an address
        id_token("not an address"),
        id_token("ma\u00efl@nextronai.com"),
        "",
        "abc",
        "a.b",
        "a.b.c.d",
        "a.!!!.c",
        "a." + "e30" + ".c",  # {} : no claims
        "a.W10.c",  # [] : not an object
        None,
        7,
    ],
)
def test_an_id_token_that_breaks_a_rule_gives_no_account(token: object) -> None:
    assert g.id_token_account(token, CLIENT_ID, NOW) is None


def test_an_exp_one_second_ahead_is_accepted() -> None:
    token = id_token(exp=int(NOW.timestamp()) + 1)

    assert g.id_token_account(token, CLIENT_ID, NOW) == "mac@nextronai.com"


# Scopes and accounts ----------------------------------------------------------------------------


def test_the_scope_must_be_among_the_granted_scopes() -> None:
    assert g.scope_granted("openid https://www.googleapis.com/auth/gmail.send email", INTERNAL)
    assert not g.scope_granted("openid email", INTERNAL)
    assert not g.scope_granted("https://www.googleapis.com/auth/gmail.sendx", INTERNAL)
    assert not g.scope_granted(None, INTERNAL)
    assert g.scope_granted("https://www.googleapis.com/auth/postmaster.readonly", POSTMASTER)


@pytest.mark.parametrize(
    ("spec", "account", "env", "allowlist", "problem"),
    [
        (INTERNAL, "mac@nextronai.com", "prod", [], None),
        (INTERNAL, "Mac@NextronAI.COM", "prod", [], None),
        (INTERNAL, "x@gmail.com", "prod", [], ConsentFailure.NOT_INTERNAL_DOMAIN),
        (INTERNAL, "x@mail.nextronai.com", "prod", [], ConsentFailure.NOT_INTERNAL_DOMAIN),
        (INTERNAL, "x@nextronai.com.evil.example", "prod", [], ConsentFailure.NOT_INTERNAL_DOMAIN),
        (INTERNAL, "x@gmail.com", "dev", ["x@gmail.com"], ConsentFailure.NOT_INTERNAL_DOMAIN),
        (INTERNAL, "mac@nextronai.com", "dev", ["mac@nextronai.com"], None),
        (INTERNAL, "mac@nextronai.com", "dev", ["MAC@nextronai.com"], None),
        (INTERNAL, "mac@nextronai.com", "dev", [], ConsentFailure.NOT_ALLOWLISTED),
        (INTERNAL, "mac@nextronai.com", "test", ["other@nextronai.com"], ConsentFailure.NOT_ALLOWLISTED),
        (POSTMASTER, "x@gmail.com", "prod", [], None),
        (POSTMASTER, "x@gmail.com", "dev", ["x@gmail.com"], None),
        (POSTMASTER, "x@gmail.com", "test", [], ConsentFailure.NOT_ALLOWLISTED),
        (POSTMASTER, "x@gmail.com", "PROD", [], ConsentFailure.NOT_ALLOWLISTED),
    ],
)
def test_account_problem(
    spec: g.GrantSpec, account: str, env: str, allowlist: list[str], problem: object
) -> None:
    assert g.account_problem(spec, account, env, allowlist) == problem


def test_same_account_compares_after_ascii_lowercasing() -> None:
    assert g.same_account("Mac@NextronAI.com", "mac@nextronai.com")
    assert not g.same_account("mac@nextronai.com", "mac2@nextronai.com")
    assert not g.same_account(None, "mac@nextronai.com")
    assert not g.same_account("stra\u00dfe@x.com", "strasse@x.com")


# Grant metadata ---------------------------------------------------------------------------------


def test_grant_meta_round_trips_as_compact_json() -> None:
    meta = GrantMeta("mac@nextronai.com", NOW)
    marked = GrantMeta("mac@nextronai.com", NOW, NOW + timedelta(days=1))

    assert g.encode_grant_meta(meta) == (
        '{"account":"mac@nextronai.com","granted_at":"2026-11-17T09:30:00.000000Z"}'
    )
    assert g.encode_grant_meta(marked).endswith(
        ',"needs_reconsent_at":"2026-11-18T09:30:00.000000Z"}'
    )
    assert g.decode_grant_meta(g.encode_grant_meta(meta)) == meta
    assert g.decode_grant_meta(g.encode_grant_meta(marked)) == marked
    assert g.decode_grant_meta(g.encode_grant_meta(GrantMeta(None, NOW))) == GrantMeta(None, NOW)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "[]",
        '{"account": "a@b.co"}',
        '{"account": "a@b.co", "granted_at": "2026-11-17"}',
        '{"account": 7, "granted_at": "2026-11-17T09:30:00.000000Z"}',
        '{"account": "a@b.co", "granted_at": "2026-11-17T09:30:00.000000Z", "x": 1}',
        '{"account": "a@b.co", "granted_at": "2026-11-17T09:30:00.000000Z", '
        '"needs_reconsent_at": null}',
        '{"granted_at": "2026-11-17T09:30:00.000000Z"}',
    ],
)
def test_decoding_other_meta_gives_none(text: object) -> None:
    assert g.decode_grant_meta(text) is None
