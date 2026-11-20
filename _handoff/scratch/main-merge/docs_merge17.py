import os

os.chdir(r"e:\Nextron AI outreach")


def edit(p, pairs):
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        assert s.count(old) == 1, (p, old[:70], s.count(old))
        s = s.replace(old, new)
    open(p, "w", encoding="utf-8", newline="").write(s)


edit("HANDOFF.md", [
    ("and **Story 2.16** (merge `f512511`). **Epics 1 and 2 are complete.**",
     "**Story 2.16** (merge `f512511`), **Story 5.11** (merge `5eaacc6`), **Story 11.5** (merge `917902e`), "
     "**Story 11.7** (merge `b6d0b48`), **Story 11.8** (merge `1af4568`), **Story 9.6** (merge `3cd459b`), "
     "**Story 9.8** (merge `52f3c3f`), **Story 4.13** (merge `f8276b0`), **Story 4.14** (merge `f8beb44`), "
     "**Story 8.4** (merge `510f672`) and **Story 8.1** (merge `f597413`). **Epics 1 and 2 are complete.**"),
    ("Main now: **18613 tests, 29 contracts.** Migration head `0054_2_16_signal_inbox`.\n",
     "**Story 5.11 result:** away dates (Settings) stop new Sequences being picked automatically while Mac is away. The Dashboard shows \"Away until ...\". Leads picked by hand still enroll.\n\n"
     "**Stories 11.5, 11.7 and 11.8 result:** Segments, Sequences with their Step briefs, and Campaigns with their Variants are edited in the dashboard.\n"
     "- Every save is a new version that can't be changed later. Running Enrollments keep the version they started with.\n"
     "- Once rows exist, `nextron seed sync` reports the seed files as ignored (\"managed in the dashboard\").\n\n"
     "**Story 9.6 result:** `/reports/calls` validates the Offer after the first 10 held calls. It shows only counts and codes, never names or erased data.\n\n"
     "**Story 9.8 result:** each Lead has a History tab listing everything that happened to it, with filters.\n\n"
     "**Story 4.13 result:** every Draft revision is checked against 14 deterministic grounding rules before it is stored, and the result is saved.\n\n"
     "**Story 4.14 result:** every revision gets an AI grounding check (Haiku, with Opus as fallback) that fails closed. The Lead page shows a pass/fail/pending badge with the flagged spans.\n"
     "- **Until a grounding route passes the 4.5 test gate, every revision fails with \"checker unavailable\".** This is by design.\n\n"
     "**Story 8.4 result:** active UK companies are discovered through Companies House keyword and SIC searches. An officer becomes a Contact only when their occupation matches a target role.\n\n"
     "**Story 8.1 result:** every job has a budget class. Supply work (discovery, enrichment, re-checks) stops at a provider quota or the AI budget. Sending, replies and unsubscribe polling always keep running.\n\n"
     "Main now: **19526 tests, 29 contracts.** Migration head `0060_8_4_registry_discovery`.\n"),
    ("is 109 stories, of which 67 are done.", "is 109 stories, of which 70 are done."),
    ("- **Story 3.12:**",
     "- **Story 11.8:** there is no Offer editor, and Offers are frozen once any Campaign exists. A new Offer would need a small follow-up story.\n"
     "- **Story 11.5:** once any Segment exists, seed sync never writes Segments again, so the dashboard is the only place they change.\n"
     "- **Story 8.4:**\n"
     "  - the website lookup trusts the first Places match;\n"
     "  - each run searches the first pages only (Story 8.10 rotates queries).\n"
     "- **Story 4.14:** no revision passes grounding until a grounding route passes its AI test set.\n"
     "- **Story 3.12:**"),
])
edit("README.md", [(
    "LinkedIn account setup and channel permissions (10.2, 10.7),",
    "LinkedIn account setup and channel permissions (10.2, 10.7), away dates (5.11), dashboard editing of Segments, Sequences and Campaigns (11.5, 11.7, 11.8), the calls report and Lead history (9.6, 9.8), grounding checks (4.13, 4.14), Companies House discovery (8.4), budget classes (8.1),",
)])
print("ok")
