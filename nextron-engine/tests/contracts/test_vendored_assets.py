"""Vendored static files are byte-for-byte the builds recorded here (AD-13).

HTMX 2.0.11 is ``dist/htmx.min.js`` from the npm tarball ``htmx.org-2.0.11.tgz``, whose
registry integrity is
``sha512-Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA==``.
To upgrade: replace the file, record the new SHA-256 below, and keep the AD-13 configuration.
"""

import hashlib

from tests.contracts.support import PACKAGE

HTMX = PACKAGE / "web" / "static" / "htmx-2.0.11.min.js"
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"


def test_htmx_matches_the_recorded_sha256() -> None:
    assert hashlib.sha256(HTMX.read_bytes()).hexdigest() == HTMX_SHA256
