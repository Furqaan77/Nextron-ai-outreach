"""The unsubscribe Worker (Story 3.10; FR-70, AD-14, AD-15).

Without Node these pin ``wrangler.jsonc``, ``package.json``, the source's banned calls, the
fixture path and ``.gitignore``. With ``node`` on PATH they also run the Worker's handler tests
(``test/handler.test.mjs``: no ``node_modules``, no port) and prove ``check-config`` refuses the
committed placeholders, so ``npm run deploy:<env>`` stops before wrangler starts.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.contracts.support import ENGINE_ROOT

WORKER = ENGINE_ROOT / "workers" / "unsubscribe"
SOURCE = WORKER / "src" / "index.js"
ENVIRONMENTS = ("dev", "test", "prod")
HOST_PREFIX = {"dev": "u-dev.", "test": "u-test.", "prod": "u."}
FIXTURE_REFERENCE = "../../../tests/fixtures/unsub_token_vectors.json"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _strip_jsonc(text: str) -> str:
    """Comments and trailing commas out, never inside a string."""
    out: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\":
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            i = text.index("*/", i + 2) + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r',(\s*[}\]])', r"\1", "".join(out))


def _config() -> dict[str, Any]:
    return json.loads(_strip_jsonc((WORKER / "wrangler.jsonc").read_text(encoding="utf-8")))


def _package() -> dict[str, Any]:
    return json.loads((WORKER / "package.json").read_text(encoding="utf-8"))


def _code() -> str:
    """The source without its comments (the bans are about what runs)."""
    text = SOURCE.read_text(encoding="utf-8")
    return "\n".join(re.sub(r"^\s*//.*$", "", line) for line in text.splitlines())


def test_the_worker_holds_its_files_and_nothing_uncommittable() -> None:
    for relative in (
        "wrangler.jsonc",
        "package.json",
        "package-lock.json",
        "src/index.js",
        "scripts/config-lib.mjs",
        "scripts/check-config.mjs",
        "scripts/secret.mjs",
        "test/handler.test.mjs",
        "test/dev.test.mjs",
    ):
        assert (WORKER / relative).is_file(), relative
    assert not (WORKER / ".gitkeep").exists()
    assert not list(WORKER.glob(".dev.vars*"))


def test_gitignore_keeps_node_modules_wrangler_state_and_dev_vars_out() -> None:
    lines = (ENGINE_ROOT.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("node_modules/", ".wrangler/", ".dev.vars*"):
        assert pattern in lines, pattern


def test_every_switch_that_exposes_or_logs_is_off_everywhere() -> None:
    config = _config()
    assert set(config["env"]) == set(ENVIRONMENTS)
    for where, section in [("top", config), *config["env"].items()]:
        assert section["workers_dev"] is False, where
        assert section["preview_urls"] is False, where
        assert section["logpush"] is False, where
        assert section["observability"]["enabled"] is False, where
        assert section["observability"]["logs"]["enabled"] is False, where
        assert section["observability"]["logs"]["invocation_logs"] is False, where
    assert config["main"] == "src/index.js"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", config["compatibility_date"])


def test_each_env_has_its_own_name_kv_namespace_and_custom_domain() -> None:
    config = _config()
    envs = config["env"]
    names = {envs[e]["name"] for e in ENVIRONMENTS} | {config["name"]}
    ids = set()
    for env in ENVIRONMENTS:
        section = envs[env]
        [namespace] = section["kv_namespaces"]
        assert namespace["binding"] == "UNSUB"
        ids.add(namespace["id"])
        assert "route" not in section
        assert section["routes"], env
        for route in section["routes"]:
            assert route["custom_domain"] is True
            assert route["pattern"].startswith(HOST_PREFIX[env]), route
            assert "workers.dev" not in route["pattern"]
            assert "/" not in route["pattern"] and "*" not in route["pattern"]
    assert len(names) == 4
    assert len(ids) == 3
    assert "kv_namespaces" not in config and "routes" not in config


def test_the_package_pins_wrangler_and_guards_every_deploy() -> None:
    package = _package()
    assert package["private"] is True
    assert package["type"] == "module"
    assert package["engines"] == {"node": ">=24"}
    scripts = package["scripts"]
    expected = {"test": "node --test"}
    for env in ENVIRONMENTS:
        guard = f"node scripts/check-config.mjs {env} && "
        expected[f"deploy:{env}"] = guard + f"wrangler deploy --env {env}"
        expected[f"secret:{env}"] = guard + f"node scripts/secret.mjs {env}"
    assert scripts == expected


def test_the_tests_read_the_engine_fixture_in_place() -> None:
    for name in ("handler.test.mjs", "dev.test.mjs"):
        assert FIXTURE_REFERENCE in (WORKER / "test" / name).read_text(encoding="utf-8")
    assert (WORKER / "test" / FIXTURE_REFERENCE).resolve() == (
        ENGINE_ROOT / "tests" / "fixtures" / "unsub_token_vectors.json"
    )
    copies = [
        path
        for path in WORKER.rglob("unsub_token_vectors*")
        if "node_modules" not in path.parts and ".wrangler" not in path.parts
    ]
    assert copies == []


BANNED = {
    "KV delete": r"\.delete\s*\(|\bdelete\b",
    "console": r"\bconsole\b",
    "request.cf": r"\.cf\b|\bcf\s*[:=]",
    "a body reader": r"\.(text|json|arrayBuffer|formData|blob|bytes)\s*\(|\.body\b|bodyUsed|getReader",
    "header enumeration": r"headers\.(has|entries|keys|values|forEach)\b|of\s+request\.headers",
    "client identity": r"(?i)cf-connecting-ip|x-forwarded-for|x-real-ip|user-agent|true-client-ip",
    "decryption or k_tok": r"(?i)\bdecrypt|\baes|\bk_tok\b",
    "an import": r"^\s*import\b|\brequire\s*\(",
    "storage beyond UNSUB": r"\benv\.(?!UNSUB\b)[A-Z_]+\.",
    "storage by bracket": r"\benv\s*\[[^\]]*\]\s*\.\s*\w+\s*\(",
}


@pytest.mark.parametrize("label", sorted(BANNED))
def test_the_source_never_makes_a_banned_call(label: str) -> None:
    match = re.search(BANNED[label], _code(), flags=re.MULTILINE)
    assert match is None, f"{label}: {match.group(0)!r}"


def test_the_only_header_read_is_authorization() -> None:
    code = _code()
    assert re.findall(r"headers\.get\(([^)]*)\)", code) == ['"authorization"']
    assert code.count("request.headers") == 1
    assert "Headers(" not in code


def test_the_kv_calls_are_get_put_and_list_only() -> None:
    calls = set(re.findall(r"env\.UNSUB\.(\w+)", _code()))
    assert calls == {"get", "put", "list"}


def test_the_ban_patterns_catch_probes() -> None:
    probes = {
        "KV delete": "await env.UNSUB.delete(name);",
        "console": "console.log(token);",
        "request.cf": "const where = request.cf.country;",
        "a body reader": "const form = await request.formData();",
        "header enumeration": "for (const h of request.headers) {}",
        "client identity": 'headers.get("CF-Connecting-IP")',
        "decryption or k_tok": "crypto.subtle.decrypt(alg, k, ct)",
        "an import": 'import x from "y";',
        "storage beyond UNSUB": "await env.LOGS.put(k, v);",
        "storage by bracket": 'await env["LOGS"].put(k, v);',
    }
    assert set(probes) == set(BANNED)
    for label, probe in probes.items():
        assert re.search(BANNED[label], probe, flags=re.MULTILINE), label
    assert re.search(BANNED["decryption or k_tok"], '{name: "AES-GCM-SIV"}')
    assert not re.search(BANNED["decryption or k_tok"], "const daes = hasKey;")
    assert not re.search(BANNED["storage by bracket"], "const secret = env[name];")


def test_the_deploy_guards_always_run_their_main() -> None:
    """check-config.mjs and secret.mjs are thin CLIs over config-lib.mjs, with no main-module
    test that a drive-letter case or a junction could make false (exit 0, guard skipped)."""
    for name in ("check-config.mjs", "secret.mjs"):
        text = (WORKER / "scripts" / name).read_text(encoding="utf-8")
        assert 'from "./config-lib.mjs"' in text, name
        assert "import.meta.url ===" not in text and "argv[1]" not in text, name
        assert "process.exit(1)" in text, name
    handler = (WORKER / "test" / "handler.test.mjs").read_text(encoding="utf-8")
    assert 'from "../scripts/config-lib.mjs"' in handler


@needs_node
@pytest.mark.parametrize("env", [*ENVIRONMENTS, "staging"])
def test_check_config_refuses_the_committed_placeholders(env: str) -> None:
    done = subprocess.run(  # noqa: S603 - node on a fixed script of this repository
        [str(NODE), "scripts/check-config.mjs", env],
        cwd=WORKER,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "refused" in done.stderr
    if env != "staging":
        assert ".invalid" in done.stderr
        assert "placeholder" in done.stderr


@needs_node
def test_the_worker_handler_tests_pass() -> None:
    done = subprocess.run(  # noqa: S603 - node on a fixed test file of this repository
        [str(NODE), "--test", str(Path("test") / "handler.test.mjs")],
        cwd=WORKER,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
    assert re.search(r"^# fail 0$|^ℹ fail 0$", done.stdout, flags=re.MULTILINE), done.stdout[-2000:]
