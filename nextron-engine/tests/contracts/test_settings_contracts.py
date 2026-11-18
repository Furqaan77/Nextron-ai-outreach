"""Settings contracts (AD-12, SC-5, NFR-40), each with a live proof on a probe schema.

- **The schema checks out.** ``schema_problems(SCHEMA)`` is empty: every key is well formed and
  in ``Key``, labels and units aren't empty, defaults are valid and canonical, and every time
  and availability setting names its timezone setting.
- **No off switch for a protected feature.** No setting can disable Suppression, Eligibility,
  the footer, opt-out handling or Market rules. A setting whose key name or label mentions a
  protected feature (a stem: ``suppress``, ``eligib``, ``footer``, ``opt_out``, ``optout``,
  ``unsub``, ``market``) fails when it is a ``boolean`` or its key name or label holds an off
  token as a word (``disable(d)``, ``off``, ``skip``, ``bypass``, ``ignore``, ``override``,
  ``without``, ``no``, ``not``, ``never``, ``allow``, ``enable(d)``, ``toggle``, ``pause(d)``,
  ``exempt``, ``waive``). Key names and labels are compared lowercased, with every run of
  other characters read as ``_`` (so "Opt-out" is ``opt_out``).
- **No secret in settings.** No key name or label contains ``secret``, ``password``,
  ``passphrase``, ``token``, ``api_key``, ``bearer``, ``credential`` or ``private``, or holds
  ``key``, ``pass``, ``pwd`` or ``passwd`` as a word: secrets live only in Windows Credential
  Manager (AD-12), never in the ``settings`` table. The one exception, ``NAMED_NOT_SECRET``, is
  ``unsub_active_kid`` ("Unsubscribe token key id", Story 3.8): the id, 1 to 255, of the keys to
  use, which only picks Credential Manager entries. It passes only with exactly that key, that
  label and the ``integer`` type, which can't hold a key or a token.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

import pytest

from nextron.domain import settings_schema
from nextron.domain.settings_schema import REQUIRED, Section, Setting, SettingType, schema_problems

PROTECTED_STEMS: Final = ("suppress", "eligib", "footer", "opt_out", "optout", "unsub", "market")
OFF_TOKENS: Final = frozenset(
    {
        "disable",
        "disabled",
        "off",
        "skip",
        "bypass",
        "ignore",
        "override",
        "without",
        "no",
        "not",
        "never",
        "allow",
        "enable",
        "enabled",
        "toggle",
        "pause",
        "paused",
        "exempt",
        "waive",
    }
)
SECRET_WORDS: Final = (  # matched anywhere in the key name or label
    "secret",
    "password",
    "passphrase",
    "token",
    "api_key",
    "bearer",
    "credential",
    "private",
)
SECRET_TOKENS: Final = ("key", "pass", "pwd", "passwd")  # matched as whole words
# key -> the exact label of a setting that names a secret's word but is an integer id, never a
# secret (the type is checked too).
NAMED_NOT_SECRET: Final[Mapping[str, str]] = MappingProxyType(
    {"unsub_active_kid": "Unsubscribe token key id"}  # which keys to use (Story 3.8)
)


def words(text: str) -> str:
    """``text`` lowercased, with every run of characters other than ``a-z0-9`` as one ``_``."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def protected_feature_problems(schema: Mapping[str, Setting]) -> list[str]:
    """Each setting that could switch off a protected feature, as ``"<key>: <why>"``."""
    problems = []
    for key, found in schema.items():
        text = f"{words(found.key)}_{words(found.label)}"
        stems = [stem for stem in PROTECTED_STEMS if stem in text]
        if not stems:
            continue
        offs = sorted(OFF_TOKENS & set(text.split("_")))
        if found.type is SettingType.BOOLEAN:
            problems.append(f"{key}: mentions {stems[0]} and is a boolean")
        elif offs:
            problems.append(f"{key}: mentions {stems[0]} and holds {', '.join(offs)}")
    return problems


def secret_problems(schema: Mapping[str, Setting]) -> list[str]:
    """Each setting whose key name or label names a secret, as ``"<key>: names a secret
    (<words>)"``."""
    problems = []
    for key, found in schema.items():
        if NAMED_NOT_SECRET.get(key) == found.label and found.type is SettingType.INTEGER:
            continue  # an integer id that names which secret to use, never a secret
        texts = (words(found.key), words(found.label))
        found_words = [word for word in SECRET_WORDS if any(word in text for text in texts)]
        found_words += [
            token for token in SECRET_TOKENS if any(token in text.split("_") for text in texts)
        ]
        if found_words:
            problems.append(f"{key}: names a secret ({', '.join(found_words)})")
    return problems


def setting(key: str, kind: SettingType = SettingType.STRING, label: str | None = None) -> Setting:
    return Setting(
        key=key,
        type=kind,
        section=Section.ENGINE,
        label=label if label is not None else key.replace("_", " ").capitalize(),
        unit="a unit",
        default=REQUIRED,
    )


def probe(*settings: Setting) -> Mapping[str, Setting]:
    return MappingProxyType({found.key: found for found in settings})


# The schema checks out -------------------------------------------------------------------------


def test_the_declared_schema_has_no_problems() -> None:
    assert schema_problems(settings_schema.SCHEMA) == []


def test_the_schema_check_catches_a_bad_probe() -> None:
    bad = probe(setting("Footer-Address"), setting("footer_postal_address", label=""))

    assert schema_problems(bad) == [
        "Footer-Address: the key must match [a-z][a-z0-9_]{0,30} (at most 31 characters)",
        "footer_postal_address: the label is empty",
        (
            "Key members not declared: ai_budget_alert_pct, ai_budget_monthly, alert_chat_id, "
            "ats_hosts, backup_offpc_dir, backup_rotation_days, backup_time, bulk_threads, "
            "crawl_max_pages, lead_score_fit_weight, lead_score_signal_weight, legal_name, "
            "linkedin_owner_chat, optout_line, places_discovery_locations, "
            "places_max_pages_per_run, postal_address, privacy_notice_url, quota_hunter_credits, "
            "quota_job_aggregator, quota_places_enterprise, quota_places_pro, "
            "quota_prospeo_credits, sender_name, sender_signature, sender_timezone, "
            "sink_allowlist, total_daily_ceiling, unsub_active_kid, unsub_worker_host, "
            "us_solicitation_label"
        ),
        "declared but not in Key: Footer-Address, footer_postal_address",
    ]


# No off switch for a protected feature -----------------------------------------------------------


def test_no_setting_can_switch_off_a_protected_feature() -> None:
    assert protected_feature_problems(settings_schema.SCHEMA) == []


PROTECTED_PROBES = [
    (setting("suppression_off"), "suppression_off: mentions suppress and holds off"),
    (setting("skip_eligibility"), "skip_eligibility: mentions eligib and holds skip"),
    (
        setting("footer_enabled", SettingType.BOOLEAN),
        "footer_enabled: mentions footer and is a boolean",
    ),
    (setting("disable_opt_out"), "disable_opt_out: mentions opt_out and holds disable"),
    (setting("market_rules_bypass"), "market_rules_bypass: mentions market and holds bypass"),
    (setting("unsubscribe_ignore"), "unsubscribe_ignore: mentions unsub and holds ignore"),
    # a boolean is an off switch whatever its name says
    (
        setting("footer_shown", SettingType.BOOLEAN),
        "footer_shown: mentions footer and is a boolean",
    ),
    # the label counts as much as the key
    (setting("mode", label="Skip the footer"), "mode: mentions footer and holds skip"),
    (setting("handling", label="Opt-out: never"), "handling: mentions opt_out and holds never"),
    (setting("optout_paused"), "optout_paused: mentions optout and holds paused"),
    (
        setting("send_without_suppression"),
        "send_without_suppression: mentions suppress and holds without",
    ),
    (setting("allow_unsubscribed"), "allow_unsubscribed: mentions unsub and holds allow"),
    (setting("market_exempt"), "market_exempt: mentions market and holds exempt"),
]


@pytest.mark.parametrize(
    ("found", "problem"), PROTECTED_PROBES, ids=[found.key for found, _ in PROTECTED_PROBES]
)
def test_the_protected_feature_check_catches_an_off_switch(found: Setting, problem: str) -> None:
    schema = probe(setting("footer_postal_address"), found)

    assert protected_feature_problems(schema) == [problem]


@pytest.mark.parametrize(
    "found",
    [
        setting("footer_postal_address"),
        setting("footer_postal_address", label="Footer postal address"),
        setting("market_timezone", SettingType.TIMEZONE),
        setting("eligibility_window_days", SettingType.INTEGER),
        setting("unsubscribe_worker_url"),
        setting("pause_all_sending", SettingType.BOOLEAN),  # no protected feature
        setting("notes", label="Notes for nobody"),  # "no" only as a whole word
    ],
    ids=lambda found: found.key,
)
def test_the_protected_feature_check_passes_what_isnt_an_off_switch(found: Setting) -> None:
    assert protected_feature_problems(probe(found)) == []


# No secret in settings ---------------------------------------------------------------------------


def test_no_setting_names_a_secret() -> None:
    assert secret_problems(settings_schema.SCHEMA) == []


SECRET_PROBES = [
    (setting("telegram_bot_token"), "token"),
    (setting("hunter_api_key"), "api_key, key"),
    (setting("smtp_password"), "password"),
    (setting("backup_passphrase"), "passphrase"),
    (setting("oauth_client_secret"), "secret"),
    (setting("bearer_header"), "bearer"),
    (setting("places_credentials"), "credential"),
    (setting("hunter_key"), "key"),
    (setting("smtp_pass"), "pass"),
    (setting("pwd"), "pwd"),
    (setting("smtp_passwd"), "passwd"),
    (setting("private_key"), "private, key"),
    # the label counts as much as the key name
    (setting("telegram_bot", label="Bot token"), "token"),
    (setting("hunter_access", label="Hunter API key"), "api_key, key"),
    (setting("smtp_login", label="SMTP pass"), "pass"),
    # the one exception holds only for its own key, label and integer type
    (setting("unsub_active_kid", label="Unsubscribe token key id"), "token, key"),
    (
        setting("unsub_active_kid", SettingType.INTEGER, label="Unsubscribe token key"),
        "token, key",
    ),
    (
        setting("unsub_token_key", SettingType.INTEGER, label="Unsubscribe token key id"),
        "token, key",
    ),
]


@pytest.mark.parametrize(
    ("found", "named"), SECRET_PROBES, ids=[found.key for found, _ in SECRET_PROBES]
)
def test_the_secret_check_catches_a_secret_name(found: Setting, named: str) -> None:
    schema = probe(setting("footer_postal_address"), found)

    assert secret_problems(schema) == [f"{found.key}: names a secret ({named})"]


@pytest.mark.parametrize(
    "found",
    [
        setting("footer_postal_address"),
        setting("footer_postal_address", label="Footer postal address"),
        setting("keyword_list", label="Keywords"),  # "key" only as a whole word
        setting("passing_score", SettingType.INTEGER),  # "pass" only as a whole word
        # an integer key id, which only picks Credential Manager entries (Story 3.8)
        setting("unsub_active_kid", SettingType.INTEGER, label="Unsubscribe token key id"),
    ],
    ids=lambda found: found.key,
)
def test_the_secret_check_passes_what_doesnt_name_a_secret(found: Setting) -> None:
    assert secret_problems(probe(found)) == []
