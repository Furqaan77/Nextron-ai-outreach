- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-scaffold-the-engine-project.md`
  summary: Enforce "callers use only nextron.app.<owner>.api", the queries-module import limits, and "web/jobs never import nextron.ports".
  evidence: Review probe: a cross-owner import of `app/setup/service.py` and web→ports imports leave all 23 contracts kept. AD-1's layers/forbidden contract types can't express "api submodule only", so this needs an AD-1/AD-3 amendment (a new contract shape or an AST test) before the first non-api owner module lands.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-scaffold-the-engine-project.md`
  summary: Define and test the clean-stop path (exit 0) for the engine on Windows.
  evidence: The tests stop the engine with terminate(), which is a hard kill on Windows, and no graceful stop protocol is defined. It belongs with the run.ps1 supervision story (1.21).
