import os

os.chdir(r"e:\Nextron AI outreach")


def edit(p, pairs):
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        assert s.count(old) == 1, (p, old[:70], s.count(old))
        s = s.replace(old, new)
    open(p, "w", encoding="utf-8", newline="").write(s)


edit("HANDOFF.md", [
    ("**Story 8.4** (merge `510f672`) and **Story 8.1** (merge `f597413`). **Epics 1 and 2 are complete.**",
     "**Story 8.4** (merge `510f672`), **Story 8.1** (merge `f597413`), **Story 8.10** (merge `c8e99d8`), "
     "**Story 8.7** (merge `61af1bc`), **Story 6.9** (merge `17c4090`), **Story 6.11** (merge `36d2c37`), "
     "**Story 4.15** (merge `01bd4ab`) and **Story 5.1** (merge `1aef1c3`). **Epics 1 and 2 are complete.**"),
    ("Main now: **19526 tests, 29 contracts.** Migration head `0060_8_4_registry_discovery`.\n",
     "**Story 8.10 result:** each weekday, discovery tops up the pool of ready Leads, within provider quotas. It is built but not triggered until Story 8.9 (daily selection) lands.\n\n"
     "**Story 8.7 result:** Leads are re-checked on a schedule (`recheck_leads`, daily). New Evidence supersedes old Evidence and never edits it, and a Tier change is recorded as an event. Re-checks are supply work, so they stop at the AI budget.\n\n"
     "**Story 6.9 result:** a closed send gate decides, before every send, whether a message may go: stop rules, Send Window, approval lifetime, away dates and inbox state. It fails closed. It is not called yet; Story 6.10 (send exactly once) wires it in.\n\n"
     "**Story 6.11 result:** cold sends are paced inside each inbox's Send Window and catch up after a gap without bursts. They are re-paced at startup.\n\n"
     "**Story 4.15 result:** enrolling a Lead queues a Step 1 Draft written by Opus from the Lead's Evidence and facts. A Draft that fails is recorded as failed.\n"
     "- **Until a drafting route passes the 4.5 test gate, Step 1 Drafts fail with `no_route`.** This is by design; Story 4.20 adds Regenerate.\n\n"
     "**Story 5.1 result:** `/queue` shows due Drafts in queue order, one focused card at a time, with the away banner.\n\n"
     "Main now: **20127 tests, 29 contracts.** Migration head `0064_4_15_draft_failed`.\n"),
    ("is 109 stories, of which 70 are done.", "is 109 stories, of which 74 are done."),
    ("- **Story 3.12:**",
     "- **Story 5.1:** \"next due\" can show a time that has already passed.\n"
     "- **Story 8.10:** discovery top-up does nothing until Story 8.9 adds daily selection.\n"
     "- **Story 3.12:**"),
])
edit("README.md", [(
    "budget classes (8.1),",
    "budget classes (8.1), discovery top-up and scheduled re-checks (8.10, 8.7), the send gate and send pacing (6.9, 6.11), Step 1 drafting (4.15), the approval queue (5.1),",
)])
print("ok")
