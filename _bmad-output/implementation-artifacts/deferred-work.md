- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-scaffold-the-engine-project.md`
  summary: Enforce "callers use only nextron.app.<owner>.api", the queries-module import limits, and "web/jobs never import nextron.ports".
  evidence: Review probe: a cross-owner import of `app/setup/service.py` and web→ports imports leave all 23 contracts kept. AD-1's layers/forbidden contract types can't express "api submodule only", so this needs an AD-1/AD-3 amendment (a new contract shape or an AST test) before the first non-api owner module lands.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-scaffold-the-engine-project.md`
  summary: Define and test the clean-stop path (exit 0) for the engine on Windows.
  evidence: The tests stop the engine with terminate(), which is a hard kill on Windows, and no graceful stop protocol is defined. It belongs with the run.ps1 supervision story (1.21).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-redacted-json-logging-and-typed-errors.md`
  summary: Unverified medium risks in logging. (a) An SDK may reset its own logger to DEBUG, for example anthropic with `ANTHROPIC_LOG`; consider a prefix-based handler filter. (b) RotatingFileHandler rollover may fail with PermissionError on Windows when another process holds nextron.log.
  evidence: maybe-false. Settle (a) by reading anthropic 2.8.0's logging setup. Settle (b) by opening nextron.log without FILE_SHARE_DELETE (antivirus or editor) and forcing a rollover.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-redacted-json-logging-and-typed-errors.md`
  summary: Make the adapter error boundary provable at runtime. Add a per-port contract test where a fake transport raises SDK, HTTP and OS errors and asserts AdapterError comes out, and taint-track `msg = str(exc)` locals.
  evidence: The AST raise check can't see adapters with no try/except or exception text copied through a local variable. Add this with the first real adapter (Telegram, Story 1.12).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-redacted-json-logging-and-typed-errors.md`
  summary: Add phone-number redaction for Reply text in alerts (AD-15).
  evidence: `redact()` doesn't mask phone numbers (tested with +44 7700 900123). Logs carry IDs only by convention, but AD-15 Telegram alert text requires phone numbers redacted, so it belongs with Story 7.9.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-one-time-code-dashboard-entry-and-hardened-shell.md`
  summary: Update the repo-root `README.md` (Quick start, line 29) and `HANDOFF.md` (verification commands, line 77), which still show `curl.exe http://127.0.0.1:8766/health` as a working check.
  evidence: Since Story 1.4 every route, `/health` included, needs a dashboard session, so that command returns 401 and the locked page. Both files are outside `nextron-engine/`, and `HANDOFF.md` is agent context, so they belong to the post-story handoff update. Expect 401 there, or use `nextron open` and the browser.
