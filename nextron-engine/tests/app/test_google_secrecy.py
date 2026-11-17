"""The planted-secret test for Google OAuth (FR-56, NFR-18, AD-12, AD-17): through the dashboard
with fakes and a database at head, a client is imported, granted, refreshed, re-granted (with a
refusal in between) and removed. The planted client secret, refresh tokens, access tokens,
authorization code and file path, and the flow's own state, verifier and consent URL, then
appear in no HTTP response, no log record (checked before redaction), no error message, no
database column and no file. Only the client secret and the live refresh token are in the
keyring; no access token, code, state, verifier, URL or path ever is.

The planted Google accounts and the raw ID tokens are checked too: an account appears in no log
record, error, database column or file (the page and the keyring meta may show it), and an ID
token nowhere at all, the keyring included.

The planted values are 20 letters and digits that no redaction rule masks, so only their absence
passes; the accounts and ID tokens would be masked, which is why every log check reads the
records before redaction. ``tests/app/test_credential_secrecy.py`` proves each check finds a value
where it is.
"""

import logging
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import Engine

from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.redaction import redact
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import TokenSet
from tests.app.test_credential_secrecy import (
    database_leaks,
    file_leaks,
    log_leaks,
    response_leaks,
)
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.google_oauth import CLIENT_ID, id_token

CLIENT_SECRET = "Qs4Vr7Lx2Mz9Nw5Ht8Pk"
REFRESH_1 = "Rf3Kd8Lq2Wz7Nx5Vb9Hm"
REFRESH_2 = "Rg6Tn2Xw9Lp4Kz7Mq3Vd"
REFUSED_REFRESH = "Rh8Bq5Wm2Zx7Lk4Nt9Pv"
ACCESS_1 = "Ac9Tm4Xp7Lq2Zr6Wn3Kd"
ACCESS_2 = "Ad2Wq8Zk5Lm3Xt7Nv6Rb"
CODE = "Cd5Hx8Jq2Lw7Mz3Np6Rb"
PATH_NAME = "Pt6Wq3Zm8Kx2Lr7Nv4Bh"
PATH = rf"C:\Users\mac\Downloads\{PATH_NAME}.json"
PLANTED = {
    "client secret": CLIENT_SECRET,
    "refresh token 1": REFRESH_1,
    "refresh token 2": REFRESH_2,
    "refused refresh token": REFUSED_REFRESH,
    "access token 1": ACCESS_1,
    "access token 2": ACCESS_2,
    "code": CODE,
    "path": PATH_NAME,
}
CLIENT_FILE = (
    '{"installed": {"client_id": "%s", "client_secret": "%s"}}' % (CLIENT_ID, CLIENT_SECRET)
).encode()
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send openid email"
ACCOUNT_1 = "Ka7Wz3Lq@nextronai.com"
ACCOUNT_2 = "Kb4Xr8Mp@nextronai.com"
REFUSED_ACCOUNT = "Kc2Vn5Ht@gmail.com"
ACCOUNTS = {"account 1": ACCOUNT_1, "account 2": ACCOUNT_2, "refused account": REFUSED_ACCOUNT}
ID_TOKENS = {f"ID token of {what}": id_token(email) for what, email in ACCOUNTS.items()}


def tokens(access: str, refresh: str | None, email: str) -> TokenSet:
    return TokenSet(access, 3600, GMAIL_SCOPE, refresh, id_token(email))


def test_the_planted_values_are_ones_no_redaction_rule_masks() -> None:
    for value in PLANTED.values():
        assert redact(f"value {value} here") == f"value {value} here"


def test_google_secrets_appear_in_no_response_log_record_error_or_database_column(
    units: UnitOfWorkFactory,
    db_engine: Engine,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    dashboard = Dashboard(units=units, allowlist=[ACCOUNT_1, ACCOUNT_2])
    cookie = dashboard.open_session()
    token = dashboard.csrf_token(cookie)
    replies: list[Reply] = []
    errors: list[AdapterError] = []
    written: list[str] = []  # every value written to Credential Manager
    real_write = dashboard.secrets.write

    def recording_write(name: str, value: str) -> None:
        written.append(value)
        real_write(name, value)

    dashboard.secrets.write = recording_write  # type: ignore[method-assign]

    def post(path: str, body: bytes, *, header_token: bool = True) -> Reply:
        replies.append(
            dashboard.post(
                path,
                cookie=cookie,
                token=token if header_token else None,
                headers=[(b"content-type", FORM)],
                body=body,
            )
        )
        return replies[-1]

    def get(path: str) -> Reply:
        replies.append(dashboard.get(path, cookie=cookie))
        return replies[-1]

    def grant(exchanged: TokenSet) -> Reply:
        dashboard.oauth.exchanges.append(exchanged)
        started = post("/credentials/google/grant", form(csrf_token=token, grant="internal_notify"))
        assert started.status == 200 and "Waiting for consent" in started.text
        state = parse_qs(urlsplit(dashboard.browser.urls[-1]).query)["state"][0]
        dashboard.listeners[-1].answer = {"state": (state,), "code": (CODE,)}
        get("/credentials/google/grant/internal_notify")  # polled while it waits
        assert dashboard.run_jobs() == 1
        return get("/credentials/google/grant/internal_notify")

    # 1. Import: refused twice (a .txt path, a Web client), then imported.
    dashboard.files.files[PATH] = b'{"web": {"client_secret": "%s"}}' % CLIENT_SECRET.encode()
    refused_web = post("/credentials/google/client", form(csrf_token=token, path=PATH))
    refused_txt = post(
        "/credentials/google/client", form(csrf_token=token, path=PATH.replace(".json", ".txt"))
    )
    csrf_refused = post(
        "/credentials/google/client", form(csrf_token="wrong", path=PATH), header_token=False
    )
    dashboard.files.files[PATH] = CLIENT_FILE
    imported = post("/credentials/google/client", form(csrf_token=token, path=f'"{PATH}"'))
    get("/credentials")

    assert "Nothing was imported" in refused_web.text and "Nothing was imported" in refused_txt.text
    assert csrf_refused.status == 403
    assert "Imported. The file was deleted." in imported.text
    assert dashboard.files.deleted == [PATH]

    # 2. Grant internal_notify.
    shown = grant(tokens(ACCESS_1, REFRESH_1, ACCOUNT_1))
    assert f"Granted for {ACCOUNT_1}" in shown.text
    assert dashboard.google.access_token("internal_notify") == ACCESS_1

    # 3. Refresh: a new access token, then Google refuses the stored grant.
    dashboard.clock.advance(timedelta(hours=2))
    dashboard.oauth.refreshes.append(tokens(ACCESS_2, None, ACCOUNT_1))
    assert dashboard.google.access_token("internal_notify") == ACCESS_2
    dashboard.clock.advance(timedelta(hours=2))
    dashboard.oauth.refreshes.append(AdapterError(False, "google_invalid_grant", "refused"))
    with pytest.raises(AdapterError) as needs_reconsent:
        dashboard.google.access_token("internal_notify")
    errors.append(needs_reconsent.value)
    assert "Needs re-consent" in get("/credentials").text

    # 4. A refused re-grant (another domain), then a re-grant on another account.
    refused = grant(tokens("Ae7Xk3Mq9Lz2", REFUSED_REFRESH, REFUSED_ACCOUNT))
    assert "internal_notify must be a nextronai.com account" in refused.text
    regranted = grant(tokens(ACCESS_1 + "x", REFRESH_2, ACCOUNT_2))
    assert f"Granted for {ACCOUNT_2}" in regranted.text
    assert dashboard.oauth.revoked == [REFUSED_REFRESH, REFRESH_1]

    # 5. Remove in two steps.
    asked = post("/credentials/google/remove", form(csrf_token=token, grant="internal_notify"))
    removed = post(
        "/credentials/google/remove",
        form(csrf_token=token, grant="internal_notify", confirm="yes"),
    )
    get("/credentials")
    assert "Remove the Internal notifications" in asked.text
    assert "Removed. Google access was revoked." in removed.text
    assert dashboard.oauth.revoked[-1] == REFRESH_2

    # The flow's own secrets, which only Nextron and Google ever see.
    states = [parse_qs(urlsplit(url).query)["state"][0] for url in dashboard.browser.urls]
    flow_secrets = {
        **PLANTED,
        **{f"state {n}": state for n, state in enumerate(states)},
        **{f"verifier {n}": verifier for n, verifier in enumerate(dashboard.oauth.verifiers)},
        **{f"consent URL {n}": url for n, url in enumerate(dashboard.browser.urls)},
        **{f"consent query {n}": urlsplit(url).query for n, url in enumerate(dashboard.browser.urls)},
    }
    assert len(states) == 3 and len(dashboard.oauth.verifiers) == 3

    # The checks.
    tables, _ = database_leaks(db_engine, CODE)
    assert {"alembic_version", "events", "settings"} <= set(tables)
    assert dashboard.oauth.exchanges == [] and len(ID_TOKENS) == 3  # every exchange ran
    for what, value in ACCOUNTS.items():  # the page may show an account; nothing else may
        assert log_leaks(caplog.records, value) == [], what
        assert database_leaks(db_engine, value)[1] == [], what
        assert file_leaks(tmp_path, value) == [], what
        for error in errors:
            assert value not in f"{error} {error!r}", what
    flow_secrets.update(ID_TOKENS)
    for what, value in flow_secrets.items():
        assert response_leaks(replies, value) == [], what
        assert log_leaks(caplog.records, value) == [], what
        assert database_leaks(db_engine, value)[1] == [], what
        assert file_leaks(tmp_path, value) == [], what
        for error in errors:
            assert value not in f"{error} {error!r}", what

    # Credential Manager was only ever given the client secret and the two stored refresh
    # tokens; never an access token, the code, the state, the verifier, the URL or the path.
    stored = ("client secret", "refresh token 1", "refresh token 2")
    kept = "\n".join(written)
    for what, value in flow_secrets.items():
        assert (value in kept) is (what in stored), what
    left = "\n".join(dashboard.secrets.store.values())
    assert REFRESH_1 not in left and REFRESH_2 not in left  # replaced, then removed
