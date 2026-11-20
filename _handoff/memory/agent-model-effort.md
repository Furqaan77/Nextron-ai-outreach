---
name: agent-model-effort
description: Mac wants every build agent and subagent run as Opus 5.5 at medium effort
metadata:
  node_type: memory
  type: feedback
  originSessionId: 85872522-d5e0-4288-b624-7096a7dc9d2a
  modified: 2026-11-18T06:39:14.602Z
---

On 2026-11-18 Mac said: "from now please make it with opus 5.5 Medium."

**Why:** Mac's preferred balance of build speed against quality.

**How to apply:** start every Agent call (track orchestrators, merge agents, and the implementers, reviewers and patchers they start) with `model: "opus"` and `effort: "medium"`. The scratchpad `track-rules.md` §1 tells orchestrators to do the same. See [[autonomous-build-mode]].
