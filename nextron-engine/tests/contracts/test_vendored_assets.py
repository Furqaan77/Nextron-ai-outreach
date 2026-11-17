"""Vendored static files are byte-for-byte the builds recorded here (AD-13).

HTMX 2.0.11 is ``dist/htmx.min.js`` from the npm tarball ``htmx.org-2.0.11.tgz``, whose
registry integrity is
``sha512-Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA==``.
To upgrade: replace the file, record the new SHA-256 below, and keep the AD-13 configuration.

The Public Suffix List (Story 2.2, MPL-2.0) is ``public_suffix_list.dat`` from
publicsuffix.org, version ``2026-11-16_22-30-55_UTC``, byte for byte with its header. To update
it: replace the file, then record its new SHA-256 and the header's ``VERSION`` below.

The US state table (Story 3.2) is ``geodata/us_state_timezones.csv``, written for this engine:
the 50 states and the District of Columbia, each with the IANA timezone where most of its people
live (its header comment lists the multi-zone states). To change it: edit the file (it must
still parse, with exactly 51 rows), then record its new SHA-256 below; ``.gitattributes`` keeps
git from changing its line endings.
"""

import hashlib

from nextron.domain.geo import US_STATE_COUNT, us_state_zones
from nextron.domain.identity import psl_version
from tests.contracts.support import PACKAGE

HTMX = PACKAGE / "web" / "static" / "htmx-2.0.11.min.js"
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"
PSL = PACKAGE / "domain" / "psl" / "public_suffix_list.dat"
PSL_SHA256 = "76918ba7c5f7501785558708be861152377f962643844828a7a3cb7f3ae864c5"
PSL_VERSION = "2026-11-16_22-30-55_UTC"
US_STATES = PACKAGE / "domain" / "geodata" / "us_state_timezones.csv"
US_STATES_SHA256 = "33512378d16aed7fa1b4b1dfdff1163993ed790853f7787f5dd94ad502eee827"


def test_the_us_state_table_matches_the_recorded_sha256_and_parses() -> None:
    assert hashlib.sha256(US_STATES.read_bytes()).hexdigest() == US_STATES_SHA256
    assert len(us_state_zones()) == US_STATE_COUNT


def test_htmx_matches_the_recorded_sha256() -> None:
    assert hashlib.sha256(HTMX.read_bytes()).hexdigest() == HTMX_SHA256


def test_the_public_suffix_list_matches_the_recorded_sha256() -> None:
    assert hashlib.sha256(PSL.read_bytes()).hexdigest() == PSL_SHA256


def test_psl_version_is_the_recorded_version() -> None:
    assert psl_version() == PSL_VERSION
    assert f"// VERSION: {PSL_VERSION}" in PSL.read_text(encoding="utf-8").splitlines()


def test_the_public_suffix_list_keeps_its_licence_header() -> None:
    head = PSL.read_text(encoding="utf-8").splitlines()[:3]

    assert head[0] == "// This Source Code Form is subject to the terms of the Mozilla Public"
    assert "v. 2.0" in head[1]
