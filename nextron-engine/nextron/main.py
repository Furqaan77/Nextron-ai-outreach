"""Composition root (AD-1, AD-2, AD-3, AD-14): wiring, startup checks, the instance lock, the
database, the notification subscriber registry and Uvicorn.

``serve`` runs these steps in order and stops at the first failure. The supervisor
(``scripts/run.ps1``, Story 1.21) relies on the exit codes:

1. Bootstrap config, including ``NEXTRON_ENV``: exit 2, before any file or folder exists.
2. Environment checks (SQLite version, tzdata, OneDrive): exit 2, still before any folder exists.
3. Create the data dir and its ``logs`` folder, then take ``<data dir>\\nextron.lock``: exit 3
   when another instance of the same environment holds it, exit 2 when the folders or the lock
   file can't be created.
4. Logging (``nextron.logconfig``): redacted JSON lines in ``<data dir>\\logs\\nextron.log``.
   It starts only once the lock is held, so a second instance never opens the log file.
5. The port check: exit 2, logged as ``startup_check_failed``. It runs after the lock because a
   second instance of the same environment finds its port taken by the first; it must report
   exit 3, not a busy port.
6. The database, ``<data dir>\\nextron.db`` (``open_database``): created when missing and brought
   to the code's head revision by ``nextron.db.migrations.ensure_current``, which logs
   ``database_migrated`` (``from_revision``, ``to_revision``) when it upgrades. A database whose
   revision is ahead of the code, or a foreign or forked one, is refused untouched: exit 2,
   logged as ``startup_check_failed`` with ``check=database_revision``. A database that can't
   be opened, or a failed migration (rolled back), exits 2, logged as
   ``database_migration_failed`` with the traceback. A second instance or a busy port never
   reaches this step.

   **The pre_migrate backup (Story 1.16).** ``open_database(config, before_upgrade=hook)``
   passes a ``PreMigrateBackup(build_local_backups(config, SystemClock()))``, which
   ``ensure_current`` calls only for a non-empty database behind head, inside the migration's
   write transaction, so nothing changes between the copy and the upgrade. A new file or a
   database at head takes no copy. The hook calls ``LocalBackups.take(PRE_MIGRATE)`` (a copy
   in ``<backup dir>\\local``, verified), keeps it and raises ``PreMigrateBackupFailed`` when
   it failed. Then:

   - the backup fails: exit 2 without migrating, printing ``startup check failed:
     pre_migrate_backup: <error>; the database was not migrated`` and logging
     ``pre_migrate_backup_failed`` (ERROR, ``code``);
   - the migration fails after a copy: exit 2; the message names the copy's full path and
     ``nextron restore "<path>"`` (quoted, so a path with a space pastes whole), and
     ``database_migration_failed`` carries the copy's ``file`` name. The copy stays (it has no
     row, so rotation never deletes it);
   - the upgrade succeeds: ``with units.open(): local.record_pre_migrate(taken)`` records the
     copy's row and rotates (a failed rotation only logs ``backup_rotation_failed``). If
     recording fails, ``backup_record_failed`` (ERROR) is logged and
     startup continues: the copy stays on disk.
7. The prompts and the Sales Playbook (Stories 2.8 and 4.4): ``sync_prompts(units,
   load_prompts, read, err, log_file)`` runs the loader (``serve(load_prompts=...)``,
   ``load_prompts`` unless injected), then reads ``config.playbook_path`` with
   ``serve(read_playbook=...)`` (``read_playbook_file`` unless injected), both outside any unit
   of work, then ``Playbook(SystemClock()).sync_prompts(files, defaults, playbook)`` in one unit
   of work: it stores each new prompt version and Playbook snapshot, records the candidate
   tuples and the Playbook check, and bootstrap-activates the default of each of the three
   extraction routes (``nextron.domain.ai_test_gate.BOOTSTRAP_ROUTES``, Story 4.5) that doesn't
   use the Playbook and has no active tuple (``nextron.app.playbook.api``); every other route
   serves only after its test run passes. ``read_playbook_file(path)`` reads at
   most ``MAX_PLAYBOOK_BYTES`` + 1 bytes and returns ``nextron.domain.playbook.read_playbook``'s
   snapshot, or the ``PlaybookProblem``: any ``OSError`` (a missing file included) is
   ``unreadable``, and an ``InvalidPlaybook`` gives its problem. A problem never stops ``serve``
   (decision 8: extraction doesn't use the Playbook): it prints ``warning: playbook: <problem
   text>`` and the sync records it. ``load_prompts(folder=None)`` reads
   ``manifest.lock`` and every ``*.md`` file at any depth of ``folder`` (the AI package's
   ``prompts`` folder unless given), named by its ``/``-separated path relative to ``folder``, so
   a stray or misnamed one is refused, as strict UTF-8 with universal newlines, checks them with
   ``nextron.domain.prompts.verify`` and returns them with ``routes.default_specs()``. It imports
   ``nextron.adapters.ai.routes`` inside the function, so ``doctor``, ``open`` and ``seed sync``
   never load the AI package or the ``anthropic`` SDK, and importing this module loads neither.
   ``InvalidPrompts`` (a file and the lock disagree, an unlisted or misnamed file, a default
   without its file, or a stored version whose text changed) exits 2, printing ``startup check
   failed: prompts: <message>`` and logging ``startup_check_failed`` with ``check=prompts``.
   Any other failure exits 2, printing ``prompt sync failed (<type>); the traceback is in
   <log file>`` and logging ``prompt_sync_failed`` with the traceback. Either way the server
   never starts. A failure in the prompt sync rolls its transaction back, so nothing was stored
   or activated; a failure in the case sync below comes after the prompt sync committed, so the
   prompt sync's rows stay stored.

   **The AI Test Set cases (Story 4.5, decision 2).** The step also runs the case loader
   (``serve(load_test_cases=...)``, ``load_test_cases`` unless injected) outside any unit of
   work, and ``Playbook.sync_test_cases(cases)`` in a unit of work of its own (a unit of work
   runs one use case) right after the prompt sync's. ``load_test_cases(folder=AI_SETS_DIR)``
   reads the four sets' folders of ``folder`` (``tests/ai_sets`` under the engine root) in this
   order (``CASE_FOLDERS``): every ``extraction/*.json`` file through
   ``nextron.domain.ai_test_cases.extraction_case`` (Epic 2's fixtures), then every
   ``reply/``, ``grounding/`` and ``drafting_smoke/`` ``*.json`` file through
   ``nextron.domain.ai_test_sets.set_case`` (Story 4.6), each folder by file name, each file as
   strict UTF-8 JSON of at most ``MAX_CASE_FILE_BYTES`` (1 MiB). Every folder is required: a
   missing one raises ``<set>: the folder doesn't exist``. A missing folder, an unreadable or
   oversized file, a file that isn't JSON and a case of the wrong shape each raise
   ``InvalidTestCases`` naming ``<set>/<file>``, which exits 2, printing ``startup check
   failed: ai_sets: <message>`` and logging ``startup_check_failed`` with ``check=ai_sets``;
   nothing is stored.
8. Uvicorn on 127.0.0.1 with one worker (``--reload`` only in ``dev``) until it stops: exit 0.
   The dashboard's GET handlers read through the unit-of-work factory step 6 returned.
   ``engine_started`` (with ``db_revision``) is logged before it and ``engine_stopped`` (with
   ``exit_code``) after. The database engine is disposed when ``serve`` returns. Uvicorn runs
   with ``log_config=None``, so its records go through the root handlers. Uvicorn exits with
   code 3 when it can't bind or start the app; ``serve`` logs ``server_start_failed`` and
   reports exit 2, so it never looks like a held lock.

Any other error is logged as ``engine_crashed`` and ends with exit 1.

**Stopping (Story 1.21).** The supervisor stops the engine gracefully with CTRL_BREAK, sent to the
engine's own process group (``run.ps1 -Stop`` or ``-Restart``). Uvicorn 0.54 catches SIGBREAK,
shuts down (the lifespan stops the runner, the scheduler and the heartbeat), restores the handler
it found and then raises the signal again to it. On Windows ``serve`` therefore installs a
SIGBREAK handler that raises ``SystemExit(0)`` just before ``run_server`` (``_stop_on_ctrl_break``)
and puts the previous one back afterwards. The CTRL_BREAK Uvicorn raises again after its graceful
shutdown lands there, and so does one that arrives before Uvicorn takes the signal over (while the
app is built and Uvicorn sets up, before anything has started). Either way ``run_server`` ends with
``SystemExit(0)``, ``engine_stopped`` is logged and the exit code is 0. A CTRL_BREAK that comes
before ``run_server`` (during a migration, say) or after ``serve`` has returned ends the process
at once, as before; a migration then rolls back and runs again at the next start.

``build_subscribers(clock=None)`` is the notification subscriber registry (AD-3): the only code
that constructs ``Subscribers`` or registers a subscriber, frozen once filled. Each story that
adds a subscriber registers its owner command there, in the order AD-3 lists. So far:
``LeadsMerged`` -> ``Pipeline(clock).reparent_lead`` (Story 2.2: the survivor of a merge gets
the more advanced Pipeline Stage; Story 9.1: and the merged Lead's booked calls), then
``Research(clock).reparent_lead`` (Story 2.3: the merged Lead's Contacts move to the survivor;
Story 2.5: so do its crawl runs, and its pages and ATS links the survivor lacks), then
``Sequencing(clock).reparent_lead`` (Story 4.8: the merged Lead's Enrollments move to the
survivor), then (after Story 2.16's Signal Inbox rows) the module-level
``sending_api.reparent_lead`` (Story 5.2: the merged Lead's OutboundMessages name the
survivor); ``SubjectSuppressed`` -> ``Sequencing(clock).stop_suppressed`` (Story 4.8: the live
Enrollments the suppression blocks stop), then ``Pipeline(clock).close_suppressed`` (Story 3.3:
an opt-out closes the Lead ``lost``; AD-3's other subscribers go between them when their stories
add them); ``ContactErased`` -> each owner's ``erase_pii`` hook (Story 3.13, AD-17):
compliance's first (``Erasures(clock, Compliance(clock)).erase_pii``, which reads the Contact
while it still holds its values), then discovery's (``discovery_api.erase_pii``, Story 2.16: it
matches the Signal Inbox items against the Contact's plaintext name and addresses), then
research's (``Research(clock).erase_pii``, which tombstones the Contact; the hooks after it touch only their own tables), then playbook's (``Playbook(clock).erase_pii``, Story 4.6: it
erases the Contact's override cases, ``erase_override_cases``), then
``Sequencing(clock).erase_pii`` (Story 4.8: the erased Contact's live Enrollments stop with
``contact_erased``), then the hooks of ``setup``, ``drafting`` (Story 4.12: it tombstones the
text of the Contact's ``draft_revisions``), ``sending`` (Story 5.2: it tombstones the copy of
the Contact's ``outbound_messages``), ``jobqueue``, ``ops`` and ``notify`` (the others
no-ops: their tables never hold a Contact's data), then pipeline's, last
(``Pipeline(clock).erase_pii``, Stories 9.1 and 10.7: the Contact's Lead's notes, and those of
every Lead merged into it, become ``[erased]``, and it revokes the Contact's Channel Permissions
and NULLs their number and handle; ``linkedin_accounts`` never holds a Contact's data); ``Enrolled`` -> ``Pipeline(clock).on_enrolled`` (Story 4.8: a ``new`` Lead moves to
``queued``); ``FooterChanged`` -> ``Drafting(clock, queue).recompose_ready`` (Story 4.12: the
Market's ``ready`` Drafts get a ``footer_change`` revision; Story 4.14: each new revision's
``check_grounding`` job is enqueued in the same transaction, through a ``JobQueue`` on
``enqueue_only_job_types()``, ``build_job_types`` with every service unwired, so the job type's
lanes and priority are the runner's own; the runner's registry runs the job). Each uses
``SystemClock()`` when no clock is given.

**Erasure (Story 3.13).** ``build_app`` builds one ``Erasures(clock, compliance,
queue=queue)`` (``nextron.app.compliance.erasure``), on the one ``Compliance(clock)`` the
dashboard gets too, and hands it to the dashboard (``create_app(..., erasures=...)``) for the
Lead page's "Erase (data subject request)". The ``ContactErased`` hook's instance
(``build_subscribers``) has no queue: it only runs ``erase_pii``, and its ``erase_contact``
would fail closed.

**Erasure outside the database (Story 3.14).** Each erasure writes a receipt and enqueues one
``erase_external`` job per external store on that queue. ``build_app`` builds one
``ErasureStores(clock, notify, sink)`` (``nextron.app.compliance.erasure_stores``), ``sink``
being ``SinkRecorder(<data dir>\\sink, clock)`` (the ``SinkFilesPort`` of the dev and test sink
files) outside ``prod`` and ``None`` in ``prod``; the registry reaches it through
``erasure_stores=lambda: erasure_stores``. ``build_health_issues`` registers
``queries.compliance.health_issues`` (open erase requests and failed stores) as the
``erasure`` source.

**The erasure ledger (Story 3.16).** ``build_app`` builds one ``LedgerFile(<backup
dir>\\erasure-ledger.jsonl)`` (``nextron.app.compliance.ledger``), one ``LedgerAppender(file,
notify)``, the one ``Compliance(clock, codec=TokenCodec(secrets), ledger=appender)`` (so the
panel, the erasure, the unsubscribe poll and every other writer through it append their lines
after each commit) and one ``ErasureLedger(clock, file, compliance, erasures)``.
``build_recovery_steps(reapply=ledger.reapply)`` registers the ``reapply_erasure_ledger``
startup step; the first startup step, ``sync_erasure_ledger(units, ledger)``, runs
``ledger.sync()`` in a unit of work of its own (inside a fresh ``contextvars.Context()``) after
the recovery pass; ``build_health_issues(..., ledger=<path>)`` registers the ``compliance``
source; and the doctor registry checks the file (``erasure_ledger``).

**The CSV import (Story 2.3).** ``build_app`` builds one ``research = Research(clock)`` and one
``LeadImports(clock, discovery, research, EmailValidatorSyntax(), queue)`` (the queue for Story
2.14's ``verify_contact_email``), on the same ``discovery = Discovery(clock, Pipeline(clock),
queue)`` as the ``canonicalize_lead`` jobs
(``nextron.app.discovery.api``; the syntax adapter is ``nextron.adapters.email_syntax``,
offline), and hands it to the dashboard (``create_app(..., lead_imports=...)``) for the
``/leads/import`` page. A Lead the import creates with a website gets its ``enrich_lead`` job
(Story 2.5) from ``Discovery.upsert_lead``.

**Live Eligibility (Story 3.6).** ``build_app`` hands the dashboard one ``Compliance(clock)``
(``create_app(..., compliance=...)``, the same instance the Suppression List panel uses, Story
3.5); the Lead page calls its ``evaluate_eligibility`` for each Contact on every render.

**Contact Markets (Story 3.2).** ``build_app`` builds one ``ContactMarkets(clock, research,
queue)`` (``nextron.app.research.api``): the ``resolve_contact_markets`` job calls its
``backfill``, and the lifespan's startup step, ``queue_contact_markets(units,
contact_markets)``, runs its ``queue_backfill`` in a unit of work of its own (inside a fresh
``contextvars.Context()``) at each engine start, so the one-off job is queued while any Contact
is unresolved. ``build_job_types(..., contact_markets=lambda: contact_markets)`` breaks the
registry cycle as for ``backups``.

**The AI test gate (Story 4.5).** ``build_app(llm=None)`` builds one ``AiTestRuns(clock, meter,
llm, queue)`` (``nextron.app.playbook.api``) on ``llm``, which is ``ClaudeGateway(secrets,
meter=meter)`` unless injected: imported inside ``build_app``, so importing this module still
loads neither the AI package nor the SDK, and building it builds no HTTP client. The
``run_ai_test_sets`` job calls its ``run``; the lifespan's startup runs ``queue_test_runs(units,
test_runs)`` after ``queue_contact_markets`` (``run_startup_steps``: each step in a unit of work
and context of its own, so either can fail without stopping the other), which queues a run of
each tuple that has none; and the dashboard gets it (``create_app(..., test_runs=...)``) to
queue runs after Check for changes. ``build_job_types(..., test_runs=lambda: test_runs)`` breaks
the registry cycle.

**Jobs (AD-10).** ``build_job_types(*, alerts, backups, restore_tests, recovery,
contact_markets, crawls, discovery, research, extractions, primary, test_runs,
domain_checks=..., unsubscribes=..., place_discovery=..., registry_discovery=...)`` is the
job type registry: the only
code that constructs ``JobTypes`` or calls ``register_job_type``. It registers each story's
``JobType`` (lanes, priority, ``max_runtime``, retry limit, a thin handler from
``nextron.jobs.handlers`` and the health panel's ``JobGroup``, always named), then freezes it.
So far: ``send_alert`` (Story 1.12: the realtime lane only, priority 90, ``max_runtime`` 3
minutes, retry limit 5, ``handlers.send_alert(alerts)``, group ``other``: the panel's groups
have none for alerts), ``backup_db`` (Story 1.16: the bulk lane only, priority 80,
``max_runtime`` 30 minutes, retry limit 2, ``handlers.backup_db(backups)``, group ``backup``) and
``restore_test`` (Story 1.18: the bulk lane only, priority 70, ``max_runtime`` 30 minutes, retry
limit 2, ``handlers.restore_test(restore_tests)``, group ``backup``), ``check_recovery``
(Story 1.19: the realtime lane only, priority 85, ``max_runtime`` 10 minutes, retry limit 2,
``handlers.check_recovery(recovery)``, group ``backup``), ``resolve_contact_markets`` (Story
3.2: the bulk lane only, priority 40, ``max_runtime`` 30 minutes, retry limit 3,
``handlers.resolve_contact_markets(contact_markets)``, group ``other``; a one-off with no
trigger), and Story 2.5's ``enrich_lead`` (the bulk lane by default, or interactive, priority
40, ``max_runtime`` 20 minutes, retry limit 3, ``handlers.enrich_lead(crawls)``, group
``enrichment``), ``canonicalize_lead`` (bulk by default, or interactive, priority 45, 2 minutes,
retry limit 2, ``handlers.canonicalize_lead(discovery)``, ``enrichment``),
Story 2.6's ``render_pages`` (bulk only, priority 40, the ``enrich_lead`` chain's, 5 minutes,
retry limit 2, ``handlers.render_pages(crawls)``, ``enrichment``) and ``purge_page_text`` (bulk
only, priority 20, 10 minutes, retry limit 2, ``handlers.purge_page_text(research)``,
``other``), Story 8.2's ``purge_place_coords`` (registered after ``purge_page_text``, with
its spec, ``handlers.purge_place_coords(discovery)``), Story 2.9's ``extract_lead`` (bulk by
default, or interactive, priority 45, 15 minutes, retry limit 3,
``handlers.extract_lead(extractions)``, ``enrichment``), Story 2.10's
``select_primary_contact`` (bulk by default, or interactive, priority 45, 5 minutes,
retry limit 3, ``handlers.select_primary_contact(primary)``, ``enrichment``), registered after
Story 2.12's ``check_legal_form`` (the same lanes, priority, runtime, retry limit and group,
``handlers.check_legal_form(legal_forms)``), Story 4.5's
``run_ai_test_sets`` (bulk only, priority 30, ``max_runtime`` 2 hours, retry limit 3,
``handlers.run_ai_test_sets(test_runs)``, ``other``), and Story 6.5's ``check_domains``
(the interactive lane only, priority 60, 2 minutes, retry limit 3,
``handlers.check_domains(domain_checks)``, ``sending``); Story 3.14's ``erase_external`` (bulk
only, priority 60, 5 minutes, retry limit 5 (``handlers.ERASE_EXTERNAL_RETRIES``),
``handlers.erase_external(erasure_stores)``, ``other``) is registered after them, and with
``erasure_stores`` unwired its handler raises a ``RuntimeError``, which isn't retried; Story
2.13's ``find_contact_email`` (bulk by default, or interactive, priority 45, 5 minutes, retry
limit 3, ``handlers.find_contact_email(finding)``, ``enrichment``) after it, and with
``finding`` left out its handler raises a ``RuntimeError``; Story 2.14's
``verify_contact_email`` (bulk by default, or interactive, priority 45, 5 minutes, retry limit
3, ``handlers.verify_contact_email(verification)``, ``enrichment``) after it, likewise with
``verification`` left out; Story 2.15's ``score_lead`` (bulk by
default, or interactive, priority 40, 5 minutes, retry limit 3, ``handlers.score_lead(scores)``,
``enrichment``) and ``recompute_tiers`` (bulk only, priority 20, 10 minutes, retry limit 2,
``handlers.recompute_tiers(scores)``, ``enrichment``) after them, and Story 3.11's
``poll_unsubscribes`` (the realtime lane only, priority 80, ``max_runtime`` 5 minutes, retry
limit 3, ``handlers.poll_unsubscribes(unsubscribes)``, ``unsubscribe_poll``; never budget-gated)
is registered after them, and Story 8.3's ``discover_places`` (bulk only, priority 30,
``max_runtime`` 1 hour, retry limit 2, ``handlers.discover_places(place_discovery)``,
``discovery``) after it, and Story 2.16's ``process_signal_item`` (interactive only, priority
60, 5 minutes, retry limit 3 (``handlers.PROCESS_SIGNAL_ITEM_RETRIES``),
``handlers.process_signal_item(signal_inbox)``, ``discovery``) after it, with ``signal_inbox``
left out its handler raising a ``RuntimeError``, and Story 4.14's ``check_grounding``
(interactive by default, or bulk, priority 50, ``max_runtime`` 25 minutes (up to four Claude
requests of 300 s each), retry limit 3, ``handlers.check_grounding(grounding)``, ``other``;
never budget-gated) after it, and Story 8.4's ``discover_registry`` (bulk only, priority 30,
``max_runtime`` 1 hour, retry limit 2, ``handlers.discover_registry(registry_discovery)``,
``discovery``) after it, then Story 8.7's ``recheck_leads`` (the daily tick: bulk only,
priority 20, 10 minutes, retry limit 2, ``handlers.recheck_leads(rechecks)``, ``recheck``) and
``recheck_lead`` (bulk only, priority 30, 20 minutes, retry limit 3
(``handlers.RECHECK_LEAD_RETRIES``), ``handlers.recheck_lead(rechecks)``, ``recheck``; budget
class blockable) after them, and Story 4.15's ``draft_step`` (interactive by default, or bulk,
priority 40, ``max_runtime`` 15 minutes (a Claude request of 300 s and its one doubled retry),
retry limit 3, ``handlers.draft_step(step_drafts)``, ``other``; never budget-gated:
``draft_sequence`` is ``essential``; ``build_app`` passes ``lambda: step_drafts``, the
``drafting`` service's ``StepDrafts(clock, llm, queue)``), then Story 4.17's ``submit_bulk_ai`` and
``poll_ai_batches`` (bulk only, priority 30, ``max_runtime`` 30 minutes and 2 hours (each re-run
item is a direct Claude call), retry limit 3, concurrency key ``ai_batches`` (one at a time),
``handlers.submit_bulk_ai(ai_batches)`` and ``handlers.poll_ai_batches(ai_batches)``,
``other``; ``build_app`` passes one ``BatchJobs`` over the ``research`` service's
``ResearchBatches(clock, meter, llm, extractions, people)`` and (Story 4.18) the ``drafting``
service's ``DraftingBatches(clock, llm, queue, step_drafts)``, in that order, so each job calls
one use case; the latter's poll also re-runs, as ``draft_step:<enrollment>:2`` jobs, the
follow-up items not applied by the ``draft_deadline`` setting) last. ``SignalInbox(clock, meter, llm, discovery, research,
queue)`` (``nextron.app.discovery.signal_inbox``) runs those jobs, reached through
``signal_inbox=lambda: signal_inbox``, and the dashboard gets it (``create_app(...,
signal_inbox=...)``) for the Signal Inbox page. ``Rechecks(clock, meter, crawls, job_boards,
queue, compliance)`` (``nextron.app.research.recheck``) runs the re-check jobs, reached through
``rechecks=lambda: rechecks``, and is kept on ``app.state.rechecks``. ``domain_checks``,
``unsubscribes``, ``place_discovery``, ``grounding``, ``registry_discovery``, ``rechecks``,
``step_drafts`` and ``ai_batches`` are optional services: each defaults to a provider that raises ``RuntimeError``
when called, so a registry built without it (only tests do) fails its job; ``build_app`` always passes the real one. ``build_app`` builds one
``JobQueue(clock, job_types)`` and one ``Runner(units, queue, job_types,
dead_jobs=DeadJobAlerts(notify))`` (``nextron.jobs.runner``).
**Budget classes (Story 8.1).** ``enrich_lead``, ``render_pages``, ``extract_lead``,
``check_legal_form``, ``run_ai_test_sets``, ``check_job_boards``, ``recheck_event``,
``discover_places``, ``discover_registry``, ``process_signal_item``, ``find_contact_email``
and ``verify_contact_email`` are registered ``budget_class=BLOCKABLE``; every
other type is ``essential`` (``tests/contracts/test_budget_classes.py``). ``build_app`` keeps
the registry on ``app.state.job_types``.

**The website crawl (Story 2.5, AD-9, AD-18).** ``build_app`` builds one
``HttpCrawler(meter=meter)`` (``nextron.adapters.crawler``) unless a ``crawler=`` is injected;
building it builds no HTTP client and resolves nothing. It also builds one
``PlaywrightRenderer(meter=meter, fetcher=<that HttpCrawler>)`` (``nextron.adapters.renderer``,
Story 2.6) unless a ``renderer=`` is injected, so a render's fetches share the crawl's pacing
per site; building it launches nothing (each render starts its own browser).
``WebsiteCrawls(clock, meter, crawler, renderer, queue)`` runs the ``enrich_lead`` and
``render_pages`` jobs, ``Discovery(clock, Pipeline(clock), queue)`` the ``canonicalize_lead``
jobs and ``Research(clock)`` the ``purge_page_text`` jobs; the registry reaches them through
``crawls=lambda: crawls``, ``discovery=lambda: discovery`` and ``research=lambda: research``,
which break the same cycle as ``notify``.

**Signals and Evidence (Story 2.9, AD-8, AD-18).** ``build_app(..., llm=None)`` builds one
``ClaudeGateway(secrets, meter=meter)`` (``nextron.adapters.ai``, imported inside the function, so
importing this module still loads neither the AI package nor the SDK) unless an ``llm=`` is
injected; building it builds no HTTP client and makes no call. ``SignalExtractions(clock, meter,
llm, research, people)`` (``nextron.app.research.extraction``) runs the ``extract_lead`` jobs
that the crawl enqueues, reached through ``extractions=lambda: extractions``, and the dashboard
gets ``LeadSignals(clock)`` (``create_app(..., lead_signals=...)``) for the Lead page's Signals
and Evidence.

**Decision-makers and the Primary Contact (Story 2.10).** ``PeopleExtractions(clock, meter, llm,
research, queue)`` (``nextron.app.research.people``) is ``SignalExtractions``' people step (its
last argument), on the same gateway. ``PrimaryContacts(clock, research, queue)`` runs the
``select_primary_contact`` jobs, reached through ``primary=lambda: primary_contacts``, and the
Lead page's "Make primary" (``create_app(..., primary_contacts=...)``).

**Scores and the Lead Tier (Story 2.15).** ``Scoring(clock, Pipeline(clock), research)``
(``nextron.app.scoring.api``) and ``LeadScores(clock, scoring, queue)``
(``nextron.app.scoring.lead_scores``) run the ``score_lead`` jobs (the end of the research
chain, queued by the Primary Contact's selection) and the daily ``recompute_tiers`` job, reached
through ``scores=lambda: lead_scores``, and the Lead page's "Re-score"
(``create_app(..., lead_scores=...)``).

**Today's capacity and the picks (Story 4.10).** ``Selection(clock, Sequencing(clock, queue),
compliance)`` (``nextron.app.scoring.selection``, on the dashboard's one ``Compliance``) gives
the "Ready to draft" page (``GET /leads/ready``, ``POST /leads/ready/pick``) its list, today's
capacity and the picks, and the notification area its "signal shortfall" line
(``create_app(..., selection=...)``); ``LeadScores(..., selection=selection)`` records the day's
capacity after each daily ``recompute_tiers`` run.

**Hiring Signals and job postings (Story 2.11).** ``JobBoards(clock, meter, boards, crawler,
queue)`` (``nextron.app.research.job_boards``) runs the ``check_job_boards`` jobs (after the
people step; each enqueues the legal-form check) and the ``recheck_event`` jobs, reached through
``job_boards=lambda: job_boards``. ``build_app(..., job_board=None)`` reads the boards through
``AtsBoards(meter=meter, fetcher=http_crawler)`` (``nextron.adapters.ats``, on the crawl's own
``HttpCrawler``, so every board request is filtered, paced and metered as ``ats`` there) unless
a ``job_board=`` is injected; building it fetches nothing.

**The legal form (Story 2.12).** ``build_app(..., registry=None)`` builds one
``CompaniesHouseRegistry(secrets, meter=meter)`` (``nextron.adapters.companies_house``) unless a
``registry=`` is injected; building it builds no HTTP client and reads no key.
``LegalFormChecks(clock, meter, registry, discovery, research, queue)``
(``nextron.app.discovery.legal_form``) runs the ``check_legal_form`` jobs that each finished
``check_job_boards`` enqueues, reached through ``legal_forms=lambda: legal_forms``; each enqueues
``select_primary_contact``.

**Finding email addresses (Story 2.13).** ``EmailFinding(clock, meter, research, queue,
domain_search=, hunter=, prospeo=)`` (``nextron.app.research.finding``) runs the
``find_contact_email`` jobs, reached through ``finding=lambda: email_finding``, and the Lead
page's "Find email" (``create_app(..., email_finding=...)``). One ``HunterFinder(secrets,
meter=meter)`` (``nextron.adapters.hunter``) serves Domain Search and Email Finder, and
``ProspeoFinder(secrets, meter=meter)`` (``nextron.adapters.prospeo``) the fallback, unless
injected.

**Stop Sequence (Story 6.9).** The dashboard gets that same ``Sequencing(clock, queue)``
(``create_app(..., sequencing=...)``) for the Lead page's Stop Sequence. The send gate
(``nextron.app.compliance.send_gate.SendGate``) isn't wired yet: Story 6.10's send path builds
and calls it.

**Google Places (Story 8.2).** ``build_places(env, secrets, meter)`` chooses the Places port once,
when the app is built: ``GooglePlaces(secrets, meter=meter)`` (``nextron.adapters.places``) in
``prod``; in ``dev`` and ``test`` only when the ``google_places`` keyring entry exists (its value
read and discarded), else ``OfflinePlaces()``, which makes no request, and also when the keyring
can't be read. ``build_app(..., places=None)`` uses it unless a ``places=`` is injected, builds
``PlaceSearches(clock, meter, places)`` (``nextron.app.discovery.places``) and keeps it on
``app.state.place_searches``. The daily ``purge_place_coords`` job nulls Places coordinates
older than 30 days.

**Places discovery (Story 8.3).** ``PlaceDiscovery(clock, place_searches, site_crawler,
discovery, queue)`` (``nextron.app.discovery.place_ingest``) runs the ``discover_places`` jobs
(reached through ``place_discovery=lambda: place_discovery``) on the crawl's own crawler, and
the Discovery page's "Run Places discovery" (``create_app(..., place_discovery=...)``); it is
also kept on ``app.state.place_discovery``.
**Validating addresses (Story 2.14).** ``EmailVerification(clock, meter, queue, dns=,
email_syntax=, verifier=)`` (``nextron.app.research.verification``) runs the
``verify_contact_email`` jobs, reached through ``verification=lambda: email_verification``. It
looks up MX records through the same ``dns`` as ``DomainChecks``, checks syntax with the offline
``EmailValidatorSyntax`` and verifies through ``verifier`` (the same ``HunterFinder`` unless
injected).

**Companies House discovery (Story 8.4).** ``RegistryDiscovery(clock, meter, registry,
place_searches, site_crawler, discovery, research, queue)``
(``nextron.app.discovery.registry_ingest``) runs the ``discover_registry`` jobs (reached through
``registry_discovery=lambda: registry_discovery``) on the SAME ``registry`` instance the
legal-form check uses, so both share its rate window, and the Discovery page's "Run Companies
House discovery" (``create_app(..., registry_discovery=...)``); it is also kept on
``app.state.registry_discovery``.

**The weekday discovery top-up (Story 8.10).** ``DiscoveryTopUp(clock, queue, place_searches)``
(``nextron.app.discovery.topup``) is built with no ``ReadyPool`` (the scoring and sequencing
services that will provide one, and Story 8.9's ``daily_selection`` that calls its ``chain``,
are not on this branch), so no trigger enqueues scheduled discovery yet. It gives the Discovery
page and the health panel today's figures (``create_app(..., discovery_topup=...)``) and is
kept on ``app.state.discovery_topup``. A ``discover_places`` or ``discover_registry`` job with a
``scheduled_for`` payload runs ``run_scheduled``.

**Notify and alerts (AD-15, INT-8).** ``build_app`` builds one ``Notify(clock, queue,
alert_ports=...)`` (``nextron.app.notify.api``), which owns the ``alerts`` and
``notifications`` tables and sends each Telegram alert as a ``send_alert`` job. Its Telegram
port is ``functools.partial(build_role_adapter, "telegram", AdapterContext(config, secrets,
clock, meter), units=units)``, built on the first send, never when the app is built. The registry
needs the handler, the handler needs ``Notify``, ``Notify`` needs the queue and the queue needs
the registry: ``build_job_types(alerts=lambda: notify)`` breaks the cycle, with ``notify`` bound
just below it. The runner's dead-job sweep (``DeadJobAlerts(notify)``, ``nextron.app.ops.api``)
raises the ``job_dead`` alert for every dead job, and the dashboard (``create_app(...,
notify=notify)``) shows the notifications and dismisses them.

**Metering and the budget (AD-8, AD-9, FR-57).** ``build_app`` builds one ``UsageMeter(units,
clock, after_record=lambda: check_usage(units, budget))`` (``nextron.db.metering``, the
``UsageMeterPort``), the only writer of ``api_usage`` and ``ai_usage``, and hands it to every
HTTP adapter: ``GoogleOAuthClient(meter=meter)`` and, through ``AdapterContext.meter``,
``TelegramAlert(c.secrets, meter=c.meter)``. Each adapter records every request it attempts;
each record commits in its own unit of work. One ``Budget(clock, meter, notify)``
(``nextron.app.ops.api``) reads the month's usage against the quotas and the AI budget: the
Quotas and cost page shows ``budget.status()`` (``create_app(..., budget=budget)``), and after
each record the meter calls ``check_usage(units, budget)``, which runs ``budget.check()`` in a
unit of work of its own inside a fresh ``contextvars.Context()`` and raises each threshold's
dashboard notice once a month. The meter needs the hook, the hook needs ``budget`` and
``budget`` needs the meter: the lambda breaks the cycle, with ``budget`` bound just below it.
Building the app builds no adapter or HTTP client and records nothing.

**Triggers (AD-10).** ``build_triggers(job_types, *, backup_schedule, restore_test_schedule,
recovery_active, domain_check_schedule, unsub_poll_period=POLL_PERIOD_OTHER,
bulk_ai_schedule=None)`` is the trigger
registry: the only code that constructs ``Triggers`` or calls ``register_trigger``. Register
each AD-10 ``Trigger`` there, after its job type (a firing enqueues the job type with the
trigger's own name), then it is frozen. So far: ``backup_db``
(Story 1.16), a ``DailyAt(backup_schedule, fallback=DailyRun(DEFAULT_BACKUP_TIME, "UTC"))``, then
``restore_test`` (Story 1.18), a ``DailyAt(restore_test_schedule,
fallback=DailyRun(DEFAULT_RESTORE_TEST_TIME, "UTC"))``, then ``check_recovery`` (Story 1.19), an
``Every(CHECK_PERIOD, at_startup=True, active=recovery_active)``: every minute, and at start,
but the scheduler enqueues it only while ``recovery_active()`` returns ``True``, so no job row is
made once recovery is over, then ``purge_page_text`` (Story 2.5), ``Every(1 day,
at_startup=True)``: the PC is not on at fixed times, so it also runs at every start, then
``purge_place_coords`` (Story 8.2), ``Every(1 day, at_startup=True)`` for the same reason, then
``check_domains`` (Story 6.5), a ``DailyAt(domain_check_schedule,
fallback=DailyRun(DOMAIN_CHECK_TIME, "UTC"))``, then ``recompute_tiers`` (Story 2.15),
``Every(1 day, at_startup=True)``, like ``purge_page_text``, then ``poll_unsubscribes`` (Story
3.11), an ``Every(unsub_poll_period, at_startup=True)``: ``build_app`` passes
``poll_period(config.env)``, 5 minutes in ``prod`` and 30 elsewhere, then ``recheck_leads``
(Story 8.7), ``Every(RECHECK_LEADS_PERIOD, at_startup=True)`` (24 hours), like
``purge_place_coords``: its local-date job keys make a second tick the same day a no-op while
the day's jobs are live, then ``poll_ai_batches`` (Story 4.17), ``Every(15 minutes,
at_startup=True)``, then ``submit_bulk_ai`` (Story 4.17), a ``DailyAt(bulk_ai_schedule,
fallback=DailyRun(DEFAULT_BULK_AI_SUBMIT_AT, "UTC"))`` (``build_app`` passes
``read_bulk_ai_schedule(units, owners)``: the ``bulk_ai_submit_at`` setting in
``sender_timezone``, caught up at start when a queued item missed the latest time; ``None``
uses the fallback).
``build_app``
builds one ``Scheduler(units, queue, triggers)`` (``nextron.jobs.scheduler``), an
``AsyncIOScheduler(timezone=UTC)`` whose firings only enqueue.

**Backups (Story 1.16; AD-2, AD-20, FR-58, OP-5).** ``build_local_backups(config, clock)`` is
``LocalBackups(clock, env=config.env, local_dir=<backup dir>\\local,
snapshot=functools.partial(snapshot, <data dir>\\nextron.db))``: ``serve`` uses it for the
``pre_migrate`` copy and ``build_app`` for the daily and manual ones. ``build_app`` builds one
``offpc = build_offpc_backups(config, clock, targets)`` and one ``Backups(clock, local,
notify=notify, queue=queue, offpc=offpc)`` (``nextron.app.ops.api``). ``build_offpc_backups``
is ``OffpcBackups(clock, env=config.env, data_dir=..., backup_dir=..., targets=targets)``
(Story 1.17), and ``targets``
is ``build_app``'s ``backup_targets``, by default ``functools.partial(FolderBackupTarget,
secrets=secrets)`` (``nextron.adapters.backup_target``): after each verified daily or manual
backup, the copy is encrypted with the Backup passphrase and written to the
``backup_offpc_dir`` folder. Building it reaches no folder and reads no secret; the
``pre_migrate`` copy stays local. The registry
needs the ``backup_db`` handler, the handler needs ``Backups`` and ``Backups`` needs the queue,
so ``build_job_types(..., backups=lambda: backups)`` breaks the cycle, as it does for
``notify``. The dashboard gets it (``create_app(..., backups=backups)``) for the health panel's
"Back up now". The trigger's ``backup_schedule`` is ``functools.partial(read_backup_schedule,
units, backups)``: when the scheduler starts, ``read_backup_schedule`` runs
``backups.schedule()`` in a read-only unit of work inside a fresh ``contextvars.Context()`` and
returns ``DailyRun(at, timezone, catch_up=missed)``. So ``backup_time`` and
``sender_timezone`` are read once per engine start, and a daily time missed while the engine
was off is made up once at start; a new database (no daily backup yet) never catches up.

**Restore tests (Story 1.18; AD-20, FR-58, OP-7, NFR-16).** ``build_restore_tests(config, clock,
offpc, notify, queue)`` is ``RestoreTests(clock, env=config.env, backup_dir=config.backup_dir,
offpc=offpc, inspect=snapshot.inspect_copy, notify=notify, queue=queue)``
(``nextron.app.ops.api``), on the same ``offpc`` as ``Backups``: the ``restore_test`` job
fetches and decrypts the newest off-PC copy into ``<backup dir>\\restore_test\\`` and checks it
read-only. Building it reaches no folder and reads no secret. ``build_job_types(...,
restore_tests=lambda: restore_tests)`` breaks the registry cycle as for ``backups``, and the
dashboard gets it (``create_app(..., restore_tests=restore_tests)``) for the health panel's
"Run restore test now". The trigger's ``restore_test_schedule`` is
``functools.partial(read_restore_test_schedule, units, restore_tests)``, which works like
``read_backup_schedule``: ``restore_tests.schedule()`` in a read-only unit of work inside a fresh
``contextvars.Context()``, returning ``DailyRun(at, timezone, catch_up=missed)``. The check runs
daily an hour after ``backup_time``; the job tests only when no monthly test has started this
calendar month, and a month's slot missed while the engine was off is made up once at start.

**Recovery mode (Story 1.19; AD-2, AD-5, AD-17, AD-20, OP-8).** ``build_recovery_steps()`` is
the recovery step registry (``nextron.domain.recovery.RecoverySteps``): the only code that
constructs one or calls ``register_step`` (``tests/contracts/test_recovery_contracts.py``). It
ships empty and frozen with no argument; ``build_recovery_steps(reapply=..., repoll=...)``
registers Story 3.16's ``reapply_erasure_ledger`` (owner ``compliance``, a startup step) and Story
3.12's ``repoll_unsubscribes`` (owner ``compliance``, a job step), and Story 6.12 registers its
``RecoveryHandler``s there.
``build_app(..., recovery_steps=None)`` builds one ``Recovery(clock)`` (``nextron.app.ops.api``)
and one ``RecoveryRunner(units, recovery, recovery_steps or build_recovery_steps())``
(``nextron.jobs.recovery``), passes ``recovery=lambda: recovery_runner`` to
``build_job_types`` and ``recovery_active=functools.partial(read_recovery_mode, units)`` to
``build_triggers``, and its ``startup`` to ``engine_lifespan``. ``read_recovery_mode(units)``
reads ``queries.ops.recovery_mode()`` in a read-only unit of work inside a fresh
``contextvars.Context()``. While recovery mode is on, the notification area shows the banner,
the health panel lists the steps, and every later send path holds its sends.

**Restore (Story 1.19).** ``restore(file, *, yes=False, ...)`` (``nextron restore <file>
[--yes]``) puts a backup copy back as this environment's database, and nothing else ever
replaces it. It exits 0 when restored, 1 when refused, cancelled or failed, and 2 for an
invalid bootstrap config or a failed environment check. Its steps, in order:

1. Bootstrap config and ``environment_checks``: exit 2.
2. ``mkdir`` of the data dir and ``logs``, then ``<data dir>\\nextron.lock``. When the engine
   holds it, it prints ``stop the engine before restoring`` and exits 1, with no log record and
   no other change: the running engine owns the log file.
3. ``configure_logging``.
4. **The file** must be an existing regular file whose name ends ``.db`` or ``.db.enc``
   (``copy_format``), named for this environment when it keeps a backup copy's name
   (``copy_env``; a renamed file isn't checked, so keep the names), not the live database
   itself and not inside the restore's work folder. A plain ``.db`` with a
   non-empty ``<file>-wal`` beside it is a live database, not a backup copy, and is refused.
5. **The work folder.** ``clear_work_folders(<data dir>\\restore, "nextron-<env>-", ...)``,
   then a new ``tempfile.mkdtemp(prefix="nextron-<env>-")`` folder in it, always removed at the
   end (``remove_work_folder``; a failure logs ``restore_cleanup_failed``).
6. **The candidate**, ``<work>\\candidate.db``: an encrypted copy is decrypted into it through
   ``targets(file.parent).get(file.name, ...)`` (``FolderBackupTarget`` with the environment's
   secrets unless injected), a plain one is copied with ``copy_candidate`` (SQLite's backup API,
   the file opened ``immutable``). The given file, and every file beside it, is never changed.
7. **The checks**: ``inspect_copy(candidate)`` must show integrity ``ok``, a revision and a
   revision this code knows.
8. ``backup_at`` is ``backup_time(file.name, newest_event_at(candidate), now)``.
9. **Without ``--yes``** it prints what it will do and asks ``Type yes to restore: ``. Only
   ``yes`` (stripped, any case) goes on; anything else, ``EOFError`` or ``KeyboardInterrupt``
   prints ``restore cancelled; nothing was changed`` and exits 1.
10. When ``<data dir>\\nextron.db`` exists, ``save_current`` copies it into ``<backup
    dir>\\pre_restore\\<copy_name(env, now)>`` (logging ``pre_restore_backup_taken``,
    ``file``). If that fails the restore refuses, and tells Mac to move a damaged
    ``nextron.db`` (with its ``-wal`` and ``-shm``) aside himself.
11. ``ensure_current(create_engine(candidate))`` upgrades the candidate, logging
    ``restore_candidate_migrated`` (``from_revision``, ``to_revision``) when it did.
12. ``Recovery(clock).begin(...)`` in a unit of work on the candidate's own
    ``UnitOfWorkFactory`` turns recovery mode on in it; then its engine is disposed. A failure
    in step 11 or 12 discards the ``pre_restore`` copy (best effort), so nothing was changed.
12a. **The erasure ledger (Story 3.16).** When ``<backup dir>\\erasure-ledger.jsonl`` doesn't
    exist and the file is an encrypted ``.db.enc``, the newest
    ``nextron-<env>-<stamp>.ledger.jsonl.enc`` beside it (``targets(file.parent).list()``) is
    decrypted (``get``) into ``<backup dir>\\erasure-ledger.jsonl.part`` (a stale one is
    cleared first) and renamed into place, never over an existing ledger, printing and logging
    ``erasure_ledger_recovered`` (``file``). When none is found, or that fails, it prints ``no
    erasure ledger found: recovery mode holds sending until <path> exists`` and logs
    ``erasure_ledger_not_recovered`` (WARNING, ``code``), and the restore goes on: recovery
    mode then holds sending until the file is put there.
13. ``swap_in(candidate, <data dir>\\nextron.db)``: SQLite's backup API, one write transaction.
    A failure leaves the database unchanged (an empty file ``swap_in`` created on a new PC is
    discarded) and keeps the ``pre_restore`` copy, whose full path the message names.
14. ``restored_check`` must show integrity ``ok``, the head revision and exactly the new row as
    the one ``on`` row; otherwise it prints ``the restored database could not be verified
    (...)``, naming the ``pre_restore`` copy's full path and telling Mac not to start the
    engine (run ``nextron restore`` again, or restore the safety copy), and exits 1.
15. It prints the result and logs ``database_restored`` (INFO: ``file``, ``from_revision``,
    ``to_revision``, ``recovery_id``, ``pre_restore``), then exits 0.

A refusal or a failure in steps 4 to 13 prints one line ending ``nothing was changed`` (``the
database is unchanged`` at step 13) and logs ``restore_refused`` (WARNING, ``reason``) or
``restore_failed`` (ERROR, ``stage``, ``code``). Logs carry file names only, never a path. The
lock is always released.

**The heartbeat (AD-20, NFR-14).** ``build_app`` builds one ``Heartbeat(clock, heartbeat
file)`` (``nextron.app.ops.api``), whose file is ``<data dir>\\heartbeat``
(``HEARTBEAT_FILE_NAME``), and one ``HeartbeatWriter(units, heartbeat, runner, scheduler)``
(``nextron.jobs.heartbeat``). Its ``nextron-job-heartbeat`` thread beats at once and every
30 s, writing the ``runner_heartbeat`` row and then touching the file, but only while every lane
thread and the keeper are alive, no running job has passed its ``max_runtime`` and the
scheduler's event loop answers its probe. The supervisor (``scripts/run.ps1``, Story 1.21)
restarts the engine when the file stays unchanged for 300 s of its own polls (900 s before the
first beat).

**Sending Inboxes (Story 4.7; AD-14, AD-21, FR-32).** ``build_app`` builds one
``Sending(clock, env=config.env, compliance=compliance)`` (``nextron.app.sending.api``; Story
5.2: on the one codec-bearing ``Compliance``, so ``enqueue`` can issue unsubscribe tokens)
and hands it to ``create_app(..., sending=sending)``: the Sending Inboxes panel registers
inboxes through it.
Outside ``prod`` it registers only addresses on the ``sink_allowlist`` setting. It calls no
external service.

**Cold-send pacing (Story 6.11).** That one ``Sending`` is built before the startup steps, so
its ``process_started_at`` is the engine's start. The lifespan's last startup step,
``repace_cold_sends(units, sending)`` (after ``queue_test_runs``), runs
``sending.repace_after_start()`` in a unit of work of its own (inside a fresh
``contextvars.Context()``): each listed inbox's ``next_send_at`` becomes the later of its
``last_sent_at`` and process start, plus a fresh 4 to 12 minute gap, so no burst follows a
restart. If it fails, ``sending_policy.cold_paced`` still refuses every ``next_send_at`` not
later than process start (fail closed).

**Domain checks (Story 6.5; FR-64, NFR-4, NFR-20).** ``build_app(..., dns=None)`` builds one
``DnspythonDns(meter=meter)`` (``nextron.adapters.dns``) unless a ``dns=`` is injected; building
it builds no resolver and reads no DNS configuration (its first query does). ``DomainChecks(clock,
dns, queue, postmaster=..., notify=notify)`` (``nextron.app.sending.checks``) runs the
``check_domains`` jobs, reached through ``domain_checks=lambda: domain_checks``; the scheduler
reads the daily time through ``read_domain_check_schedule(units, domain_checks)``; the health-issue registry gets its
``sending`` source on the same clock; and the dashboard gets it (``create_app(...,
domain_checks=...)``) for the Sending Inboxes page's Recheck. Story 6.2 calls
``DomainChecks.queue_check`` inside its connect transaction.

Story 6.6 (FR-38, FR-58, AD-9) adds the reputation guardrails to each check: ``build_app(...,
postmaster=None)`` builds one ``GooglePostmaster(token=lambda:
google.access_token("postmaster"), meter=meter)`` (``nextron.adapters.postmaster``) unless a
``postmaster=`` is injected; it builds no HTTP client until its first read. The checks get the
same ``Notify`` for their deduplicated alerts, so ``domain_checks`` is built after it. The
health registry gets ``postmaster_grant_missing=functools.partial(postmaster_grant_missing,
google)`` (whether ``GoogleAccess.check_stored_grant("postmaster")`` raises; it never calls
Google).

**The Suppression List panel (Story 3.5; AD-16, FR-50, FR-41).** ``build_app`` builds one
``Compliance(clock, codec=TokenCodec(secrets), ledger=...)`` (``nextron.app.compliance.api``;
``nextron.adapters.crypto.unsub_token``, which reads no key until it decodes) and hands it to
``create_app(..., compliance=compliance)``: the panel's add, import and Reverse run its use
cases. Its ``SubjectSuppressed`` goes to the subscribers ``build_subscribers`` registers, on the
units of work ``build_app`` is given.

**The unsubscribe poll (Story 3.11; FR-70, AD-10, AD-15).** ``build_app(..., unsubscribe=None)``
builds one ``WorkerUnsubscribe(secrets, meter=meter)`` (``nextron.adapters.unsubscribe``) unless
an ``unsubscribe=`` is injected; building it builds no HTTP client and reads no bearer.
``UnsubscribePolls(clock, compliance, unsubscribe, notify, meter)``
(``nextron.app.compliance.unsub_poll``), on that same ``Compliance``, runs the
``poll_unsubscribes`` jobs, reached through ``unsubscribes=lambda: unsubscribes``. Its
``repolled`` is the ``repoll_unsubscribes`` recovery step (Story 3.12): ``build_app`` builds
``build_recovery_steps(reapply=erasure_ledger.reapply, repoll=lambda context:
unsubscribes.repolled(context))`` unless ``recovery_steps`` is injected.

**The lifespan.** ``build_app`` hands ``engine_lifespan(runner, scheduler, writer,
recovery_runner.startup, startup=...)`` to ``create_app``, so all three start when the server
starts and stop when it stops, in whichever process serves the app (the reload worker
included). Building the app starts nothing. Startup first runs the recovery steps' startup pass
(``recovery_startup``) through ``asyncio.to_thread``; if it raises (``recovery_state`` can't be
read or written), the server doesn't start: Uvicorn exits 3, and ``serve`` reports
``server_start_failed`` and exits 2, which fails closed. Next it runs the ``startup`` step
(``run_startup_steps``: ``sync_erasure_ledger``, then ``queue_contact_markets``, then
``queue_test_runs``, then ``repace_cold_sends``) through
``asyncio.to_thread``; an exception there is logged as ``startup_step_failed`` (ERROR, with the
exception's type and, from ``run_startup_steps``, the step's name) and never stops the engine.
The
recovery pass goes first so that a start it refuses writes nothing else, and so the backfill
is queued against the state recovery left. Then it runs ``runner.start`` through
``asyncio.to_thread``, then ``scheduler.start()`` on the event
loop (APScheduler needs the running loop), then ``writer.start`` through ``asyncio.to_thread``.
Shutdown runs in reverse: ``writer.stop`` (to a thread), ``await scheduler.stop()``, then
``runner.stop`` (to a thread), which waits up to 15 s for running handlers and then releases
their jobs back to the queue. Each part that started is stopped even when a later start, or the
server, failed.

With ``--reload`` (``dev`` only), Uvicorn serves from a worker process that does not write
``nextron.log``: only the reloader's own records reach the file. The worker's WARNING and above
go to stderr, redacted. The worker opens the database with ``connect_database``, which never
migrates: the reloader brought it to head before starting Uvicorn.

**Doctor (FR-56).** ``build_doctor_checks()`` is the doctor check registry: the only code that
constructs ``DoctorChecks`` or calls ``register_check`` (``nextron.startup_checks``). It
registers thirteen ``DoctorCheck``s, each binding a pure check function to a ``DoctorContext``
(``config``, ``env_source``, ``probes``, ``secrets``, ``google``, ``database``), then freezes
it; later stories register their checks there. ``doctor(env=..., as_json=...)`` loads the
bootstrap config (``--env`` overrides ``NEXTRON_ENV`` for the run), runs the registry
(``run_checks``) and prints the results as a table, or as JSON, and nothing else. It exits 0
when nothing is ``fail`` (warnings allowed), 1 otherwise, and 2 when the bootstrap config is
invalid. It opens the database read-only (``DoctorDatabase``, on
``nextron.db.engine.create_read_only_engine``): it never creates, migrates or writes
``nextron.db``. It takes no lock, configures no logging and builds no app, runner, scheduler or
heartbeat writer. Its ``GoogleAccess`` refreshes the ``internal_notify`` grant once through
``GoogleOAuthClient(meter=DoctorMeter())``: the meter keeps that call in memory only, so doctor
writes no usage row; with no client or grant stored, or a grant already marked for re-consent,
nothing is sent to Google. Its browser always refuses and its allowlist is empty, because
doctor never starts a consent flow.

``DoctorContext.database`` is a ``ReadinessDatabase`` (``exists``, ``revision``,
``required_unset``, ``offpc_dir``, ``unsub_active_kid`` and ``unsub_worker_host``):
``DoctorDatabase`` for ``doctor`` (``offpc_dir`` and ``unsub_worker_host`` are blank and
``unsub_active_kid`` the schema default with no database file), ``EngineDatabase`` in the
engine. ``DoctorContext.unsubscribe`` (Story 3.12) is the ``UnsubscribePort`` the
``unsub_worker`` check probes the Worker through: ``doctor`` builds
``WorkerUnsubscribe(secrets, meter=DoctorMeter())`` unless one is injected (``unsubscribe=``),
so the probe writes no usage row; the engine passes none and never probes.

**Health (AD-10, AD-20, FR-58, OP-9).** ``build_health_issues(*, budget, clock=None)`` is the
health-issue registry: the only code that constructs ``HealthIssues`` or calls
``register_source``
(``nextron.domain.health``; ``tests/contracts/test_health_contracts.py``). It registers
``IssueSource("jobqueue", queries.jobqueue.health_issues)`` (each dead job), then
``IssueSource("budget", budget.health_issues)`` (AI spend and quotas), then
``IssueSource("backups", queries.ops.health_issues)`` (each failed backup newer than the newest
verified one), then ``IssueSource("playbook", queries.playbook.health_issues)`` (the newest
Sales Playbook check's problem, Story 4.4), then ``IssueSource("sending", lambda:
queries.sending.health_issues(clock.now(), postmaster_grant_missing))`` (each listed Sending
Domain whose cold sends are blocked, Story 6.5, and its blocklist and spam-rate issues, Story
6.6; ``clock=None`` means ``SystemClock()``), then ``IssueSource("discovery",
lambda: queries.discovery.health_issues(budget.status()))`` ("Discovery stopped: ..." for the
AI budget and each reached discovery quota, Story 8.1), then ``IssueSource("erasure",
queries.compliance.health_issues)`` (each open erase request and each failed erasure store,
Story 3.14), then ``IssueSource("unsub_poll", lambda:
queries.compliance.unsub_poll_issues(clock.now(), polling_on=unsub_polling_on()))`` (the
unsubscribe poll failing for over 24 hours, Story 3.12), then ``IssueSource("pipeline",
queries.pipeline.health_issues)`` (LinkedIn Tasks off, Story 10.2), then, given ``ledger``,
``IssueSource("compliance", functools.partial(queries.compliance.ledger_health_issues,
ledger))`` (a missing or unreapplied erasure ledger after a restore, Story 3.16), then freezes
it; each later owner
registers its ``queries.<owner>.health_issues`` there. ``build_app`` builds one
``EngineHealth(clock, job_types, issues=build_health_issues(budget=budget, clock=clock,
ledger=<the ledger's path>, postmaster_grant_missing=...),
readiness=functools.partial(run_checks, build_doctor_checks(), context))``
(``nextron.app.ops.api``) and hands it, with the ``JobQueue`` (which dismisses a dead job), to
``create_app(..., health=health, queue=queue)``: the health panel (``GET /health/panel``)
shows its report (its Recovery section from ``queries.ops.recovery_state()``, Story 1.19) and
the notification area its error count.

**The doctor in the engine.** The panel's readiness list is the doctor registry itself, run on
``DoctorContext(config, ENV_SOURCE_VARIABLE, probes, secrets, google,
EngineDatabase(<data dir>\\nextron.db), in_engine=True)``, where ``probes`` is
``build_app``'s ``probes`` (``Probes.system()`` unless injected). The ids and their order are
doctor's; with ``in_engine`` only three checks change form: ``port_free`` probes nothing and is
``ok`` (the engine serving the page holds the port), ``unsub_worker`` (Story 3.12) never probes
the Worker, and ``internal_notify_grant`` calls
``GoogleAccess.check_stored_grant`` with ``refresh=False``, which reads Credential Manager and
never calls Google, so a GET makes no refresh, no ``api_usage`` row and no re-consent mark
(``nextron doctor`` tests the refresh). ``EngineDatabase`` reads the revision
(``nextron.db.migrations.current_revision_in_unit``), the required settings
(``queries.setup.required_unset``) and ``backup_offpc_dir`` in the caller's unit of work: the
GET's read-only one; ``offpc_backup`` reads the Backup passphrase before that setting, and the
checks that read the database come last, so the Credential Manager and registry reads before
them never hold the read transaction (``build_doctor_checks`` states the order rule). The one
exceptions are the last two checks, ``unsub_token_keys`` (Story 3.8): its keyring entries'
names depend on the ``unsub_active_kid`` setting, so it reads that first and then its two
Credential Manager entries under the read lock; and ``unsub_worker`` (Story 3.12), which reads
the bearer's entry only when the ``unsub_worker_host`` setting is set. Building the app runs no
check.

``build_app`` wires the dashboard (AD-13): one ``KeyringSecrets(<env>)`` (Windows Credential
Manager, namespace ``nextron-engine/<env>``) serves its ``DashboardEntry``, which reads the
one-time code, its ``Credentials``, which store the external credentials, and its
``GoogleAccess``, which holds the Google client and grants; its ``Setup`` stores the settings,
its ``Campaigns`` (``nextron.app.setup.api``) runs the Settings page's Campaign controls
(``create_app(..., campaigns=...)``), its ``CompanyFacts`` (``nextron.app.playbook.api``)
the Approved Company Facts the facts page edits (``create_app(..., facts=...)``), and its
``Playbook`` the AI content view's "Check for changes" (``create_app(..., playbook=...,
ai_content=...)``, Story 4.4), whose loader (``load_ai_content``, bound to
``config.playbook_path``) runs ``load_prompts()`` and ``read_playbook_file`` outside any unit
of work when the button is pressed, never when the app is built; all of them
share one ``SystemClock``; and its handlers open their units of work through ``units``. Tests
inject the secrets and the clock.

**Google OAuth (AD-12, AD-14).** ``GoogleAccess`` gets ``GoogleOAuthClient(meter=meter)``, which
builds its HTTP client only on its first call to Google; ``LoopbackListener``, the class, which
builds one listener per consent flow; ``LocalClientFiles()``; the environment; ``browser``,
which defaults to ``open_in_browser`` (``webbrowser.open``, looked up when it is called); and
``functools.partial(read_sink_allowlist, units)``, read only outside ``prod``. Building the app
therefore constructs no HTTP client, no listener and no browser. Tests inject ``google_oauth``
and ``browser`` (or build ``GoogleAccess`` themselves with fakes).

**Role adapters and the sink (AD-14).** Every adapter with the ``mail`` or ``alert`` role
(``SinkRole``) is registered once in ``ROLE_ADAPTERS`` as a ``RoleAdapter(name, role,
adapter_type, build)``, and built only by ``build_role_adapter(name, context, units=units)``,
the one function that reads ``ROLE_ADAPTERS``. It looks the name up (``LookupError`` unless it
is registered exactly once), calls ``build(AdapterContext(config, secrets, clock, meter))``,
and checks that the result is an ``adapter_type`` that implements its role's port
(``TypeError`` otherwise). In ``prod`` it returns that adapter. Anywhere else
(``nextron.domain.sink.sink_applies``) it returns the adapter wrapped in its role's sink
(``nextron.adapters.sink``: ``MailSink`` or ``AlertSink``), which records to
``<data dir>\\sink`` and reads the allowlist through ``read_sink_allowlist`` on every send. A
live ``mail`` or ``alert`` adapter could be obtained outside ``prod`` only by constructing it
directly, which ``tests/contracts/test_sink_contracts.py`` refuses anywhere in this module but
the ``ROLE_ADAPTERS`` assignment. The entries so far: ``telegram`` (``TelegramAlert``, the
``alert`` role, Story 1.12); Stories 6.x (Gmail) and 7.9 add the others.

``read_sink_allowlist(units)`` returns ``queries.setup.get(Key.SINK_ALLOWLIST)``, read in its
own read-only unit of work inside a fresh ``contextvars.Context()``: a port call happens inside
its caller's unit of work, between that unit's transactions, and a second ``units.open()`` in
the same context would raise. The read-only unit takes ``BEGIN IMMEDIATE`` on its first read,
so it must never run while the caller holds a transaction open.

**Seed sync (FR-1, FR-2, FR-3, FR-4, FR-72, AD-12, Stories 2.1, 3.1, 4.1, 4.2 and 4.3).**
``seed_sync(env=..., seed_dir=...)`` (``nextron seed sync``) loads
``<seed_dir>\\segments.yaml``, ``<seed_dir>\\markets.yaml``, ``<seed_dir>\\sequences.yaml``,
``<seed_dir>\\company_facts.yaml``, ``<seed_dir>\\offers.yaml`` and
``<seed_dir>\\campaigns.yaml`` (``SEED_DIR``, the engine's ``seed`` folder, unless given) into
the ``segments``, ``markets``, ``sequences``, ``step_briefs``, ``company_facts``, ``offers``,
``campaigns`` and ``variants`` tables, stopping at the first failure:

1. Bootstrap config (``--env`` overrides ``NEXTRON_ENV``): exit 2.
2. ``segments.yaml``, then ``markets.yaml``, ``sequences.yaml``, ``company_facts.yaml``,
   ``offers.yaml`` and ``campaigns.yaml``, each read as UTF-8, at most 1 MiB, then
   ``nextron.domain.seed_yaml.parse`` and its validation (``validate_segments``,
   ``nextron.domain.markets.validate_markets``, ``nextron.domain.sequences.validate_sequences``,
   ``nextron.domain.company_facts.validate_company_facts``,
   ``nextron.domain.offers.validate_offers``, then
   ``nextron.domain.campaign.validate_campaigns``). All six files are required. A missing,
   unreadable, larger or not UTF-8 file prints ``<label>: <reason>``, and a
   ``SeedSyntaxError``, ``InvalidSegments``, ``InvalidMarkets``, ``InvalidSequences``,
   ``InvalidCompanyFacts``, ``InvalidOffers`` or ``InvalidCampaigns`` prints ``<label>:
   <message>`` (the label is ``seed/segments.yaml``, ``seed/markets.yaml``,
   ``seed/sequences.yaml``, ``seed/company_facts.yaml``, ``seed/offers.yaml`` or
   ``seed/campaigns.yaml``); either exits 1 before any database access.
3. ``<data dir>\\nextron.db`` must exist and be at ``head_revision()``, read with
   ``current_revision`` on ``create_read_only_engine`` (disposed at once), as doctor does. A
   missing or empty database, one behind or ahead of head, or one that isn't this engine's
   prints why and exits 1, creating and changing nothing: a seed sync never creates or
   migrates the database (only ``serve`` does, after its backup).
4. ``connect_database`` (never ``open_database``) and one unit of work running
   ``Seeds(clock).sync_seed`` (``nextron.app.ops.api``): the Segments, the Markets, the
   Sequences, the Offers, the Campaigns, then the Approved Company Facts, in one transaction.
   Its ``InvalidSegments`` (a
   stored Segment that no longer validates, found while listing the differences) prints
   ``seed/segments.yaml: <message>``; its ``InvalidMarkets`` (a country code
   or ccTLD of the file that a stored Market no longer in the file still uses, or a stored
   Market that no longer validates) prints ``seed/markets.yaml: <message>``; its
   ``InvalidSequences`` (an angle naming a Segment or Market that isn't in this sync's files, a
   regulation cited outside its Market's country, or a stored Sequence that no longer
   validates) prints ``seed/sequences.yaml: <message>``; and its ``InvalidCompanyFacts`` (a
   fact naming a Segment or Market that isn't in this sync's files, a regulation outside its
   Market's country, a date in the future, or a stored fact that no longer validates) prints
   ``seed/company_facts.yaml: <message>``; its ``InvalidOffers`` (a stored Offer that no longer
   validates) prints ``seed/offers.yaml: <message>``; and its ``InvalidCampaigns`` (a reference
   that isn't in this sync's files, a Step beyond its Sequence or an angle note below the floor,
   checked only while the database holds no Campaign, or a stored Campaign that no longer
   validates) prints ``seed/campaigns.yaml: <message>``; each exits 1. Any other exception prints ``seed
   sync failed (<type>); nothing was changed`` and exits 1. Either way the transaction rolled
   back, so no table changed.
5. The summary on ``out`` (``format_seed_sync``) and exit 0: while the database held no
   Segment, ``segments: <n> changed, <m> unchanged`` (n counts created plus changed), then a
   line per created Segment; once it holds any (Story 11.5: the dashboard owns them and nothing
   is written), ``segments: Segments are managed in the dashboard; seed/segments.yaml
   ignored``, then ``  <key>: only in the file``, ``  <key>: differs in <path>, <path>`` (the
   changed fields, the database's version against the file's) and ``  <key>: only in the
   dashboard`` per difference; then the same block for the Markets (``markets: <n> changed, <m>
   unchanged`` and a line per Market code) and for the Sequences (``sequences: <n> changed,
   <m> unchanged`` and a line per Sequence key, or, once the database holds any, Story 11.7,
   ``sequences: Sequences are managed in the dashboard; seed/sequences.yaml ignored`` and a line
   per difference like the Segments'); then the facts' block: ``company_facts: <n>
   changed, <m> unchanged`` (plus ``, <c> conflict`` or ``conflicts`` when there are any), a
   line per created and changed fact, ``  <fact_id>: conflict, left as the Operator set it
   (version <n>); seed/company_facts.yaml differs`` per conflict, and a line per fact not in
   the file; then the Offers' block (``offers: <n> changed, <m> unchanged`` and a line per Offer
   key) and the Campaigns' block (``campaigns: <n> changed, <m> unchanged`` and a line per
   Campaign key), shaped like the Segments'. Once the database holds a Campaign (Story 11.8:
   the dashboard's Campaigns page owns Campaigns, Variants and Offers, and nothing is written)
   both read ``<offers|campaigns>: Campaigns are managed in the dashboard; seed/campaigns.yaml
   and seed/offers.yaml ignored``, then a line per difference like the Segments'. The seed
   never sets a Campaign's status or LIA.

It takes no lock, configures no logging, reads no Credential Manager entry and builds no app,
runner or scheduler; the engine is disposed in a ``finally``. A sync never re-scores a Lead.

``open_dashboard`` (``nextron open``) runs these steps and stops at the first failure:

1. Bootstrap config: exit 2.
2. Is the engine running? A TCP connect to ``127.0.0.1:<port>`` with a 1 s timeout
   (``startup_checks.engine_is_running``). No: ``engine not running`` on stderr and exit 1,
   before any secrets object is built, so no code is written.
3. ``DashboardEntry.issue_code()`` writes a fresh code to the keyring entry
   ``session/open_code``. A Credential Manager failure: exit 1.
4. The default browser opens ``http://127.0.0.1:<port>/enter?t=<code>``. When it can't, the
   code is withdrawn (best effort) and the command exits 1.
5. Exit 0. The code is never printed or logged; the link works once, within 5 minutes.
"""

import asyncio
import contextlib
import contextvars
import functools
import json
import logging
import os
import signal
import sys
import tempfile
import threading
import webbrowser
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import FrameType, MappingProxyType
from typing import Final, Never, Protocol, TextIO

import uvicorn
from fastapi import FastAPI

from nextron import __version__
from nextron.adapters.ats import AtsBoards
from nextron.adapters.backup_target import FolderBackupTarget
from nextron.adapters.clock import SystemClock
from nextron.adapters.companies_house import CompaniesHouseRegistry
from nextron.adapters.crawler import HttpCrawler
from nextron.adapters.crypto.unsub_token import TokenCodec
from nextron.adapters.dns import DnspythonDns
from nextron.adapters.postmaster import GooglePostmaster
from nextron.adapters.email_syntax import EmailValidatorSyntax
from nextron.adapters.google_oauth import GoogleOAuthClient, LocalClientFiles, LoopbackListener
from nextron.adapters.hunter import HunterFinder
from nextron.adapters.places import GooglePlaces, OfflinePlaces
from nextron.adapters.prospeo import ProspeoFinder
from nextron.adapters.renderer import PlaywrightRenderer
from nextron.adapters.secrets import KeyringSecrets
from nextron.adapters.sink import AlertSink, MailSink, SinkRecorder
from nextron.adapters.telegram import TelegramAlert
from nextron.adapters.unsubscribe import WorkerUnsubscribe
from nextron.app.analytics.history import LeadHistory
from nextron.app.approval.api import Approval
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.compliance.erasure_stores import ErasureStores
from nextron.app.compliance.ledger import ErasureLedger, LedgerAppender, LedgerFile
from nextron.app.compliance.unsub_poll import UnsubscribePolls
from nextron.app.discovery import api as discovery_api
from nextron.app.discovery.api import Discovery, LeadImports, SignalInboxRows
from nextron.app.discovery.legal_form import LegalFormChecks
from nextron.app.discovery.place_ingest import PlaceDiscovery
from nextron.app.discovery.registry_ingest import RegistryDiscovery
from nextron.app.discovery.places import PlaceSearches
from nextron.app.discovery.signal_inbox import SignalInbox
from nextron.app.discovery.topup import DiscoveryTopUp
from nextron.app.drafting import api as drafting_api
from nextron.app.drafting.api import Drafting
from nextron.app.drafting.batches import DraftingBatches
from nextron.app.drafting.generation import StepDrafts
from nextron.app.drafting.grounding import GroundingChecks
from nextron.app.jobqueue import api as jobqueue_api
from nextron.app.jobqueue.api import JobQueue, error_code
from nextron.app.notify import api as notify_api
from nextron.app.notify.api import Notify
from nextron.app.ops import api as ops_api
from nextron.app.ops.api import (
    Backups,
    Budget,
    DashboardEntry,
    DeadJobAlerts,
    EngineHealth,
    Heartbeat,
    LocalBackups,
    OffpcBackups,
    Recovery,
    RestoreTests,
    SeedSync,
    Seeds,
    TakenBackup,
    clear_work_folders,
    remove_work_folder,
)
from nextron.app.pipeline.api import Pipeline
from nextron.app.pipeline.assist_accounts import AssistAccounts
from nextron.app.pipeline.channel_permissions import ChannelPermissions
from nextron.app.playbook.api import AiTestRuns, CompanyFacts, CompanyFactSync, Playbook
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import ContactMarkets, Research, WebsiteCrawls
from nextron.app.research.batches import ResearchBatches
from nextron.app.research.extraction import LeadSignals, SignalExtractions
from nextron.app.research.finding import EmailFinding
from nextron.app.research.job_boards import JobBoards
from nextron.app.research.people import PeopleExtractions, PrimaryContacts
from nextron.app.research.recheck import Rechecks
from nextron.app.research.verification import EmailVerification
from nextron.app.scoring.api import Scoring
from nextron.app.scoring.lead_scores import LeadScores
from nextron.app.scoring.selection import Selection
from nextron.app.sending import api as sending_api
from nextron.app.sending.api import Sending
from nextron.app.sending.checks import DomainChecks
from nextron.app.sequencing.api import Sequencing
from nextron.app.setup import api as setup_api
from nextron.app.setup.api import (
    SEGMENT_ONLY_IN_DASHBOARD,
    SEGMENT_ONLY_IN_FILE,
    CampaignDifference,
    Campaigns,
    CampaignSync,
    Credentials,
    GoogleAccess,
    MarketSync,
    OfferDifference,
    OfferSync,
    SegmentDifference,
    SegmentSync,
    SequenceDifference,
    SequenceSync,
    Setup,
)
from nextron.app.setup.segment_editing import SegmentEditor
from nextron.app.setup.sequence_editing import SequenceEditor
from nextron.bootstrap import ENGINE_ROOT, BootstrapConfig, BootstrapError, load_bootstrap
from nextron.db.engine import ConnectionSettingError, create_engine, create_read_only_engine
from nextron.db.metering import UsageMeter
from nextron.db.migrations import (
    DatabaseStateError,
    current_revision,
    current_revision_in_unit,
    ensure_current,
    head_revision,
    known_revisions,
)
from nextron.db.repos.ops.restore import (
    copy_candidate,
    newest_event_at,
    restored_check,
    save_current,
    swap_in,
)
from nextron.db.repos.ops.snapshot import INTEGRITY_PASSED, discard, inspect_copy, snapshot
from nextron.db.uow import Subscribers, UnitOfWorkFactory, use_case
from nextron.domain import seed_yaml, settings_schema
from nextron.domain.alert import SEND_ALERT_JOB, Channel
from nextron.domain.backup import (
    BACKUP_DB_JOB,
    DEFAULT_BACKUP_TIME,
    ERASURE_LEDGER_FILE,
    LOCAL_DIR_NAME,
    BackupKind,
    copy_name,
    offpc_stamp,
)
from nextron.domain.ai import RouteSpec
from nextron.domain.ai_test_cases import InvalidTestCases, TestCase, extraction_case
from nextron.domain.google_oauth import GoogleGrant
from nextron.domain.ai_test_gate import RUN_AI_TEST_SETS_JOB, TestSet
from nextron.domain.ai_test_sets import set_case
from nextron.domain.budget import AiCall, ApiCall, BudgetClass, Usage, month_of
from nextron.domain.campaign import InvalidCampaigns, validate_campaigns
from nextron.domain.company_facts import InvalidCompanyFacts, validate_company_facts
from nextron.domain.contact_market import RESOLVE_CONTACT_MARKETS_JOB
from nextron.domain.crawl import (
    CANONICALIZE_LEAD_JOB,
    ENRICH_LEAD_JOB,
    PURGE_PAGE_TEXT_JOB,
    RENDER_PAGES_JOB,
)
from nextron.domain.credentials import CredentialKey
from nextron.domain.domain_auth import CHECK_DOMAINS_JOB, DOMAIN_CHECK_TIME
from nextron.domain.email_checks import VERIFY_CONTACT_EMAIL_JOB
from nextron.domain.email_finding import FIND_CONTACT_EMAIL_JOB
from nextron.domain.erasure_receipt import ERASE_EXTERNAL_JOB
from nextron.domain.extraction import EXTRACT_LEAD_JOB
from nextron.domain.grounding_check import CHECK_GROUNDING_JOB
from nextron.domain.step_drafting import DRAFT_STEP_JOB
from nextron.domain.ai_batch import CONCURRENCY_KEY as AI_BATCHES_KEY
from nextron.domain.ai_batch import (
    DEFAULT_BULK_AI_SUBMIT_AT,
    POLL_AI_BATCHES_JOB,
    SUBMIT_BULK_AI_JOB,
    BulkAiSchedule,
)
from nextron.domain.health import HealthIssues, IssueSource
from nextron.domain.job import JobGroup, JobType, JobTypes, Lane
from nextron.domain.legal_form import CHECK_LEGAL_FORM_JOB
from nextron.domain.markets import InvalidMarkets, validate_markets
from nextron.domain.notifications import (
    ContactErased,
    Enrolled,
    FooterChanged,
    LeadsMerged,
    MessageSent,
    SubjectSuppressed,
)
from nextron.domain.offers import InvalidOffers, validate_offers
from nextron.domain.people import SELECT_PRIMARY_CONTACT_JOB
from nextron.domain.place_discovery import DISCOVER_PLACES_JOB
from nextron.domain.registry_discovery import DISCOVER_REGISTRY_JOB
from nextron.domain.places import PURGE_PLACE_COORDS_JOB
from nextron.domain.postings import CHECK_JOB_BOARDS_JOB, RECHECK_EVENT_JOB
from nextron.domain.recheck import RECHECK_LEAD_JOB, RECHECK_LEADS_JOB, RECHECK_LEADS_PERIOD
from nextron.domain.scoring import RECOMPUTE_TIERS_JOB, SCORE_LEAD_JOB
from nextron.domain.signal_inbox_item import PROCESS_SIGNAL_ITEM_JOB
from nextron.domain.playbook import (
    MAX_PLAYBOOK_BYTES,
    InvalidPlaybook,
    PlaybookProblem,
    PlaybookSnapshot,
    problem_text,
    read_playbook,
)
from nextron.domain.prompts import (
    MANIFEST_FILE_NAME,
    PROMPT_SUFFIX,
    InvalidPrompts,
    PromptFile,
    decode_prompt,
    parse_manifest,
    verify,
)
from nextron.domain.recovery import (
    CHECK_PERIOD,
    CHECK_RECOVERY_JOB,
    ENCRYPTED,
    PRE_RESTORE_DIR_NAME,
    RESTORE_WORK_DIR_NAME,
    RecoveryContext,
    RecoveryHandler,
    RecoveryStep,
    RecoverySteps,
    backup_time,
    copy_env,
    copy_format,
)
from nextron.domain.restore_test import DEFAULT_RESTORE_TEST_TIME, RESTORE_TEST_JOB
from nextron.domain.seed_yaml import SeedSyntaxError
from nextron.domain.segments import InvalidSegments, validate_segments
from nextron.domain.sequences import InvalidSequences, validate_sequences
from nextron.domain.settings_schema import Key
from nextron.domain.sink import sink_applies
from nextron.domain.unsub_requests import (
    DOCTOR_DEADLINE_S,
    POLL_PERIOD_OTHER,
    POLL_UNSUBSCRIBES_JOB,
    poll_period,
)
from nextron.jobs import handlers
from nextron.jobs.heartbeat import HeartbeatWriter
from nextron.jobs.recovery import RecoveryRunner
from nextron.jobs.runner import Runner
from nextron.jobs.scheduler import Scheduler
from nextron.jobs.triggers import DailyAt, DailyRun, Every, Trigger, Triggers
from nextron.lock import LOCK_FILE_NAME, InstanceLock, LockHeldError
from nextron.logconfig import CRASH_EVENT, LOG_FILE_NAME, configure_logging, mark_logged
from nextron.ports.alert import AlertPort
from nextron.ports.backup_target import BackupTargets
from nextron.ports.clock import Clock
from nextron.ports.crawler import CrawlerPort
from nextron.ports.dns import DnsPort
from nextron.ports.email_finder import DomainSearchPort, EmailFinderPort, EmailVerifierPort
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import GoogleOAuthPort
from nextron.ports.job_board import JobBoardPort
from nextron.ports.llm import LlmPort
from nextron.ports.mail import MailPort
from nextron.ports.places import PlacesPort
from nextron.ports.postmaster import PostmasterPort
from nextron.ports.registry import RegistryPort
from nextron.ports.renderer import RendererPort
from nextron.ports.secrets import SecretsPort, read_credential
from nextron.ports.unsubscribe import UnsubscribePort
from nextron.ports.usage_meter import UsageMeterPort
from nextron.startup_checks import (
    BACKUP_DIR_SEPARATE,
    DATA_DIR_OUTSIDE_ONEDRIVE,
    DATABASE_REVISION,
    ERASURE_LEDGER,
    INTERNAL_NOTIFY_GRANT,
    OFFPC_BACKUP,
    PORT_FREE,
    PROD_ENV_NOT_PERSISTENT,
    PYTHON_VERSION,
    REQUIRED_CREDENTIALS,
    REQUIRED_SETTINGS,
    SQLITE_VERSION,
    TZDATA,
    UNSUB_TOKEN_KEYS,
    UNSUB_WORKER,
    CheckFailed,
    CheckResult,
    DoctorCheck,
    DoctorChecks,
    Probes,
    Status,
    check_backup_dir_separate,
    check_data_dir_outside_onedrive,
    check_database_revision,
    check_erasure_ledger,
    check_internal_notify_grant,
    check_offpc_backup,
    check_port_free,
    check_prod_env_not_persistent,
    check_python_version,
    check_required_credentials,
    check_required_settings,
    check_sqlite_version,
    check_tzdata,
    check_unsub_token_keys,
    check_unsub_worker,
    engine_is_running,
    environment_checks,
    format_json,
    format_table,
    run_checks,
)
from nextron.web.app import Lifespan, create_app
from nextron.web.routing import Units

HOST: Final = "127.0.0.1"
WORKERS: Final = 1
LOGS_DIR_NAME: Final = "logs"
DB_FILE_NAME: Final = "nextron.db"
SINK_DIR_NAME: Final = "sink"
POSTMASTER_GRANT: Final = str(GoogleGrant.POSTMASTER)  # the domain checks' spam-rate grant
HEARTBEAT_FILE_NAME: Final = "heartbeat"
DATABASE_REVISION_CHECK: Final = DATABASE_REVISION
ENV_SOURCE_FLAG: Final = "--env"
ENV_SOURCE_VARIABLE: Final = "NEXTRON_ENV"
PRE_MIGRATE_BACKUP_CHECK: Final = "pre_migrate_backup"
PROMPTS_CHECK: Final = "prompts"
AI_SETS_CHECK: Final = "ai_sets"
FALLBACK_TIMEZONE: Final = "UTC"
PURGE_PAGE_TEXT_PERIOD: Final = timedelta(days=1)
RECOMPUTE_TIERS_PERIOD: Final = timedelta(days=1)
PURGE_PLACE_COORDS_PERIOD: Final = timedelta(days=1)
POLL_AI_BATCHES_PERIOD: Final = timedelta(minutes=15)  # Story 4.17, AD-10
PLACES_PORT_CHOSEN: Final = "places_port_chosen"
PACKAGE_DIR: Final = Path(__file__).resolve().parent

EXIT_OK: Final = 0
EXIT_CRASHED: Final = 1  # serve: an unexpected error (a Python traceback)
EXIT_CHECKS_FAILED: Final = 1  # doctor: at least one check failed
EXIT_STARTUP_FAILED: Final = 2  # invalid config or a failed startup check
EXIT_LOCK_HELD: Final = 3  # another instance runs for this environment
EXIT_OPEN_FAILED: Final = 1  # open: the engine is down, or the code or the browser failed
EXIT_SEED_FAILED: Final = 1  # seed sync: the file, the database or the sync failed

SEED_DIR: Final = ENGINE_ROOT / "seed"
SEGMENTS_FILE_NAME: Final = "segments.yaml"
SEGMENTS_FILE_LABEL: Final = "seed/segments.yaml"  # how messages name the file
MARKETS_FILE_NAME: Final = "markets.yaml"
MARKETS_FILE_LABEL: Final = "seed/markets.yaml"
SEQUENCES_FILE_NAME: Final = "sequences.yaml"
SEQUENCES_FILE_LABEL: Final = "seed/sequences.yaml"
COMPANY_FACTS_FILE_NAME: Final = "company_facts.yaml"
COMPANY_FACTS_FILE_LABEL: Final = "seed/company_facts.yaml"
OFFERS_FILE_NAME: Final = "offers.yaml"
OFFERS_FILE_LABEL: Final = "seed/offers.yaml"
CAMPAIGNS_FILE_NAME: Final = "campaigns.yaml"
CAMPAIGNS_FILE_LABEL: Final = "seed/campaigns.yaml"
MAX_SEED_FILE_BYTES: Final = 1024 * 1024
AI_SETS_DIR: Final = ENGINE_ROOT / "tests" / "ai_sets"  # Story 4.5: the AI Test Set cases
CASE_FOLDERS: Final = (  # Story 4.6: each set's folder, in load order
    TestSet.EXTRACTION,
    TestSet.REPLY,
    TestSet.GROUNDING,
    TestSet.DRAFTING_SMOKE,
)
CASE_FILE_SUFFIX: Final = ".json"
MAX_CASE_FILE_BYTES: Final = 1024 * 1024
SEED_DATABASE_MISSING: Final = (
    "the database doesn't exist yet: start the engine once (nextron serve), "
    "then run seed sync again"
)

ENGINE_NOT_RUNNING: Final = "engine not running"
CODE_NOT_STORED: Final = "could not store the one-time code in Windows Credential Manager"
BROWSER_NOT_OPENED: Final = "could not open the default browser"
DASHBOARD_OPENED: Final = (
    "opened the dashboard in your browser; the link works once, within 5 minutes"
)

EXIT_RESTORE_FAILED: Final = 1  # restore: refused, cancelled or failed
STOP_THE_ENGINE: Final = "stop the engine before restoring"
NOTHING_CHANGED: Final = "nothing was changed"
DATABASE_UNCHANGED: Final = "the database is unchanged"
RESTORE_CANCELLED: Final = "restore cancelled; nothing was changed"
CONFIRM_PROMPT: Final = "Type yes to restore: "
CONFIRM_ANSWER: Final = "yes"
CANDIDATE_NAME: Final = "candidate.db"
WAL_SUFFIX: Final = "-wal"
RESTORE_CLEANUP_FAILED: Final = "restore_cleanup_failed"
RESTORE_TIME_FORMAT: Final = "%Y-%m-%d %H:%M:%S UTC"

ServerRunner = Callable[[BootstrapConfig, bool, UnitOfWorkFactory], None]
PromptLoader = Callable[[], tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]]
PlaybookReader = Callable[[Path], PlaybookSnapshot | PlaybookProblem]
TestCaseLoader = Callable[[], tuple[TestCase, ...]]
EngineProbe = Callable[[str, int], bool]
Browser = Callable[[str], bool]

log = logging.getLogger(__name__)


def build_entry(
    config: BootstrapConfig, *, secrets: SecretsPort | None = None, clock: Clock | None = None
) -> DashboardEntry:
    """The dashboard's one-time code service: keyring and the system clock unless injected."""
    return DashboardEntry(
        secrets if secrets is not None else KeyringSecrets(config.env),
        clock if clock is not None else SystemClock(),
    )


def open_in_browser(url: str) -> bool:
    """Open ``url`` in the default browser; whether it opened. ``webbrowser.open`` is looked up
    now, not when this module was imported."""
    return bool(webbrowser.open(url))


def build_places(env: str, secrets: SecretsPort, meter: UsageMeterPort) -> PlacesPort:
    """The Places port the engine searches through, chosen once at startup: ``GooglePlaces`` in
    ``prod``; in any other environment ``GooglePlaces`` only when the ``google_places`` key is
    stored (its value read and discarded), else ``OfflinePlaces``, which also stands in when
    the keyring can't be read. So paid use never starts on its own outside ``prod``. Logs
    ``places_port_chosen`` (``live``: whether it is ``GooglePlaces``), never the key."""
    if env != "prod":
        try:
            read_credential(secrets, CredentialKey.GOOGLE_PLACES)
        except Exception:  # noqa: BLE001 - no key, or no keyring: stay offline
            log.info(PLACES_PORT_CHOSEN, extra={"live": False})
            return OfflinePlaces()
    log.info(PLACES_PORT_CHOSEN, extra={"live": True})
    return GooglePlaces(secrets, meter=meter)


def build_app(
    config: BootstrapConfig,
    *,
    units: Units,
    secrets: SecretsPort | None = None,
    clock: Clock | None = None,
    google_oauth: GoogleOAuthPort | None = None,
    browser: Browser | None = None,
    probes: Probes | None = None,
    backup_targets: BackupTargets | None = None,
    recovery_steps: RecoverySteps | None = None,
    crawler: CrawlerPort | None = None,
    llm: LlmPort | None = None,
    renderer: RendererPort | None = None,
    dns: DnsPort | None = None,
    job_board: JobBoardPort | None = None,
    registry: RegistryPort | None = None,
    domain_search: DomainSearchPort | None = None,
    hunter: EmailFinderPort | None = None,
    prospeo: EmailFinderPort | None = None,
    unsubscribe: UnsubscribePort | None = None,
    places: PlacesPort | None = None,
    verifier: EmailVerifierPort | None = None,
    postmaster: PostmasterPort | None = None,
) -> FastAPI:
    """The dashboard app; its handlers open their units of work through ``units``. One secrets
    object, keyring unless injected, serves the entry code, the credentials and the Google
    grants; one clock, the system clock unless injected, serves them and the settings. Google
    is reached through ``google_oauth`` (``GoogleOAuthClient(meter=meter)`` unless injected)
    and the consent page opens with ``browser`` (``open_in_browser`` unless injected). The job
    runner, the scheduler and the heartbeat writer are built here and started by the app's
    lifespan (``engine_lifespan``), never by building; ``Notify`` builds its Telegram adapter
    on the first send. The usage meter records each adapter's requests and, after each record,
    runs the budget's threshold check (``check_usage``). The health panel's ``EngineHealth``
    runs the doctor registry in the engine on ``probes`` (``Probes.system()`` unless injected)
    when the panel is read, never when the app is built. ``Backups`` takes the daily and
    manual backups (the ``backup_db`` job) into ``<backup dir>\\local`` and sends each off the
    PC through ``backup_targets`` (``FolderBackupTarget`` with these secrets unless injected);
    ``RestoreTests`` fetches the newest off-PC copy back through the same targets and checks
    it (the ``restore_test`` job); the scheduler reads the daily times when it starts, never
    when the app is built. ``LeadImports`` runs the CSV import on ``/leads/import`` with the
    offline ``EmailValidatorSyntax``. The ``RecoveryRunner`` runs the recovery steps of
    ``recovery_steps`` (``build_recovery_steps()`` unless injected) at startup, before the
    runner, and from the ``check_recovery`` job while recovery mode is on. ``Sending``
    registers the Sending Inboxes panel's inboxes, for ``config.env``. ``ContactMarkets``
    resolves the Contacts stored before Story 3.2 (the ``resolve_contact_markets`` job), which
    the lifespan's startup step (``queue_contact_markets``, after the recovery startup pass)
    queues while any Contact is unresolved. The
    website crawl fetches through ``crawler`` (``HttpCrawler(meter=meter)`` unless injected),
    which builds its HTTP client on its first fetch, and renders through ``renderer``
    (``PlaywrightRenderer(meter=meter)`` on that same ``HttpCrawler`` unless injected), which
    starts a browser only for each render. The Signal extraction calls Claude through ``llm``
    (``ClaudeGateway(secrets, meter=meter)`` unless injected, imported here so that importing
    this module loads no AI package), which builds its HTTP client on its first call.
    ``AiTestRuns`` runs the AI Test Sets (the ``run_ai_test_sets`` job) through that same
    ``llm``; the lifespan's startup queues a run of each tuple that has none
    (``queue_test_runs``, after ``queue_contact_markets``). ``DomainChecks`` checks the
    Sending Domains' DNS authentication (the ``check_domains`` job) through ``dns``
    (``DnspythonDns(meter=meter)`` unless injected), which builds its resolver on its first
    query. The ATS boards are read through ``job_board`` (``AtsBoards(meter=meter,
    fetcher=<that same HttpCrawler>)`` unless injected), and a careers posting's recheck fetches
    through the crawl's ``crawler``. The legal-form check reads the Companies House register
    through ``registry`` (``CompaniesHouseRegistry(secrets, meter=meter)`` unless injected), which
    builds its HTTP client on its first call. The email search (Story 2.13) calls Hunter's Domain
    Search and Email Finder through ``domain_search`` and ``hunter`` (one ``HunterFinder(secrets,
    meter=meter)`` unless injected) and Prospeo through ``prospeo`` (``ProspeoFinder(secrets,
    meter=meter)`` unless injected); each builds its HTTP client on its first call. The unsubscribe
    poll reads the Worker's pending list through ``unsubscribe`` (``WorkerUnsubscribe(secrets,
    meter=meter)`` unless injected), which builds its HTTP client on its first fetch, and applies
    it through a ``Compliance`` with the ``TokenCodec``. Google Places is
    searched through ``places`` (``build_places(config.env, secrets, meter)`` unless injected),
    kept with its ``PlaceSearches`` on ``app.state.place_searches``; ``PlaceDiscovery`` runs the
    Places discovery on them and the crawl's ``crawler``, kept on ``app.state.place_discovery``. The address checks (Story 2.14) look up MX records
    through that same ``dns`` and call Hunter's Email Verifier through ``verifier`` (that same
    ``HunterFinder`` unless injected).
    ``RegistryDiscovery`` runs the Companies House discovery on that same ``registry``, those
    ``PlaceSearches`` and the crawl's ``crawler``, kept on ``app.state.registry_discovery``.
    The job type registry is kept on ``app.state.job_types`` (Story 8.1). ``DiscoveryTopUp``
    (Story 8.10, no ``ReadyPool``) is kept on ``app.state.discovery_topup``. The domain checks read
    Postmaster spam rates through ``postmaster`` (``GooglePostmaster`` on the ``postmaster``
    grant's access token unless injected), which builds its HTTP client on its first read."""
    clock = clock if clock is not None else SystemClock()
    secrets = secrets if secrets is not None else KeyringSecrets(config.env)
    meter = UsageMeter(units, clock, after_record=lambda: check_usage(units, budget))
    google = GoogleAccess(
        secrets,
        clock,
        oauth=google_oauth if google_oauth is not None else GoogleOAuthClient(meter=meter),
        listen=LoopbackListener,
        files=LocalClientFiles(),
        browser=browser if browser is not None else open_in_browser,
        env=config.env,
        allowlist=functools.partial(read_sink_allowlist, units),
    )
    # recovery_runner, notify, backups, restore_tests, contact_markets, crawls, discovery,
    # research, extractions, primary_contacts, test_runs, domain_checks, job_boards, legal_forms,
    # erasure_stores, email_finding, email_verification, lead_scores, unsubscribes,
    # place_discovery, signal_inbox, grounding_checks, registry_discovery, rechecks and step_drafts
    # are bound just below
    job_types = build_job_types(
        alerts=lambda: notify,
        backups=lambda: backups,
        restore_tests=lambda: restore_tests,
        recovery=lambda: recovery_runner,
        contact_markets=lambda: contact_markets,
        crawls=lambda: crawls,
        discovery=lambda: discovery,
        research=lambda: research,
        extractions=lambda: extractions,
        primary=lambda: primary_contacts,
        test_runs=lambda: test_runs,
        domain_checks=lambda: domain_checks,
        job_boards=lambda: job_boards,
        legal_forms=lambda: legal_forms,
        erasure_stores=lambda: erasure_stores,
        finding=lambda: email_finding,
        verification=lambda: email_verification,
        scores=lambda: lead_scores,
        unsubscribes=lambda: unsubscribes,
        place_discovery=lambda: place_discovery,
        signal_inbox=lambda: signal_inbox,
        grounding=lambda: grounding_checks,
        registry_discovery=lambda: registry_discovery,
        rechecks=lambda: rechecks,
        step_drafts=lambda: step_drafts,
        ai_batches=lambda: (batch_jobs,),
    )
    queue = JobQueue(clock, job_types)
    # one HttpCrawler fetches for the crawl, the renderer and the ATS boards, so they share one
    # pacing table
    http_crawler = HttpCrawler(meter=meter)
    site_crawler = crawler if crawler is not None else http_crawler
    crawls = WebsiteCrawls(
        clock,
        meter,
        site_crawler,
        renderer if renderer is not None else PlaywrightRenderer(meter=meter, fetcher=http_crawler),
        queue,
    )
    discovery = Discovery(clock, Pipeline(clock), queue)
    research = Research(clock)
    if llm is None:
        from nextron.adapters.ai import ClaudeGateway  # noqa: PLC0415 - kept out of import time

        llm = ClaudeGateway(secrets, meter=meter)
    people = PeopleExtractions(clock, meter, llm, research, queue)
    grounding_checks = GroundingChecks(clock, llm)  # Story 4.14: the check_grounding jobs
    step_drafts = StepDrafts(clock, llm, queue)  # Story 4.15: the draft_step jobs
    extractions = SignalExtractions(clock, meter, llm, research, people)
    # Story 4.17: the bulk extraction work as Message Batches (submit_bulk_ai, poll_ai_batches)
    # Story 4.18: and the follow-up Steps 2 to 4, with the draft_deadline sweep
    batch_owners = (
        ResearchBatches(clock, meter, llm, extractions, people),
        DraftingBatches(clock, llm, queue, step_drafts),
    )
    batch_jobs = BatchJobs(batch_owners)  # one use case per job over every owner (AD-3)
    primary_contacts = PrimaryContacts(clock, research, queue)
    site_dns = dns if dns is not None else DnspythonDns(meter=meter)
    job_boards = JobBoards(
        clock,
        meter,
        job_board if job_board is not None else AtsBoards(meter=meter, fetcher=http_crawler),
        site_crawler,
        queue,
    )
    # one registry serves the legal-form check and the discovery, so they share its rate window
    companies_house = (
        registry if registry is not None else CompaniesHouseRegistry(secrets, meter=meter)
    )
    legal_forms = LegalFormChecks(clock, meter, companies_house, discovery, research, queue)
    signal_inbox = SignalInbox(clock, meter, llm, discovery, research, queue)  # Story 2.16
    hunter_finder = HunterFinder(secrets, meter=meter)
    email_finding = EmailFinding(
        clock,
        meter,
        research,
        queue,
        domain_search=domain_search if domain_search is not None else hunter_finder,
        hunter=hunter if hunter is not None else hunter_finder,
        prospeo=prospeo if prospeo is not None else ProspeoFinder(secrets, meter=meter),
    )
    place_searches = PlaceSearches(
        clock, meter, places if places is not None else build_places(config.env, secrets, meter)
    )
    place_discovery = PlaceDiscovery(clock, place_searches, site_crawler, discovery, queue)
    email_verification = EmailVerification(
        clock,
        meter,
        queue,
        dns=site_dns,
        email_syntax=EmailValidatorSyntax(),
        verifier=verifier if verifier is not None else hunter_finder,
    )
    registry_discovery = RegistryDiscovery(
        clock, meter, companies_house, place_searches, site_crawler, discovery, research, queue
    )
    discovery_topup = DiscoveryTopUp(clock, queue, place_searches)  # Story 8.10: no ReadyPool
    telegram = functools.partial(
        build_role_adapter, "telegram", AdapterContext(config, secrets, clock, meter), units=units
    )
    notify = Notify(clock, queue, alert_ports={Channel.TELEGRAM: telegram})
    domain_checks = DomainChecks(
        clock,
        site_dns,
        queue,
        postmaster=(
            postmaster
            if postmaster is not None
            else GooglePostmaster(token=lambda: google.access_token(POSTMASTER_GRANT), meter=meter)
        ),
        notify=notify,
    )
    sink_files = (
        SinkRecorder(config.data_dir / SINK_DIR_NAME, clock) if sink_applies(config.env) else None
    )
    erasure_stores = ErasureStores(clock, notify, sink_files)
    targets = (
        backup_targets
        if backup_targets is not None
        else functools.partial(FolderBackupTarget, secrets=secrets)
    )
    offpc = build_offpc_backups(config, clock, targets)
    backups = Backups(
        clock, build_local_backups(config, clock), notify=notify, queue=queue, offpc=offpc
    )
    restore_tests = build_restore_tests(config, clock, offpc, notify, queue)
    contact_markets = ContactMarkets(clock, research, queue)
    # Story 3.16: one ledger file; every suppression, void and erasure is appended after commit
    ledger_file = LedgerFile(config.backup_dir / ERASURE_LEDGER_FILE)
    # the Suppression List panel, Eligibility, erasure and the unsubscribe poll
    compliance = Compliance(
        clock, codec=TokenCodec(secrets), ledger=LedgerAppender(ledger_file, notify)
    )
    erasures = Erasures(clock, compliance, queue=queue)  # Story 3.13: on the same Compliance
    # Story 8.7: the scheduled re-check, on the crawl, the boards and the same Compliance
    rechecks = Rechecks(clock, meter, crawls, job_boards, queue, compliance)
    # Story 4.10: today's capacity and the picks, on the same Compliance; the daily
    # recompute_tiers job also records the day's capacity through it
    # Story 4.15: enroll enqueues Step 1's draft_step job, so the Sequencing has the queue
    sequencing = Sequencing(clock, queue)
    selection = Selection(clock, sequencing, compliance)
    lead_scores = LeadScores(
        clock, Scoring(clock, Pipeline(clock), research), queue, selection=selection
    )
    erasure_ledger = ErasureLedger(clock, ledger_file, compliance, erasures)
    recovery_runner = RecoveryRunner(
        units,
        Recovery(clock),
        (
            recovery_steps
            if recovery_steps is not None
            else build_recovery_steps(
                reapply=erasure_ledger.reapply,
                repoll=lambda context: unsubscribes.repolled(context),
            )
        ),
    )
    unsubscribes = UnsubscribePolls(
        clock,
        compliance,
        unsubscribe if unsubscribe is not None else WorkerUnsubscribe(secrets, meter=meter),
        notify,
        meter,
    )
    test_runs = AiTestRuns(clock, meter, llm, queue)
    sending = Sending(clock, env=config.env, compliance=compliance)  # Story 6.11: built now, so it knows process start
    startup_steps = (
        ("sync_erasure_ledger", functools.partial(sync_erasure_ledger, units, erasure_ledger)),
        ("queue_contact_markets", functools.partial(queue_contact_markets, units, contact_markets)),
        ("queue_test_runs", functools.partial(queue_test_runs, units, test_runs)),
        ("repace_cold_sends", functools.partial(repace_cold_sends, units, sending)),
    )
    budget = Budget(clock, meter, notify)  # the meter's hook reads it when it runs
    runner = Runner(units, queue, job_types, dead_jobs=DeadJobAlerts(notify))
    triggers = build_triggers(
        job_types,
        backup_schedule=functools.partial(read_backup_schedule, units, backups),
        restore_test_schedule=functools.partial(read_restore_test_schedule, units, restore_tests),
        recovery_active=functools.partial(read_recovery_mode, units),
        domain_check_schedule=functools.partial(read_domain_check_schedule, units, domain_checks),
        unsub_poll_period=poll_period(config.env),
        bulk_ai_schedule=functools.partial(read_bulk_ai_schedule, units, batch_owners),
    )
    scheduler = Scheduler(units, queue, triggers)
    heartbeat = Heartbeat(clock, config.data_dir / HEARTBEAT_FILE_NAME)
    writer = HeartbeatWriter(units, heartbeat, runner, scheduler)
    context = DoctorContext(
        config,
        ENV_SOURCE_VARIABLE,
        probes if probes is not None else Probes.system(),
        secrets,
        google,
        EngineDatabase(config.data_dir / DB_FILE_NAME),
        in_engine=True,
    )
    health = EngineHealth(
        clock,
        job_types,
        issues=build_health_issues(
            budget=budget,
            clock=clock,
            ledger=ledger_file.path,
            postmaster_grant_missing=functools.partial(postmaster_grant_missing, google),
        ),
        readiness=functools.partial(run_checks, build_doctor_checks(), context),
    )
    app = create_app(
        env=config.env,
        version=__version__,
        port=config.port,
        entry=build_entry(config, secrets=secrets, clock=clock),
        setup=Setup(clock),
        campaigns=Campaigns(clock),
        assist_accounts=AssistAccounts(clock),  # Story 10.2: the LinkedIn account
        facts=CompanyFacts(clock),
        playbook=Playbook(clock),
        ai_content=functools.partial(load_ai_content, config.playbook_path),
        test_runs=test_runs,
        credentials=Credentials(secrets, clock),
        google=google,
        notify=notify,
        budget=budget,
        health=health,
        queue=queue,
        backups=backups,
        restore_tests=restore_tests,
        lead_imports=LeadImports(clock, discovery, research, EmailValidatorSyntax(), queue),
        sending=sending,
        lead_signals=LeadSignals(clock),
        lead_history=LeadHistory(clock),  # Story 9.8: the Lead page's History tab
        compliance=compliance,
        primary_contacts=primary_contacts,
        erasures=erasures,  # Story 3.13: on the same Compliance, with the queue
        pipeline=Pipeline(clock),  # Story 9.1: the Lead page's Pipeline panel
        # Story 10.7: the Contact's Channel Permissions, on the same Research and Compliance
        channel_permissions=ChannelPermissions(clock, research, compliance),
        domain_checks=domain_checks,
        email_finding=email_finding,
        lead_scores=lead_scores,
        place_discovery=place_discovery,
        selection=selection,
        signal_inbox=signal_inbox,
        segment_editor=SegmentEditor(clock),  # Story 11.5: the Segments page
        sequence_editor=SequenceEditor(clock),  # Story 11.7: the Sequences page
        registry_discovery=registry_discovery,
        discovery=discovery,
        sequencing=sequencing,  # Story 6.9: the Lead page's Stop Sequence
        units=units,
        discovery_topup=discovery_topup,
        now=clock.now,  # Story 5.1: the Approval Queue and each session's start
        lifespan=engine_lifespan(
            runner,
            scheduler,
            writer,
            recovery_runner.startup,
            startup=functools.partial(run_startup_steps, startup_steps),
        ),
    )
    app.state.job_types = job_types  # Story 8.1: tests/contracts/test_budget_classes.py
    app.state.place_searches = place_searches  # Story 8.2
    app.state.place_discovery = place_discovery  # Story 8.3
    app.state.registry_discovery = registry_discovery  # Story 8.4
    app.state.discovery_topup = discovery_topup  # Story 8.10
    app.state.rechecks = rechecks  # Story 8.7
    return app


def sync_erasure_ledger(units: Units, ledger: ErasureLedger) -> None:
    """The engine's first startup step (Story 3.16): ``ledger.sync()`` in a unit of work of its
    own, inside a fresh ``contextvars.Context()``, after the recovery startup pass, so every
    entry, void and erasure the ledger lacks is appended."""

    def run() -> None:
        with units.open():
            ledger.sync()

    contextvars.Context().run(run)


def queue_contact_markets(units: Units, contact_markets: ContactMarkets) -> None:
    """The engine's startup step (Story 3.2): ``contact_markets.queue_backfill()`` in a unit of
    work of its own, inside a fresh ``contextvars.Context()``, so the one-off
    ``resolve_contact_markets`` job is queued while any Contact is unresolved."""

    def run() -> None:
        with units.open():
            contact_markets.queue_backfill()

    contextvars.Context().run(run)


def queue_test_runs(units: Units, test_runs: AiTestRuns) -> None:
    """The engine's startup step (Story 4.5): ``test_runs.queue_runs()`` in a unit of work of
    its own, inside a fresh ``contextvars.Context()``, so a run is queued for each tuple that has
    none on its route's set."""

    def run() -> None:
        with units.open():
            test_runs.queue_runs()

    contextvars.Context().run(run)


def repace_cold_sends(units: Units, sending: Sending) -> None:
    """The engine's startup step (Story 6.11): ``sending.repace_after_start()`` in a unit of
    work of its own, inside a fresh ``contextvars.Context()``, so every listed inbox's next cold
    send waits a fresh gap after the later of its last cold send and process start."""

    def run() -> None:
        with units.open():
            sending.repace_after_start()

    contextvars.Context().run(run)


def run_startup_steps(steps: Sequence[tuple[str, Callable[[], None]]]) -> None:
    """The lifespan's startup step: each ``(name, step)`` in order. A step that raises is
    logged as ``startup_step_failed`` (ERROR, with ``step`` and the exception's type only), and
    the next step still runs."""
    for name, step in steps:
        try:
            step()
        except Exception as error:  # noqa: BLE001 - one step never stops the others
            log.error("startup_step_failed", extra={"step": name, "error": type(error).__name__})


def check_usage(units: Units, budget: Budget) -> None:
    """The usage meter's ``after_record`` hook: ``budget.check()`` in a unit of work of its own,
    inside a fresh ``contextvars.Context()``, so it works whatever unit of work the recording
    adapter's caller has open."""

    def run() -> None:
        with units.open():
            budget.check()

    contextvars.Context().run(run)


def _no_finding() -> handlers.FindingEmail:
    """``build_job_types``' ``finding`` when none is given: a ``find_contact_email`` job fails."""
    raise RuntimeError("no EmailFinding was given to build_job_types")


def _no_verification() -> handlers.VerifyingEmail:
    """``build_job_types``' ``verification`` when none is given: a ``verify_contact_email`` job
    fails."""
    raise RuntimeError("no EmailVerification was given to build_job_types")


def _no_rechecks() -> handlers.Rechecking:
    """``build_job_types``' ``rechecks`` when none is given: a ``recheck_leads`` or
    ``recheck_lead`` job fails."""
    raise RuntimeError("no Rechecks was given to build_job_types")


def _no_signal_inbox() -> handlers.ProcessingSignalItems:
    """``build_job_types``' ``signal_inbox`` when none is given: a ``process_signal_item`` job
    fails."""
    raise RuntimeError("no SignalInbox was given to build_job_types")


def build_job_types(
    *,
    alerts: Callable[[], handlers.AlertSending],
    backups: Callable[[], handlers.BackingUp],
    restore_tests: Callable[[], handlers.RestoreTesting],
    recovery: Callable[[], handlers.RecoveryChecking],
    contact_markets: Callable[[], handlers.MarketBackfilling],
    crawls: Callable[[], handlers.Crawling],
    discovery: Callable[[], handlers.DiscoveryJobs],
    research: Callable[[], handlers.PurgingPageText],
    extractions: Callable[[], handlers.Extracting],
    primary: Callable[[], handlers.SelectingPrimary],
    test_runs: Callable[[], handlers.AiTestRunning],
    job_boards: Callable[[], handlers.CheckingJobBoards],
    legal_forms: Callable[[], handlers.CheckingLegalForm],
    scores: Callable[[], handlers.ScoringLeads],
    domain_checks: Callable[[], handlers.DomainChecking] | None = None,
    erasure_stores: Callable[[], handlers.StoreErasing] | None = None,
    finding: Callable[[], handlers.FindingEmail] | None = None,
    unsubscribes: Callable[[], handlers.PollingUnsubscribes] | None = None,
    place_discovery: Callable[[], handlers.DiscoveringPlaces] | None = None,
    verification: Callable[[], handlers.VerifyingEmail] | None = None,
    signal_inbox: Callable[[], handlers.ProcessingSignalItems] | None = None,
    grounding: Callable[[], handlers.CheckingGrounding] | None = None,
    registry_discovery: Callable[[], handlers.DiscoveringRegistry] | None = None,
    rechecks: Callable[[], handlers.Rechecking] | None = None,
    step_drafts: Callable[[], handlers.DraftingSteps] | None = None,
    ai_batches: Callable[[], Sequence[handlers.BatchingAi]] | None = None,
) -> JobTypes:
    """The job type registry (AD-10), frozen: register each story's job type with
    ``register_job_type(JobType(..., group=JobGroup.X))`` before ``freeze()``; a type the
    budget may stop also passes ``budget_class=BudgetClass.BLOCKABLE`` (Story 8.1: the
    discovery, enrichment and re-check types and ``run_ai_test_sets``; every other type is
    ``essential``, ``tests/contracts/test_budget_classes.py``). ``alerts``
    returns the ``notify`` service that ``send_alert`` jobs call, ``backups`` the ``ops``
    service's ``Backups`` that ``backup_db`` jobs call, ``restore_tests`` its
    ``RestoreTests`` that ``restore_test`` jobs call, ``recovery`` the ``RecoveryRunner`` that
    ``check_recovery`` jobs call, ``contact_markets`` the ``research`` service's
    ``ContactMarkets`` that ``resolve_contact_markets`` jobs call, ``crawls`` its
    ``WebsiteCrawls`` that ``enrich_lead`` and ``render_pages`` jobs call, ``discovery`` the
    ``discovery`` service that ``canonicalize_lead`` jobs call, ``research`` the ``research``
    service that ``purge_page_text`` jobs call, ``extractions`` its ``SignalExtractions`` that
    ``extract_lead`` jobs call, ``primary`` its ``PrimaryContacts`` that
    ``select_primary_contact`` jobs call, ``test_runs`` the ``playbook`` service's
    ``AiTestRuns`` that ``run_ai_test_sets`` jobs call, ``job_boards`` the ``research``
    service's ``JobBoards`` that ``check_job_boards`` and ``recheck_event`` jobs call,
    ``legal_forms`` the ``discovery`` service's ``LegalFormChecks`` that ``check_legal_form``
    jobs call, ``domain_checks`` the ``sending`` service's ``DomainChecks`` that
    ``check_domains`` jobs call (when it is ``None``, a provider that raises ``RuntimeError``
    when a job calls it: ``build_app`` always passes the real one), and ``erasure_stores``
    the ``compliance`` service's ``ErasureStores`` that ``erase_external`` jobs call (``None``:
    unwired, so such a job fails with a ``RuntimeError``, which isn't retried), and ``finding``
    the ``research`` service's ``EmailFinding`` that ``find_contact_email`` jobs call (left
    out, as in tests that never run one, the handler raises ``RuntimeError`` when a job
    runs), ``verification`` the ``research`` service's ``EmailVerification`` that
    ``verify_contact_email`` jobs call (left out, likewise), and ``scores`` the ``scoring``
    service's ``LeadScores`` that ``score_lead`` and ``recompute_tiers`` jobs call, and
    ``unsubscribes`` the ``compliance`` service's ``UnsubscribePolls`` that ``poll_unsubscribes``
    jobs call (``None``: a provider that raises ``RuntimeError``, as for ``domain_checks``), and
    ``place_discovery`` the ``discovery`` service's ``PlaceDiscovery`` that ``discover_places``
    jobs call (``None``: the same), and ``signal_inbox`` the ``discovery`` service's
    ``SignalInbox`` that ``process_signal_item`` jobs call (left out, the handler raises
    ``RuntimeError`` when a job runs), and ``grounding`` the ``drafting`` service's
    ``GroundingChecks`` that ``check_grounding`` jobs call (Story 4.14; ``None``: likewise),
    and ``registry_discovery`` the ``discovery`` service's ``RegistryDiscovery`` that
    ``discover_registry`` jobs call (Story 8.4; ``None``: likewise), and ``rechecks`` the
    ``research`` service's ``Rechecks`` that ``recheck_leads`` and ``recheck_lead`` jobs call
    (Story 8.7; left out, likewise), and ``step_drafts`` the ``drafting`` service's
    ``StepDrafts`` that ``draft_step`` jobs call (Story 4.15; ``None``: likewise), and
    ``ai_batches`` every batch owner that ``submit_bulk_ai`` and ``poll_ai_batches`` jobs call
    (Story 4.17; ``None``: likewise)."""
    checks = domain_checks if domain_checks is not None else no_domain_checks
    found = finding if finding is not None else _no_finding
    verifying = verification if verification is not None else _no_verification
    polls = unsubscribes if unsubscribes is not None else no_unsubscribes
    discovering = place_discovery if place_discovery is not None else no_place_discovery
    inbox = signal_inbox if signal_inbox is not None else _no_signal_inbox
    checks_grounding = grounding if grounding is not None else no_grounding
    registry_runs = (
        registry_discovery if registry_discovery is not None else no_registry_discovery
    )
    rechecking = rechecks if rechecks is not None else _no_rechecks
    drafts_steps = step_drafts if step_drafts is not None else no_step_drafts
    batch_owners = ai_batches if ai_batches is not None else no_ai_batches
    job_types = JobTypes()
    job_types.register_job_type(
        JobType(
            name=SEND_ALERT_JOB,
            default_lane=Lane.REALTIME,
            lanes=frozenset({Lane.REALTIME}),
            priority=90,
            max_runtime=timedelta(minutes=3),  # 10 s connect, 15 s per phase, database waits
            retry_limit=5,  # 30 s doubling: about 15 minutes of backoff
            handler=handlers.send_alert(alerts),
            group=JobGroup.OTHER,  # the health panel's groups have none for alerts
        )
    )
    job_types.register_job_type(
        JobType(
            name=BACKUP_DB_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=80,
            # a copy, its check and its hash, then the encrypted off-PC copy
            max_runtime=timedelta(minutes=30),
            retry_limit=2,  # a failed copy never raises: only recording its row is retried
            handler=handlers.backup_db(backups),
            group=JobGroup.BACKUP,
        )
    )
    job_types.register_job_type(
        JobType(
            name=RESTORE_TEST_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=70,
            # fetch and decrypt the newest off-PC copy, then its checks
            max_runtime=timedelta(minutes=30),
            retry_limit=2,  # a failed test never raises: only recording its row is retried
            handler=handlers.restore_test(restore_tests),
            group=JobGroup.BACKUP,
        )
    )
    job_types.register_job_type(
        JobType(
            name=CHECK_RECOVERY_JOB,
            default_lane=Lane.REALTIME,
            lanes=frozenset({Lane.REALTIME}),
            priority=85,
            # quick: each step's handler enqueues its work, it doesn't do it
            max_runtime=timedelta(minutes=10),
            retry_limit=2,  # the trigger fires every minute while recovery mode is on
            handler=handlers.check_recovery(recovery),
            group=JobGroup.BACKUP,
        )
    )
    job_types.register_job_type(
        JobType(
            name=RESOLVE_CONTACT_MARKETS_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=40,
            # a one-off: every Contact stored before Story 3.2, 200 per transaction
            max_runtime=timedelta(minutes=30),
            retry_limit=3,
            handler=handlers.resolve_contact_markets(contact_markets),
            group=JobGroup.OTHER,
        )
    )
    job_types.register_job_type(
        JobType(
            name=ENRICH_LEAD_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),
            priority=40,
            # robots.txt, then up to 15 pages at 1 to 10 s apart within the 10-minute deadline
            max_runtime=timedelta(minutes=20),
            retry_limit=handlers.ENRICH_LEAD_RETRIES,  # the last attempt never fails a flaky site
            handler=handlers.enrich_lead(crawls),
            group=JobGroup.ENRICHMENT,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=CANONICALIZE_LEAD_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),
            priority=45,
            max_runtime=timedelta(minutes=2),
            retry_limit=2,
            handler=handlers.canonicalize_lead(discovery),
            group=JobGroup.ENRICHMENT,
        )
    )
    job_types.register_job_type(
        JobType(
            name=RENDER_PAGES_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=40,  # the enrich_lead chain's: priority is set per job type
            # up to 3 renders of 20 s each, each with its own browser launch
            max_runtime=timedelta(minutes=5),
            retry_limit=2,  # a failed render never raises: only recording the run is retried
            handler=handlers.render_pages(crawls),
            group=JobGroup.ENRICHMENT,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=PURGE_PAGE_TEXT_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=20,
            max_runtime=timedelta(minutes=10),
            retry_limit=2,
            handler=handlers.purge_page_text(research),
            group=JobGroup.OTHER,
        )
    )
    job_types.register_job_type(
        JobType(
            name=PURGE_PLACE_COORDS_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=20,
            max_runtime=timedelta(minutes=10),
            retry_limit=2,
            handler=handlers.purge_place_coords(discovery),
            group=JobGroup.OTHER,
        )
    )
    job_types.register_job_type(
        JobType(
            name=EXTRACT_LEAD_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),
            priority=45,
            # one Claude call on up to 180,000 characters, then the apply transaction
            max_runtime=timedelta(minutes=15),
            retry_limit=3,
            handler=handlers.extract_lead(extractions),
            group=JobGroup.ENRICHMENT,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=CHECK_LEGAL_FORM_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),
            priority=45,
            max_runtime=timedelta(minutes=5),  # at most five registry calls, 15 s per phase
            retry_limit=3,
            handler=handlers.check_legal_form(legal_forms),
            group=JobGroup.ENRICHMENT,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=SELECT_PRIMARY_CONTACT_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),
            priority=45,
            max_runtime=timedelta(minutes=5),
            retry_limit=3,
            handler=handlers.select_primary_contact(primary),
            group=JobGroup.ENRICHMENT,
        )
    )
    job_types.register_job_type(
        JobType(
            name=RUN_AI_TEST_SETS_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=30,
            # every case of the set, one Claude call each (50 for the Reply set)
            max_runtime=timedelta(hours=2),
            retry_limit=3,  # a retryable gateway failure retries the whole run
            handler=handlers.run_ai_test_sets(test_runs),
            group=JobGroup.OTHER,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=CHECK_DOMAINS_JOB,
            default_lane=Lane.INTERACTIVE,
            lanes=frozenset({Lane.INTERACTIVE}),  # a Recheck lands in seconds, not behind crawls
            priority=60,
            # three TXT queries of at most 5 s each, then the insert
            max_runtime=timedelta(minutes=2),
            retry_limit=3,
            handler=handlers.check_domains(checks),
            group=JobGroup.SENDING,
        )
    )
    job_types.register_job_type(
        JobType(
            name=CHECK_JOB_BOARDS_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),
            priority=45,
            # one paced request per board, then the apply transaction
            max_runtime=timedelta(minutes=10),
            retry_limit=3,
            handler=handlers.check_job_boards(job_boards),
            group=JobGroup.ENRICHMENT,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=RECHECK_EVENT_JOB,
            default_lane=Lane.BULK,  # AD-10: re-checks run on the bulk lane
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),  # interactive before a send (AD-5)
            priority=50,
            max_runtime=timedelta(minutes=5),
            retry_limit=3,
            handler=handlers.recheck_event(job_boards),
            group=JobGroup.RECHECK,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=ERASE_EXTERNAL_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=60,
            # one store: a few Telegram deletes, a sink file rewrite or a few reads
            max_runtime=timedelta(minutes=5),
            retry_limit=handlers.ERASE_EXTERNAL_RETRIES,  # the last attempt settles it failed
            handler=handlers.erase_external(
                erasure_stores if erasure_stores is not None else _no_erasure_stores
            ),
            group=JobGroup.OTHER,
        )
    )
    job_types.register_job_type(
        JobType(
            name=FIND_CONTACT_EMAIL_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),
            priority=45,
            max_runtime=timedelta(minutes=5),  # four paid calls at 15 s each, database waits
            retry_limit=3,
            handler=handlers.find_contact_email(found),
            group=JobGroup.ENRICHMENT,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=VERIFY_CONTACT_EMAIL_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),
            priority=45,
            max_runtime=timedelta(minutes=5),  # one MX lookup and one 15 s call, database waits
            retry_limit=3,
            handler=handlers.verify_contact_email(verifying),
            group=JobGroup.ENRICHMENT,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=SCORE_LEAD_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK, Lane.INTERACTIVE}),  # interactive for "Re-score"
            priority=40,
            max_runtime=timedelta(minutes=5),
            retry_limit=3,
            handler=handlers.score_lead(scores),
            group=JobGroup.ENRICHMENT,
        )
    )
    job_types.register_job_type(
        JobType(
            name=RECOMPUTE_TIERS_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),
            priority=20,
            max_runtime=timedelta(minutes=10),
            retry_limit=2,
            handler=handlers.recompute_tiers(scores),
            group=JobGroup.ENRICHMENT,
        )
    )
    job_types.register_job_type(
        JobType(
            name=POLL_UNSUBSCRIBES_JOB,
            default_lane=Lane.REALTIME,
            lanes=frozenset({Lane.REALTIME}),  # an opt-out lands within one polling cycle
            priority=80,
            # one GET (10 s connect, 15 s per phase), then the apply transaction
            max_runtime=timedelta(minutes=5),
            retry_limit=3,  # the next firing retries anyway: the Worker keeps every request
            handler=handlers.poll_unsubscribes(polls),
            group=JobGroup.UNSUBSCRIBE_POLL,
        )
    )
    job_types.register_job_type(
        JobType(
            name=DISCOVER_PLACES_JOB,
            default_lane=Lane.BULK,  # AD-10: supply work runs on the bulk lane
            lanes=frozenset({Lane.BULK}),
            priority=30,
            # up to 3 Places pages of 20 candidates, each a paced robots.txt and homepage fetch
            max_runtime=timedelta(hours=1),
            retry_limit=2,
            handler=handlers.discover_places(discovering),
            group=JobGroup.DISCOVERY,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=PROCESS_SIGNAL_ITEM_JOB,
            default_lane=Lane.INTERACTIVE,
            lanes=frozenset({Lane.INTERACTIVE}),  # a pasted item is processed on submission
            priority=60,
            max_runtime=timedelta(minutes=5),  # one Claude call, then the apply transaction
            retry_limit=handlers.PROCESS_SIGNAL_ITEM_RETRIES,  # the last attempt flags the item
            handler=handlers.process_signal_item(inbox),
            group=JobGroup.DISCOVERY,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=CHECK_GROUNDING_JOB,
            default_lane=Lane.INTERACTIVE,  # an edit recheck, a Step 1; bulk for a footer change
            lanes=frozenset({Lane.INTERACTIVE, Lane.BULK}),
            priority=50,
            max_runtime=timedelta(minutes=25),  # up to four Claude requests of 300 s each
            retry_limit=3,  # a timeout is a recorded fail, never retried; only a bug retries
            handler=handlers.check_grounding(checks_grounding),
            group=JobGroup.OTHER,
        )
    )
    job_types.register_job_type(
        JobType(
            name=DISCOVER_REGISTRY_JOB,
            default_lane=Lane.BULK,  # AD-10: supply work runs on the bulk lane
            lanes=frozenset({Lane.BULK}),
            priority=30,
            # up to 10 search pages of 20 companies, each a profile, a Places lookup, a paced
            # robots.txt and homepage fetch and an officers list
            max_runtime=timedelta(hours=1),
            retry_limit=2,
            handler=handlers.discover_registry(registry_runs),
            group=JobGroup.DISCOVERY,
            budget_class=BudgetClass.BLOCKABLE,
        )
    )
    job_types.register_job_type(
        JobType(
            name=RECHECK_LEADS_JOB,
            default_lane=Lane.BULK,  # AD-10: supply work runs on the bulk lane
            lanes=frozenset({Lane.BULK}),
            priority=20,
            max_runtime=timedelta(minutes=10),  # reads and enqueues only
            retry_limit=2,
            handler=handlers.recheck_leads(rechecking),
            group=JobGroup.RECHECK,
        )
    )
    job_types.register_job_type(
        JobType(
            name=RECHECK_LEAD_JOB,
            default_lane=Lane.BULK,  # AD-10: re-checks run on the bulk lane, never essential
            lanes=frozenset({Lane.BULK}),
            priority=30,
            # a full re-check is the enrich_lead crawl, inline: robots.txt and up to 15 pages
            max_runtime=timedelta(minutes=20),
            retry_limit=handlers.RECHECK_LEAD_RETRIES,  # the last attempt never fails a site
            handler=handlers.recheck_lead(rechecking),
            group=JobGroup.RECHECK,
            budget_class=BudgetClass.BLOCKABLE,  # Story 8.1: supply work
        )
    )
    job_types.register_job_type(
        JobType(
            name=DRAFT_STEP_JOB,
            default_lane=Lane.INTERACTIVE,  # Story 4.15: Step 1 is due at once; bulk on request
            lanes=frozenset({Lane.INTERACTIVE, Lane.BULK}),
            priority=40,
            max_runtime=timedelta(minutes=15),  # a Claude request of 300 s and its doubled retry
            retry_limit=3,  # an unavailable API is retried; nothing is stored before the answer
            handler=handlers.draft_step(drafts_steps),
            group=JobGroup.OTHER,
        )
    )
    job_types.register_job_type(
        JobType(
            name=SUBMIT_BULK_AI_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),  # Story 4.17: the daily batch submit
            priority=30,
            max_runtime=timedelta(minutes=30),  # rebuilding the requests, one create per route
            retry_limit=3,  # an unavailable API is retried; the items stay pending meanwhile
            handler=handlers.submit_bulk_ai(batch_owners),
            group=JobGroup.OTHER,
            concurrency_key=AI_BATCHES_KEY,
        )
    )
    job_types.register_job_type(
        JobType(
            name=POLL_AI_BATCHES_JOB,
            default_lane=Lane.BULK,
            lanes=frozenset({Lane.BULK}),  # Story 4.17: every 15 minutes
            priority=30,
            max_runtime=timedelta(hours=2),  # each lost item is re-run as a direct call
            retry_limit=3,  # a settled item is never touched again, so a retry is safe
            handler=handlers.poll_ai_batches(batch_owners),
            group=JobGroup.OTHER,
            concurrency_key=AI_BATCHES_KEY,
        )
    )
    job_types.freeze()
    return job_types


def no_place_discovery() -> handlers.DiscoveringPlaces:
    """``build_job_types``'s default ``place_discovery``: a registry built without the service
    fails a ``discover_places`` job instead of discovering nothing."""
    raise RuntimeError("this job type registry was built without place_discovery")


def _unwired() -> Never:
    """A service provider of the enqueue-only registry (``enqueue_only_job_types``): it never
    runs a job."""
    raise RuntimeError("the enqueue-only job type registry never runs a job")


def enqueue_only_job_types() -> JobTypes:
    """``build_job_types`` with every service unwired (Story 4.14): the registry of the queue
    that the ``FooterChanged`` subscriber's ``Drafting`` enqueues its revisions'
    ``check_grounding`` jobs through. It only enqueues (each job type's lanes and priority are
    the runner's own); the runner's registry runs the jobs."""
    return build_job_types(
        alerts=_unwired,
        backups=_unwired,
        restore_tests=_unwired,
        recovery=_unwired,
        contact_markets=_unwired,
        crawls=_unwired,
        discovery=_unwired,
        research=_unwired,
        extractions=_unwired,
        primary=_unwired,
        test_runs=_unwired,
        job_boards=_unwired,
        legal_forms=_unwired,
        scores=_unwired,
        domain_checks=_unwired,
        erasure_stores=_unwired,
        finding=_unwired,
        unsubscribes=_unwired,
        place_discovery=_unwired,
        verification=_unwired,
        signal_inbox=_unwired,
        grounding=_unwired,
        registry_discovery=_unwired,
        step_drafts=_unwired,
        ai_batches=_unwired,
    )


def no_grounding() -> handlers.CheckingGrounding:
    """``build_job_types``'s default ``grounding``: a registry built without the service fails a
    ``check_grounding`` job instead of checking nothing."""
    raise RuntimeError("this job type registry was built without grounding")


def no_registry_discovery() -> handlers.DiscoveringRegistry:
    """``build_job_types``'s default ``registry_discovery``: a registry built without the
    service fails a ``discover_registry`` job instead of discovering nothing."""
    raise RuntimeError("this job type registry was built without registry_discovery")


def no_ai_batches() -> Sequence[handlers.BatchingAi]:
    """``build_job_types``'s default ``ai_batches`` (Story 4.17): a registry built without the
    batch owners fails a ``submit_bulk_ai`` or ``poll_ai_batches`` job instead of doing
    nothing."""
    raise RuntimeError("this job type registry was built without ai_batches")


def no_step_drafts() -> handlers.DraftingSteps:
    """``build_job_types``'s default ``step_drafts`` (Story 4.15): a registry built without the
    service fails a ``draft_step`` job instead of drafting nothing."""
    raise RuntimeError("this job type registry was built without step_drafts")


def no_domain_checks() -> handlers.DomainChecking:
    """``build_job_types``'s default ``domain_checks``: a registry built without the service
    fails a ``check_domains`` job instead of checking nothing."""
    raise RuntimeError("this job type registry was built without domain_checks")


def no_unsubscribes() -> handlers.PollingUnsubscribes:
    """``build_job_types``'s default ``unsubscribes``: a registry built without the service fails
    a ``poll_unsubscribes`` job instead of polling nothing."""
    raise RuntimeError("this job type registry was built without unsubscribes")


def _no_erasure_stores() -> handlers.StoreErasing:
    """``erase_external`` with no ``ErasureStores`` wired: fail the job, without a retry."""
    raise RuntimeError("erase_external has no ErasureStores wired")


def postmaster_grant_missing(google: GoogleAccess) -> bool:
    """Whether the ``postmaster`` grant is missing or unusable (``check_stored_grant`` raises:
    no client, not granted, needs re-consent, or Credential Manager failed); never calls
    Google."""
    try:
        google.check_stored_grant(POSTMASTER_GRANT)
    except AdapterError:
        return True
    return False


def build_health_issues(
    *,
    budget: Budget,
    clock: Clock | None = None,
    ledger: Path | None = None,
    postmaster_grant_missing: Callable[[], bool] | None = None,
) -> HealthIssues:
    """The health-issue registry, frozen: jobqueue's dead jobs, then the budget's AI spend and
    quotas, then the failed backups, then the Sales Playbook's problem, then the Sending
    Domains whose cold sends are blocked and their reputation issues (with one
    ``postmaster_grant_missing`` issue while ``postmaster_grant_missing()`` is true; never when
    it is ``None``), read at ``clock.now()`` (``SystemClock()`` when ``clock`` is ``None``),
    then discovery's "Discovery stopped" issues (Story 8.1), then the
    erasures' open requests and failed stores, then the unsubscribe poll failing for over 24
    hours (Story 3.12, also read at ``clock.now()``), then the LinkedIn Tasks setup (Story
    10.2), then, given the erasure ledger's path ``ledger`` (``build_app`` always gives it),
    compliance's missing or unreapplied ledger (Story 3.16). Each later owner registers its
    ``queries.<owner>.health_issues`` here, before ``freeze()``."""
    now = (clock if clock is not None else SystemClock()).now
    issues = HealthIssues()
    issues.register_source(IssueSource("jobqueue", jobqueue_queries.health_issues))
    issues.register_source(IssueSource("budget", budget.health_issues))
    issues.register_source(IssueSource("backups", ops_queries.health_issues))
    issues.register_source(IssueSource("playbook", playbook_queries.health_issues))
    issues.register_source(
        IssueSource(
            "sending",
            lambda: sending_queries.health_issues(now(), postmaster_grant_missing),
        )
    )
    issues.register_source(
        IssueSource("discovery", lambda: discovery_queries.health_issues(budget.status()))
    )
    issues.register_source(IssueSource("erasure", compliance_queries.health_issues))
    issues.register_source(
        IssueSource(
            "unsub_poll",
            lambda: compliance_queries.unsub_poll_issues(now(), polling_on=unsub_polling_on()),
        )
    )
    issues.register_source(IssueSource("pipeline", pipeline_queries.health_issues))  # Story 10.2
    if ledger is not None:
        issues.register_source(
            IssueSource(
                "compliance", functools.partial(compliance_queries.ledger_health_issues, ledger)
            )
        )
    issues.freeze()
    return issues


def unsub_polling_on() -> bool:
    """Whether unsubscribe polling is on: the ``unsub_worker_host`` setting is set, read in the
    current unit of work."""
    return bool(setup_queries.get(Key.UNSUB_WORKER_HOST))


def build_triggers(
    job_types: JobTypes,
    *,
    backup_schedule: Callable[[], DailyRun],
    restore_test_schedule: Callable[[], DailyRun],
    recovery_active: Callable[[], bool],
    domain_check_schedule: Callable[[], DailyRun],
    unsub_poll_period: timedelta = POLL_PERIOD_OTHER,
    bulk_ai_schedule: Callable[[], DailyRun] | None = None,
) -> Triggers:
    """The trigger registry (AD-10), frozen: register each trigger with
    ``register_trigger(Trigger.X, Every(...))`` or ``DailyAt(...)``, after its job type, before
    ``freeze()``. ``backup_schedule`` returns the daily backup's ``DailyRun`` and
    ``restore_test_schedule`` the daily restore-test check's; the scheduler calls them when it
    starts. ``recovery_active`` says whether recovery mode is on; the scheduler calls it before
    each ``check_recovery`` firing. ``domain_check_schedule`` returns the daily domain checks'
    ``DailyRun``, read when the scheduler starts. ``unsub_poll_period`` is how often
    ``poll_unsubscribes`` fires (``build_app`` passes ``poll_period(config.env)``: 5 minutes in
    ``prod``, 30 elsewhere). ``bulk_ai_schedule`` returns the daily ``submit_bulk_ai``'s
    ``DailyRun`` (Story 4.17; ``None``: the 14:00 UTC fallback)."""
    triggers = Triggers(job_types)
    triggers.register_trigger(
        Trigger.BACKUP_DB,
        DailyAt(backup_schedule, fallback=DailyRun(DEFAULT_BACKUP_TIME, FALLBACK_TIMEZONE)),
    )
    triggers.register_trigger(
        Trigger.RESTORE_TEST,
        DailyAt(
            restore_test_schedule,
            fallback=DailyRun(DEFAULT_RESTORE_TEST_TIME, FALLBACK_TIMEZONE),
        ),
    )
    triggers.register_trigger(
        Trigger.CHECK_RECOVERY, Every(CHECK_PERIOD, at_startup=True, active=recovery_active)
    )
    # the PC is not on at fixed times: purge at every start too
    triggers.register_trigger(
        Trigger.PURGE_PAGE_TEXT, Every(PURGE_PAGE_TEXT_PERIOD, at_startup=True)
    )
    triggers.register_trigger(
        Trigger.PURGE_PLACE_COORDS, Every(PURGE_PLACE_COORDS_PERIOD, at_startup=True)
    )
    triggers.register_trigger(
        Trigger.CHECK_DOMAINS,
        DailyAt(domain_check_schedule, fallback=DailyRun(DOMAIN_CHECK_TIME, FALLBACK_TIMEZONE)),
    )
    # Events age out by the day: re-tier daily, and at every start too
    triggers.register_trigger(
        Trigger.RECOMPUTE_TIERS, Every(RECOMPUTE_TIERS_PERIOD, at_startup=True)
    )
    # the PC is not on at fixed times: poll at every start too, so requests made while it was
    # off land at once
    triggers.register_trigger(Trigger.POLL_UNSUBSCRIBES, Every(unsub_poll_period, at_startup=True))
    # Story 8.7: the PC is not on at fixed times: pick the day's re-checks at every start too
    triggers.register_trigger(Trigger.RECHECK_LEADS, Every(RECHECK_LEADS_PERIOD, at_startup=True))
    # Story 4.17: an ended batch is settled within 15 minutes, and at every start
    triggers.register_trigger(
        Trigger.POLL_AI_BATCHES, Every(POLL_AI_BATCHES_PERIOD, at_startup=True)
    )
    bulk_fallback = DailyRun(DEFAULT_BULK_AI_SUBMIT_AT, FALLBACK_TIMEZONE)
    triggers.register_trigger(
        Trigger.SUBMIT_BULK_AI,
        DailyAt(
            bulk_ai_schedule if bulk_ai_schedule is not None else lambda: bulk_fallback,
            fallback=bulk_fallback,
        ),
    )
    triggers.freeze()
    return triggers


def build_local_backups(config: BootstrapConfig, clock: Clock) -> LocalBackups:
    """The local backup copies: ``<backup dir>\\local``, copied from ``<data dir>\\nextron.db``
    with SQLite's backup API. Building it creates no folder."""
    return LocalBackups(
        clock,
        env=config.env,
        local_dir=config.backup_dir / LOCAL_DIR_NAME,
        snapshot=functools.partial(snapshot, config.data_dir / DB_FILE_NAME),
    )


def build_offpc_backups(
    config: BootstrapConfig, clock: Clock, targets: BackupTargets
) -> OffpcBackups:
    """The encrypted off-PC copies: this environment's names, the data and backup dirs (which
    the off-PC folder must not be in, and where the erasure ledger is) and ``targets``, the
    ``BackupTargetPort`` of a folder. Building it reaches no folder and reads no secret."""
    return OffpcBackups(
        clock,
        env=config.env,
        data_dir=config.data_dir,
        backup_dir=config.backup_dir,
        targets=targets,
    )


def build_restore_tests(
    config: BootstrapConfig, clock: Clock, offpc: OffpcBackups, notify: Notify, queue: JobQueue
) -> RestoreTests:
    """The restore test: this environment's work folders under ``<backup dir>\\restore_test``,
    the off-PC copies of ``offpc`` and ``inspect_copy``, which reads a restored copy read-only.
    Building it reaches no folder and reads no secret."""
    return RestoreTests(
        clock,
        env=config.env,
        backup_dir=config.backup_dir,
        offpc=offpc,
        inspect=inspect_copy,
        notify=notify,
        queue=queue,
    )


def read_restore_test_schedule(units: Units, tests: RestoreTests) -> DailyRun:
    """The daily restore-test check's ``DailyRun``: ``tests.schedule()`` read in its own
    read-only unit of work inside a fresh ``contextvars.Context()``, so it works whatever the
    caller has open; ``catch_up`` is whether this month's slot was missed."""

    def read() -> DailyRun:
        with units.open(read_only=True):
            found = tests.schedule()
        return DailyRun(found.at, found.timezone, catch_up=found.missed)

    return contextvars.Context().run(read)


def build_recovery_steps(
    reapply: Callable[[RecoveryContext], bool] | None = None,
    repoll: Callable[[RecoveryContext], bool] | None = None,
) -> RecoverySteps:
    """The recovery step registry (Story 1.19), frozen: the only code that constructs
    ``RecoverySteps`` or calls ``register_step``. Given ``reapply`` (``build_app`` passes
    ``ErasureLedger.reapply``), Story 3.16 registers ``reapply_erasure_ledger`` (owner
    ``compliance``, a ``startup`` step: it runs before the job runner); given ``repoll``
    (``build_app`` passes ``UnsubscribePolls.repolled``), Story 3.12 registers
    ``repoll_unsubscribes`` (owner ``compliance``, not a ``startup`` step: the
    ``check_recovery`` job runs it); with no argument the registry is empty. Story 6.12 adds
    ``mark_oms_unknown`` and ``reingest_mailboxes`` with ``register_step(RecoveryHandler(step,
    owner, run, startup))`` before ``freeze()``. A step with no handler becomes
    ``not_applicable``."""
    steps = RecoverySteps()
    if reapply is not None:
        steps.register_step(
            RecoveryHandler(
                RecoveryStep.REAPPLY_ERASURE_LEDGER, "compliance", reapply, startup=True
            )
        )
    if repoll is not None:
        steps.register_step(
            RecoveryHandler(
                RecoveryStep.REPOLL_UNSUBSCRIBES, "compliance", repoll, startup=False
            )
        )
    steps.freeze()
    return steps


def read_recovery_mode(units: Units) -> bool:
    """Whether recovery mode is on: ``queries.ops.recovery_mode()`` read in its own read-only
    unit of work inside a fresh ``contextvars.Context()``; the ``check_recovery`` trigger's
    ``active``."""

    def read() -> bool:
        with units.open(read_only=True):
            return ops_queries.recovery_mode()

    return contextvars.Context().run(read)


def read_backup_schedule(units: Units, backups: Backups) -> DailyRun:
    """The daily backup's ``DailyRun``: ``backups.schedule()`` read in its own read-only unit
    of work inside a fresh ``contextvars.Context()``, so it works whatever the caller has
    open; ``catch_up`` is whether the last daily time was missed."""

    def read() -> DailyRun:
        with units.open(read_only=True):
            found = backups.schedule()
        return DailyRun(found.at, found.timezone, catch_up=found.missed)

    return contextvars.Context().run(read)


class RunningBatches(Protocol):
    """What ``BatchJobs`` needs of a batch owner (Story 4.18)."""

    def submit_queued(self) -> int: ...

    def poll_open(self) -> int: ...


class BatchJobs:
    """Every batch owner's submit and poll as the one use case of a ``submit_bulk_ai`` or
    ``poll_ai_batches`` job (Story 4.18, AD-3: a job's entry point calls exactly one use case;
    the owners, ``research`` and ``drafting``, sit side by side in the app DAG, so the
    composition root joins them): ``submit_bulk()`` and ``poll()`` call each owner's
    ``submit_queued()`` / ``poll_open()`` in order and return the sum."""

    def __init__(self, owners: Sequence[RunningBatches]) -> None:
        self._owners = tuple(owners)

    @use_case
    def submit_bulk(self) -> int:
        return sum(owner.submit_queued() for owner in self._owners)

    @use_case
    def poll(self) -> int:
        return sum(owner.poll_open() for owner in self._owners)


class ScheduledBatching(Protocol):
    """What ``read_bulk_ai_schedule`` needs of a batch owner (Story 4.17)."""

    def schedule(self) -> BulkAiSchedule: ...


def read_bulk_ai_schedule(units: Units, owners: Sequence[ScheduledBatching]) -> DailyRun:
    """The daily ``submit_bulk_ai``'s ``DailyRun`` (Story 4.17): each batch owner's
    ``schedule()`` read in its own read-only unit of work inside a fresh
    ``contextvars.Context()``; the time and timezone are the first owner's (they are the
    settings'), and ``catch_up`` is whether any owner holds an item queued before the latest
    daily time."""

    def read() -> DailyRun:
        with units.open(read_only=True):
            found = [owner.schedule() for owner in owners]
        if not found:
            raise RuntimeError("no batch owner is registered")
        return DailyRun(
            found[0].at, found[0].timezone, catch_up=any(each.missed for each in found)
        )

    return contextvars.Context().run(read)


def read_domain_check_schedule(units: Units, checks: DomainChecks) -> DailyRun:
    """The daily domain checks' ``DailyRun`` (Story 6.5): ``checks.schedule()`` read in its own
    read-only unit of work inside a fresh ``contextvars.Context()``, so it works whatever the
    caller has open; ``catch_up`` is whether a listed Sending Domain has no check since the
    latest daily time."""

    def read() -> DailyRun:
        with units.open(read_only=True):
            found = checks.schedule()
        return DailyRun(found.at, found.timezone, catch_up=found.missed)

    return contextvars.Context().run(read)


class PreMigrateBackupFailed(RuntimeError):
    """The ``pre_migrate`` backup failed, so the database was not migrated; the message is the
    backup's error."""


class PreMigrateBackup:
    """``ensure_current``'s ``before_upgrade`` hook in ``serve``: take a ``pre_migrate`` copy
    and keep it in ``taken``; raise ``PreMigrateBackupFailed`` when it failed, so nothing is
    migrated."""

    def __init__(self, local: LocalBackups) -> None:
        self._local = local
        self.taken: TakenBackup | None = None

    def __call__(self, before: str) -> None:
        taken = self._local.take(BackupKind.PRE_MIGRATE)
        self.taken = taken
        if taken.error is not None:
            raise PreMigrateBackupFailed(taken.error)


def engine_lifespan(
    runner: Runner,
    scheduler: Scheduler,
    writer: HeartbeatWriter,
    recovery_startup: Callable[[], None],
    *,
    startup: Callable[[], None] | None = None,
) -> Lifespan:
    """The app's lifespan: run the recovery steps' startup pass (``recovery_startup``, on a
    worker thread; if it raises, the server doesn't start), then ``startup`` (on a worker
    thread; an exception is logged as ``startup_step_failed`` and never stops the engine), then
    start the runner (on a worker
    thread), the scheduler (on the event loop) and the heartbeat writer (on a worker thread)
    when the server starts, and stop them in reverse when it stops. Each part that started is
    stopped even when a later start, or the server, failed. The recovery pass goes first: a
    start it refuses writes nothing else, and ``startup`` sees the state recovery left."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(recovery_startup)  # before the runner: a failure stops the start
        if startup is not None:
            try:
                await asyncio.to_thread(startup)
            except Exception as error:  # noqa: BLE001 - a startup step never stops the engine
                log.error("startup_step_failed", extra={"error": type(error).__name__})
        async with contextlib.AsyncExitStack() as started:
            await asyncio.to_thread(runner.start)
            started.push_async_callback(asyncio.to_thread, runner.stop)
            scheduler.start()
            started.push_async_callback(scheduler.stop)
            await asyncio.to_thread(writer.start)
            started.push_async_callback(asyncio.to_thread, writer.stop)
            yield

    return lifespan


class SinkRole(StrEnum):
    """The roles whose adapters reach people, so are wrapped in a sink outside ``prod``."""

    MAIL = "mail"
    ALERT = "alert"


@dataclass(frozen=True, slots=True)
class AdapterContext:
    """What a role adapter's ``build`` may use: the bootstrap config, the environment's secrets,
    the clock and the usage meter."""

    config: BootstrapConfig
    secrets: SecretsPort
    clock: Clock
    meter: UsageMeterPort


@dataclass(frozen=True, slots=True)
class RoleAdapter:
    """One registered ``mail`` or ``alert`` adapter: ``build(context)`` returns an
    ``adapter_type``."""

    name: str
    role: SinkRole
    adapter_type: type
    build: Callable[[AdapterContext], object]

    def __post_init__(self) -> None:
        if not (isinstance(self.name, str) and self.name):
            raise TypeError("a role adapter's name must be a non-empty str")
        if not isinstance(self.role, SinkRole):
            raise TypeError(f"role adapter {self.name}: the role must be a SinkRole")
        if not isinstance(self.adapter_type, type):
            raise TypeError(f"role adapter {self.name}: adapter_type must be a class")
        if not callable(self.build):
            raise TypeError(f"role adapter {self.name}: build must be callable")


# The role adapter registry (AD-14): the only place a mail or alert adapter is constructed.
# Story 1.12 added Telegram; Stories 6.x and 7.9 add the others. Only build_role_adapter reads
# it.
ROLE_ADAPTERS: tuple[RoleAdapter, ...] = (
    RoleAdapter(
        "telegram", SinkRole.ALERT, TelegramAlert, lambda c: TelegramAlert(c.secrets, meter=c.meter)
    ),
)

ROLE_PORTS: Final[Mapping[SinkRole, type]] = MappingProxyType(
    {SinkRole.MAIL: MailPort, SinkRole.ALERT: AlertPort}
)
ROLE_SINKS: Final[Mapping[SinkRole, type[MailSink] | type[AlertSink]]] = MappingProxyType(
    {SinkRole.MAIL: MailSink, SinkRole.ALERT: AlertSink}
)


def read_sink_allowlist(units: Units) -> tuple[str, ...]:
    """The ``sink_allowlist`` setting, read in its own read-only unit of work inside a fresh
    ``contextvars.Context()``, so it works while the caller's unit of work is open (between its
    transactions)."""

    def read() -> tuple[str, ...]:
        with units.open(read_only=True):
            return tuple(setup_queries.get(Key.SINK_ALLOWLIST))

    return contextvars.Context().run(read)


def build_role_adapter(
    name: str, context: AdapterContext, *, units: Units
) -> AlertPort | MailPort:
    """Build the role adapter registered as ``name``; outside ``prod``, wrapped in its role's
    sink. Raises ``LookupError`` unless ``name`` is registered exactly once, and ``TypeError``
    when ``build`` returns something other than an ``adapter_type`` that implements the role's
    port."""
    matches = [entry for entry in ROLE_ADAPTERS if entry.name == name]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} adapters are registered as {name!r}; expected one")
    [entry] = matches
    adapter = entry.build(context)
    if not isinstance(adapter, entry.adapter_type):
        raise TypeError(
            f"role adapter {entry.name} built a {type(adapter).__name__}, "
            f"not a {entry.adapter_type.__name__}"
        )
    port = ROLE_PORTS[entry.role]
    if not isinstance(adapter, port):
        raise TypeError(f"role adapter {entry.name} does not implement {port.__name__}")
    if not sink_applies(context.config.env):
        return adapter  # type: ignore[return-value]  # an AlertPort or MailPort, checked above
    recorder = SinkRecorder(context.config.data_dir / SINK_DIR_NAME, context.clock)
    allowlist = functools.partial(read_sink_allowlist, units)
    return ROLE_SINKS[entry.role](adapter, allowlist, recorder)  # type: ignore[arg-type]


def build_subscribers(clock: Clock | None = None) -> Subscribers:
    """The notification subscriber registry (AD-3), frozen. Register each subscriber with
    ``register_subscriber(<Notification>, <owner command>)``, in the order AD-3 lists, before
    ``freeze()``. The subscribers' services use ``clock``, or a ``SystemClock``."""
    chosen = clock if clock is not None else SystemClock()
    subscribers = Subscribers()
    subscribers.register_subscriber(LeadsMerged, Pipeline(chosen).reparent_lead)
    subscribers.register_subscriber(LeadsMerged, Research(chosen).reparent_lead)
    subscribers.register_subscriber(LeadsMerged, Sequencing(chosen).reparent_lead)
    # Story 2.16: the merged Lead's Signal Inbox items
    subscribers.register_subscriber(LeadsMerged, SignalInboxRows(chosen).reparent_lead)
    # Story 5.2: the merged Lead's OutboundMessages name the survivor
    subscribers.register_subscriber(LeadsMerged, sending_api.reparent_lead)
    # Story 4.8: stop the Enrollments first, then close an opted-out Lead (AD-3's order)
    subscribers.register_subscriber(SubjectSuppressed, Sequencing(chosen).stop_suppressed)
    # Story 4.9: then cancel the ready Drafts of every ended Enrollment (the backstop)
    subscribers.register_subscriber(SubjectSuppressed, Approval(chosen).cancel_pending_suppressed)
    subscribers.register_subscriber(SubjectSuppressed, Pipeline(chosen).close_suppressed)
    # Story 3.13: compliance's hook first (it reads the Contact before research tombstones it);
    # hook-only, so no queue (its erase_contact fails closed)
    erasures = Erasures(chosen, Compliance(chosen))
    subscribers.register_subscriber(ContactErased, erasures.erase_pii)
    # Story 2.16: the Signal Inbox items that may hold the Contact's data (it reads the Contact's
    # plaintext name and addresses, so it runs before research tombstones them)
    subscribers.register_subscriber(ContactErased, discovery_api.erase_pii)
    subscribers.register_subscriber(ContactErased, Research(chosen).erase_pii)
    # Story 4.6: the Operator's override cases of the Contact (ai_test_cases.input)
    subscribers.register_subscriber(ContactErased, Playbook(chosen).erase_pii)
    # Story 4.8: stop the erased Contact's live Enrollments, before the no-op hooks
    subscribers.register_subscriber(ContactErased, Sequencing(chosen).erase_pii)
    # Story 5.2: sending's hook tombstones the Contact's OutboundMessage copies
    for owner_api in (setup_api, drafting_api, sending_api, jobqueue_api, ops_api, notify_api):
        subscribers.register_subscriber(ContactErased, owner_api.erase_pii)
    # Stories 9.1 and 10.7, last: the erased Contact's Lead's notes, then its Channel
    # Permissions (revoked, number and handle NULL); linkedin_accounts never holds a Contact's
    # data
    subscribers.register_subscriber(ContactErased, Pipeline(chosen).erase_pii)
    subscribers.register_subscriber(Enrolled, Pipeline(chosen).on_enrolled)
    # Story 4.9: the next Step's due time, or the Enrollment's completion
    subscribers.register_subscriber(MessageSent, Sequencing(chosen).on_step_sent)
    # Story 4.12: a footer change recomposes the Market's ready Drafts
    # (Story 4.14: each new revision's check_grounding job, through an enqueue-only queue)
    enqueue_only = JobQueue(chosen, enqueue_only_job_types())
    subscribers.register_subscriber(FooterChanged, Drafting(chosen, enqueue_only).recompose_ready)
    subscribers.freeze()
    return subscribers


def connect_database(config: BootstrapConfig) -> UnitOfWorkFactory:
    """The entry points' unit-of-work factory on ``<data dir>\\nextron.db``, with the frozen
    subscriber registry. It connects lazily and never migrates."""
    subscribers = build_subscribers()
    engine = create_engine(config.data_dir / DB_FILE_NAME)
    try:
        return UnitOfWorkFactory(engine, subscribers)
    except BaseException:
        engine.dispose()
        raise


def open_database(
    config: BootstrapConfig, *, before_upgrade: Callable[[str], None] | None = None
) -> tuple[UnitOfWorkFactory, str]:
    """Open ``<data dir>\\nextron.db`` and bring it to head; return the entry points' factory and
    the database's revision. ``before_upgrade(before)`` runs just before the upgrade of a
    non-empty database behind head, in the migration's transaction (``ensure_current``).

    Raises ``DatabaseStateError`` (``DatabaseAheadError`` included) or ``ConnectionSettingError``
    when the database is refused, whatever ``before_upgrade`` raised, and whatever made opening
    or a migration fail; either way the engine is disposed first.
    """
    units = connect_database(config)
    try:
        before, after = ensure_current(units.engine, before_upgrade=before_upgrade)
    except BaseException:
        units.engine.dispose()
        raise
    if before != after:
        log.info("database_migrated", extra={"from_revision": before, "to_revision": after})
    return units, after


def create_app_from_environment() -> FastAPI:
    """App factory for Uvicorn's reload worker, which starts in a fresh process. The reloader
    has already brought the database to head, so the worker only connects."""
    config = load_bootstrap()
    return build_app(config, units=connect_database(config))


def run_uvicorn(config: BootstrapConfig, reload: bool, units: UnitOfWorkFactory) -> None:
    """Serve on 127.0.0.1 only, with one worker, until Uvicorn stops. Without reload the app
    reads through ``units``; the reload worker builds its own (``create_app_from_environment``)."""
    if reload:
        uvicorn.run(
            "nextron.main:create_app_from_environment",
            factory=True,
            reload=True,
            reload_dirs=[str(PACKAGE_DIR)],
            host=HOST,
            port=config.port,
            workers=WORKERS,
            proxy_headers=False,
            log_config=None,
        )
    else:
        uvicorn.run(
            build_app(config, units=units),
            host=HOST,
            port=config.port,
            workers=WORKERS,
            proxy_headers=False,
            log_config=None,
        )


def load_prompts(
    folder: Path | None = None,
) -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...]]:
    """The verified prompt files of ``folder`` (``routes.PROMPTS_DIR`` unless given) and each
    route's default candidate; ``InvalidPrompts`` when they and ``manifest.lock`` disagree. File
    I/O only: it opens no unit of work. It imports the AI routes table here, so the other
    commands never load the AI package or its SDK."""
    from nextron.adapters.ai import routes  # noqa: PLC0415 - only serve loads the AI package

    root = folder if folder is not None else routes.PROMPTS_DIR
    try:
        manifest = (root / MANIFEST_FILE_NAME).read_bytes()
    except FileNotFoundError:
        raise InvalidPrompts(f"{MANIFEST_FILE_NAME}: the file doesn't exist") from None
    entries = parse_manifest(decode_prompt(manifest, MANIFEST_FILE_NAME))
    files: dict[str, str] = {}
    for path in sorted(root.rglob(f"*{PROMPT_SUFFIX}")):  # any depth: verify refuses a stray one
        if path.is_file():
            name = path.relative_to(root).as_posix()
            files[name] = decode_prompt(path.read_bytes(), name)
    return verify(entries, files), routes.default_specs()


def read_playbook_file(path: Path) -> PlaybookSnapshot | PlaybookProblem:
    """The Sales Playbook snapshot of the file at ``path``, or the problem that kept it from
    one: ``unreadable`` for any ``OSError`` (a missing file included), else
    ``read_playbook``'s. File I/O only: it opens no unit of work, and it reads at most
    ``MAX_PLAYBOOK_BYTES`` + 1 bytes, so a huge file is ``too_large`` without being loaded."""
    try:
        with path.open("rb") as file:
            data = file.read(MAX_PLAYBOOK_BYTES + 1)
    except OSError:
        return PlaybookProblem.UNREADABLE
    try:
        return read_playbook(data)
    except InvalidPlaybook as exc:
        return exc.problem


def load_ai_content(
    playbook_path: Path,
) -> tuple[tuple[PromptFile, ...], tuple[RouteSpec, ...], PlaybookSnapshot | PlaybookProblem]:
    """The AI content view's loader: ``load_prompts()`` and ``read_playbook_file(playbook_path)``,
    outside any unit of work. ``InvalidPrompts`` propagates."""
    files, defaults = load_prompts()
    return files, defaults, read_playbook_file(playbook_path)


def _case_file(path: Path, name: str) -> object:
    """The JSON of one case file; ``InvalidTestCases`` naming it when it can't be read."""
    try:
        with path.open("rb") as file:
            data = file.read(MAX_CASE_FILE_BYTES + 1)
    except OSError:
        raise InvalidTestCases(name, "the file couldn't be read") from None
    if len(data) > MAX_CASE_FILE_BYTES:
        raise InvalidTestCases(name, "the file is larger than 1 MiB")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise InvalidTestCases(name, "the file isn't UTF-8 JSON") from None


def load_test_cases(folder: Path = AI_SETS_DIR) -> tuple[TestCase, ...]:
    """The AI Test Set cases of ``folder`` (Stories 4.5 and 4.6): every ``*.json`` file of each
    set's folder (``CASE_FOLDERS``), by name; ``InvalidTestCases`` naming the folder or the file
    for a missing folder or a bad file. File I/O only: it opens no unit of work."""
    cases: list[TestCase] = []
    for test_set in CASE_FOLDERS:
        found = folder / test_set.value
        if not found.is_dir():
            raise InvalidTestCases(test_set.value, "the folder doesn't exist")
        for path in sorted(found.glob(f"*{CASE_FILE_SUFFIX}")):
            name = f"{test_set.value}/{path.name}"
            parsed = _case_file(path, name)
            if test_set is TestSet.EXTRACTION:
                cases.append(extraction_case(name, parsed))  # its stem is the file's stem
            else:
                cases.append(set_case(test_set, path.name, parsed))
    return tuple(cases)


def sync_prompts(
    units: UnitOfWorkFactory,
    load: PromptLoader,
    read: Callable[[], PlaybookSnapshot | PlaybookProblem],
    err: TextIO,
    log_file: Path,
    load_cases: TestCaseLoader,
) -> bool:
    """``serve``'s prompt step: ``load()``, ``read()`` and ``load_cases()`` outside any unit of
    work, then ``Playbook.sync_prompts`` in one, then ``Playbook.sync_test_cases`` in the next
    (a unit of work runs one use case). A Playbook
    problem prints ``warning: playbook: <problem text>`` and the step goes on. False, after
    printing and logging why, when it failed."""
    try:
        files, defaults = load()
        playbook = read()
        cases = load_cases()
        if isinstance(playbook, PlaybookProblem):
            print(f"warning: playbook: {problem_text(playbook)}", file=err)
        prompts = Playbook(SystemClock())
        with units.open():
            prompts.sync_prompts(files, defaults, playbook)
        with units.open():  # a unit of work runs one use case
            prompts.sync_test_cases(cases)
    except InvalidPrompts as exc:
        print(f"startup check failed: {PROMPTS_CHECK}: {exc.message}", file=err)
        log.error("startup_check_failed", extra={"check": PROMPTS_CHECK})
        return False
    except InvalidTestCases as exc:
        print(f"startup check failed: {AI_SETS_CHECK}: {exc.message}", file=err)
        log.error("startup_check_failed", extra={"check": AI_SETS_CHECK})
        return False
    except Exception as exc:  # noqa: BLE001 - logged with its traceback; startup stops
        log.exception("prompt_sync_failed")
        print(
            f"prompt sync failed ({type(exc).__name__}); the traceback is in {log_file}",
            file=err,
        )
        return False
    return True


def _exit_on_ctrl_break(signum: int, frame: FrameType | None) -> None:
    """The SIGBREAK handler ``serve`` installs while the server runs: a clean stop, exit 0."""
    raise SystemExit(0)


@contextlib.contextmanager
def _stop_on_ctrl_break() -> Iterator[None]:
    """On Windows, a SIGBREAK handler that raises ``SystemExit(0)`` while the server runs, then
    the previous one back.

    Uvicorn saves this handler, catches CTRL_BREAK itself, shuts down gracefully, restores the
    handler and raises the signal again to it. A CTRL_BREAK before Uvicorn takes the signal over
    lands here directly. Either way ``run_server`` ends with ``SystemExit(0)`` and ``serve``
    exits 0. Elsewhere, or off the main thread (where ``signal.signal`` can't be called), it
    changes nothing.
    """
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is None or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(sigbreak, _exit_on_ctrl_break)
    try:
        yield
    finally:
        signal.signal(sigbreak, previous if previous is not None else signal.SIG_DFL)


def _report_failures(results: list[CheckResult], err: TextIO) -> bool:
    failures = [result for result in results if result.status is Status.FAIL]
    for result in failures:
        print(f"startup check failed: {result.id}: {result.message}", file=err)
    return bool(failures)


def serve(
    *,
    reload: bool = False,
    probes: Probes | None = None,
    run_server: ServerRunner = run_uvicorn,
    err: TextIO | None = None,
    load_prompts: PromptLoader = load_prompts,
    read_playbook: PlaybookReader = read_playbook_file,
    load_test_cases: TestCaseLoader = load_test_cases,
) -> int:
    """Start the engine for ``NEXTRON_ENV``; return the process exit code."""
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap()
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    if reload and config.env != "dev":
        print("--reload is allowed only when NEXTRON_ENV=dev", file=err)
        return EXIT_STARTUP_FAILED

    probes = probes if probes is not None else Probes.system()
    if _report_failures(environment_checks(config.data_dir, probes), err):
        return EXIT_STARTUP_FAILED

    lock = InstanceLock(config.data_dir / LOCK_FILE_NAME)
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        (config.data_dir / LOGS_DIR_NAME).mkdir(exist_ok=True)
        lock.acquire()
    except LockHeldError:
        print(f"another Nextron instance is running for {config.env}", file=err)
        return EXIT_LOCK_HELD
    except OSError as exc:
        print(f"cannot prepare {exc.filename or config.data_dir}: {exc.strerror or exc}", file=err)
        return EXIT_STARTUP_FAILED
    try:
        configure_logging(config.data_dir / LOGS_DIR_NAME)
        port_check = check_port_free(HOST, config.port, probes)
        if _report_failures([port_check], err):
            log.error("startup_check_failed", extra={"check": port_check.id})
            return EXIT_STARTUP_FAILED
        local = build_local_backups(config, SystemClock())
        pre_migrate = PreMigrateBackup(local)
        try:
            units, db_revision = open_database(config, before_upgrade=pre_migrate)
        except PreMigrateBackupFailed as exc:
            print(
                f"startup check failed: {PRE_MIGRATE_BACKUP_CHECK}: {exc}; "
                "the database was not migrated",
                file=err,
            )
            code = None if pre_migrate.taken is None else pre_migrate.taken.code
            log.error("pre_migrate_backup_failed", extra={"code": code})
            return EXIT_STARTUP_FAILED
        except (DatabaseStateError, ConnectionSettingError) as exc:
            print(f"startup check failed: {DATABASE_REVISION_CHECK}: {exc}", file=err)
            log.error("startup_check_failed", extra={"check": DATABASE_REVISION_CHECK})
            return EXIT_STARTUP_FAILED
        except Exception:
            copy = pre_migrate.taken
            message = (
                "database startup failed: it could not be opened, or a migration failed and "
                f"was rolled back; the traceback is in "
                f"{config.data_dir / LOGS_DIR_NAME / LOG_FILE_NAME}"
            )
            if copy is not None and copy.verified:
                message += (
                    f". The database was copied before the migration to {copy.path}; "
                    f'to go back to that copy, run: nextron restore "{copy.path}"'
                )
                log.exception("database_migration_failed", extra={"file": copy.path.name})
            else:
                log.exception("database_migration_failed")
            print(message, file=err)
            return EXIT_STARTUP_FAILED
        try:
            if pre_migrate.taken is not None:  # a verified copy, and the upgrade succeeded
                record_pre_migrate(units, local, pre_migrate.taken)
            log_file = config.data_dir / LOGS_DIR_NAME / LOG_FILE_NAME
            read = functools.partial(read_playbook, config.playbook_path)
            if not sync_prompts(units, load_prompts, read, err, log_file, load_test_cases):
                return EXIT_STARTUP_FAILED
            identity = {"env": config.env, "version": __version__}
            log.info("engine_started", extra={**identity, "db_revision": db_revision})
            exit_code = EXIT_CRASHED
            try:
                with _stop_on_ctrl_break():
                    run_server(config, reload, units)
                exit_code = EXIT_OK
            except SystemExit as exc:
                if exc.code in (None, 0):
                    exit_code = EXIT_OK
                else:
                    print(f"server failed to start (Uvicorn exit code {exc.code})", file=err)
                    log.error("server_start_failed", extra={"exit_code": exc.code})
                    exit_code = EXIT_STARTUP_FAILED
            except Exception as exc:
                log.exception(CRASH_EVENT)
                mark_logged(exc)  # the uncaught-exception hook would log it a second time
                raise
            finally:
                log.info("engine_stopped", extra={**identity, "exit_code": exit_code})
            return exit_code
        finally:
            units.engine.dispose()
    finally:
        lock.release()


def record_pre_migrate(units: UnitOfWorkFactory, local: LocalBackups, taken: TakenBackup) -> None:
    """Record the ``pre_migrate`` copy taken before a successful upgrade; a failure logs
    ``backup_record_failed`` and startup goes on (the copy stays on disk)."""
    try:
        with units.open():
            local.record_pre_migrate(taken)
    except Exception as exc:  # noqa: BLE001 - the upgrade succeeded; the engine still starts
        log.exception(
            "backup_record_failed", extra={"code": error_code(exc), "file": taken.path.name}
        )


class DoctorMeter:
    """``doctor``'s ``UsageMeterPort``: it keeps each recorded call in memory (``calls``) and
    writes nothing, so doctor's own Google refresh never reaches ``api_usage`` or the Quotas
    page. ``snapshot`` returns an empty ``Usage`` and ``day_calls`` and ``units_since`` 0. Doctor's unsubscribe
    Worker probe (Story 3.12) is metered here too."""

    def __init__(self) -> None:
        self.calls: list[ApiCall | AiCall] = []

    def record(self, call: ApiCall | AiCall) -> None:
        self.calls.append(call)

    def snapshot(self, now: datetime) -> Usage:
        return Usage(month=month_of(now), api=(), ai_calls=0, ai_cost_micros=0)

    def day_calls(self, now: datetime, provider: str, operation: str) -> int:
        return 0

    def units_since(self, provider: str, sku: str | None, since: datetime) -> int:
        del provider, sku, since
        return 0


class ReadinessDatabase(Protocol):
    """What the ``offpc_backup``, ``required_settings``, ``database_revision``,
    ``unsub_token_keys`` and ``unsub_worker`` checks read: whether the database file exists, its
    revision (``None`` when empty; a database that isn't this engine's raises ``CheckFailed``),
    the required settings that are unset or no longer valid, the ``backup_offpc_dir`` setting,
    the ``unsub_active_kid`` setting and the ``unsub_worker_host`` setting."""

    def exists(self) -> bool: ...

    def revision(self) -> str | None: ...

    def required_unset(self) -> tuple[str, ...]: ...

    def offpc_dir(self) -> str: ...

    def unsub_active_kid(self) -> int: ...

    def unsub_worker_host(self) -> str: ...


class DoctorDatabase:
    """``doctor``'s read-only view of the database file at ``path``: it never creates,
    migrates or writes it. The read-only engine (``create_read_only_engine``) is built on first
    use; ``close()`` disposes it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._units: UnitOfWorkFactory | None = None

    def exists(self) -> bool:
        """Whether the database file is there."""
        return self._path.exists()

    def _read_only_units(self) -> UnitOfWorkFactory:
        if self._units is None:
            engine = create_read_only_engine(self._path)
            try:
                self._units = UnitOfWorkFactory(engine, build_subscribers())
            except BaseException:
                engine.dispose()
                raise
        return self._units

    def revision(self) -> str | None:
        """The database's revision, ``None`` for an empty one. A database that isn't this
        engine's raises ``CheckFailed`` with the reason."""
        try:
            return current_revision(self._read_only_units().engine)
        except (DatabaseStateError, ConnectionSettingError) as exc:
            raise CheckFailed(str(exc)) from None

    def required_unset(self) -> tuple[str, ...]:
        """The required settings that are unset or no longer valid: every one when there is no
        database file, else ``queries.setup.required_unset()`` in a read-only unit of work."""
        if not self.exists():
            return tuple(
                str(found.key) for found in settings_schema.declared() if found.required
            )
        with self._read_only_units().open(read_only=True):
            return setup_queries.required_unset()

    def offpc_dir(self) -> str:
        """The ``backup_offpc_dir`` setting: blank when there is no database file, else read in
        a read-only unit of work (an error propagates)."""
        if not self.exists():
            return ""
        with self._read_only_units().open(read_only=True):
            return str(setup_queries.get(Key.BACKUP_OFFPC_DIR))

    def unsub_active_kid(self) -> int:
        """The ``unsub_active_kid`` setting: its schema default when there is no database
        file, else read in a read-only unit of work (an error propagates)."""
        if not self.exists():
            return int(settings_schema.setting(Key.UNSUB_ACTIVE_KID).default)
        with self._read_only_units().open(read_only=True):
            return int(setup_queries.get(Key.UNSUB_ACTIVE_KID))

    def unsub_worker_host(self) -> str:
        """The ``unsub_worker_host`` setting (Story 3.12): blank when there is no database
        file, else read in a read-only unit of work (an error propagates)."""
        if not self.exists():
            return ""
        with self._read_only_units().open(read_only=True):
            return str(setup_queries.get(Key.UNSUB_WORKER_HOST))

    def close(self) -> None:
        """Dispose the read-only engine, if one was built."""
        units, self._units = self._units, None
        if units is not None:
            units.engine.dispose()


class EngineDatabase:
    """The engine's view of its own database file at ``path``, for the health panel's
    readiness list: the revision and the required settings are read in the caller's unit of
    work (a dashboard GET's read-only one), never in one of their own."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def exists(self) -> bool:
        """Whether the database file is there."""
        return self._path.exists()

    def revision(self) -> str | None:
        """The database's revision, ``None`` for an empty one, read in the current unit of
        work. A database that isn't this engine's raises ``CheckFailed`` with the reason."""
        try:
            return current_revision_in_unit()
        except (DatabaseStateError, ConnectionSettingError) as exc:
            raise CheckFailed(str(exc)) from None

    def required_unset(self) -> tuple[str, ...]:
        """The required settings that are unset or no longer valid, read in the current unit
        of work."""
        return setup_queries.required_unset()

    def offpc_dir(self) -> str:
        """The ``backup_offpc_dir`` setting, read in the current unit of work."""
        return str(setup_queries.get(Key.BACKUP_OFFPC_DIR))

    def unsub_active_kid(self) -> int:
        """The ``unsub_active_kid`` setting, read in the current unit of work."""
        return int(setup_queries.get(Key.UNSUB_ACTIVE_KID))

    def unsub_worker_host(self) -> str:
        """The ``unsub_worker_host`` setting, read in the current unit of work."""
        return str(setup_queries.get(Key.UNSUB_WORKER_HOST))


@dataclass(frozen=True, slots=True)
class DoctorContext:
    """What the doctor checks read: the bootstrap config, where its environment came from
    (``--env`` or ``NEXTRON_ENV``), the machine probes, the environment's secrets, its Google
    access and its database, read-only. ``in_engine`` is set when the engine runs the checks
    for its health panel: ``port_free`` then probes nothing, ``internal_notify_grant`` reads
    only what is stored, never refreshing at Google, and ``unsub_worker`` never calls
    ``unsubscribe`` (the ``UnsubscribePort`` doctor probes the Worker through, Story 3.12)."""

    config: BootstrapConfig
    env_source: str
    probes: Probes
    secrets: SecretsPort
    google: GoogleAccess
    database: ReadinessDatabase = field(repr=False)
    in_engine: bool = False
    unsubscribe: UnsubscribePort | None = field(default=None, repr=False)


def build_doctor_checks() -> DoctorChecks[DoctorContext]:
    """The doctor check registry (FR-56), frozen: the eleven checks of Story 1.14, Story 1.17's
    ``offpc_backup`` (after ``internal_notify_grant``), Story 3.16's ``erasure_ledger`` (after
    ``offpc_backup``; it only looks at the file, never at the database's contents), Story 3.8's
    ``unsub_token_keys`` (after ``database_revision``) and Story 3.12's ``unsub_worker`` (last),
    fifteen in all, in the order doctor prints them (the health panel shows the same list). With
    ``DoctorContext.in_engine``, ``port_free``, ``internal_notify_grant`` and ``unsub_worker``
    take their no-probe, no-network forms. Later stories register their checks here, before
    ``freeze()``.

    The order rule: the health panel runs this registry inside a GET's read-only unit of work,
    where the first check that reads the database (``offpc_backup``'s setting read, then
    ``required_settings`` and ``database_revision``) begins the read transaction (AD-2's
    ``BEGIN IMMEDIATE``). Any check that reads Credential Manager, the registry or the network
    goes before them, and ``offpc_backup`` reads the Backup passphrase before its setting, so
    none of those reads runs while that lock is held. The one exception is
    ``unsub_token_keys``: its two keyring entries' names depend on the ``unsub_active_kid``
    setting, so it can't read Credential Manager first. It runs near the end, so only its own
    two local Credential Manager reads happen under the read lock; ``unsub_worker`` follows it
    for the same reason (the bearer is read only when the ``unsub_worker_host`` setting is set,
    and in the engine it makes no network call). A check that calls the network (or the
    off-PC folder) also needs an in-engine form (``c.in_engine``) that doesn't, because a GET
    makes no network call; ``offpc_backup`` touches no folder at all."""
    checks: DoctorChecks[DoctorContext] = DoctorChecks()
    for check in (
        DoctorCheck(PYTHON_VERSION, lambda c: check_python_version(c.probes)),
        DoctorCheck(SQLITE_VERSION, lambda c: check_sqlite_version(c.probes)),
        DoctorCheck(TZDATA, lambda c: check_tzdata(c.probes)),
        DoctorCheck(
            PROD_ENV_NOT_PERSISTENT,
            lambda c: check_prod_env_not_persistent(c.probes, c.config.env, c.env_source),
        ),
        DoctorCheck(
            DATA_DIR_OUTSIDE_ONEDRIVE,
            lambda c: check_data_dir_outside_onedrive(c.config.data_dir, c.probes),
        ),
        DoctorCheck(
            BACKUP_DIR_SEPARATE,
            lambda c: check_backup_dir_separate(c.config.data_dir, c.config.backup_dir, c.probes),
        ),
        DoctorCheck(
            PORT_FREE,
            lambda c: check_port_free(
                HOST,
                c.config.port,
                c.probes,
                heartbeat=c.config.data_dir / HEARTBEAT_FILE_NAME,
                in_engine=c.in_engine,
            ),
        ),
        DoctorCheck(REQUIRED_CREDENTIALS, lambda c: check_required_credentials(c.secrets.read)),
        DoctorCheck(INTERNAL_NOTIFY_GRANT, _internal_notify_grant),
        DoctorCheck(
            OFFPC_BACKUP,
            lambda c: check_offpc_backup(
                c.secrets.read, c.database.offpc_dir, c.config.data_dir, c.config.backup_dir
            ),
        ),
        DoctorCheck(
            ERASURE_LEDGER,
            lambda c: check_erasure_ledger(
                c.config.backup_dir / ERASURE_LEDGER_FILE, c.database.exists
            ),
        ),
        DoctorCheck(
            REQUIRED_SETTINGS, lambda c: check_required_settings(c.database.required_unset)
        ),
        DoctorCheck(
            DATABASE_REVISION_CHECK,
            lambda c: check_database_revision(
                c.database.exists(),
                c.database.revision,
                head=head_revision(),
                known=known_revisions(),
            ),
        ),
        DoctorCheck(
            UNSUB_TOKEN_KEYS,
            lambda c: check_unsub_token_keys(c.secrets.read, c.database.unsub_active_kid),
        ),
        DoctorCheck(UNSUB_WORKER, _unsub_worker),
    ):
        checks.register_check(check)
    checks.freeze()
    return checks


def _internal_notify_grant(c: DoctorContext) -> CheckResult:
    """``doctor`` refreshes the grant at Google; the engine only reads what is stored."""
    if c.in_engine:
        return check_internal_notify_grant(c.google.check_stored_grant, refresh=False)
    return check_internal_notify_grant(c.google.access_token)


def _unsub_worker(c: DoctorContext) -> CheckResult:
    """``doctor`` probes the unsubscribe Worker; the engine never calls it."""

    def probe(host: str) -> None:
        if c.unsubscribe is None:
            raise RuntimeError("doctor was given no unsubscribe port")
        c.unsubscribe.probe(host, deadline_s=DOCTOR_DEADLINE_S)

    return check_unsub_worker(
        c.secrets.read,
        c.database.unsub_worker_host,
        probe,
        in_engine=c.in_engine,
        deadline_s=DOCTOR_DEADLINE_S,
    )


def _never_open_browser(url: str) -> bool:
    """``doctor``'s browser: doctor never starts a consent flow, so it never opens one."""
    return False


def doctor(
    *,
    env: str | None = None,
    as_json: bool = False,
    probes: Probes | None = None,
    secrets: SecretsPort | None = None,
    google_oauth: GoogleOAuthPort | None = None,
    clock: Clock | None = None,
    unsubscribe: UnsubscribePort | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run every doctor check for ``env`` (``NEXTRON_ENV`` unless given) and print the results
    as a table, or as JSON with ``as_json``; return 0 when nothing is ``fail``, 1 otherwise and
    2 for an invalid bootstrap config. Secrets are keyring, Google is ``GoogleOAuthClient`` with
    a ``DoctorMeter``, the unsubscribe Worker is reached through ``WorkerUnsubscribe`` with a
    ``DoctorMeter`` (Story 3.12) and the clock is the system clock, unless injected."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap(env)
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    probes = probes if probes is not None else Probes.system()
    clock = clock if clock is not None else SystemClock()
    secrets = secrets if secrets is not None else KeyringSecrets(config.env)
    google = GoogleAccess(
        secrets,
        clock,
        oauth=google_oauth if google_oauth is not None else GoogleOAuthClient(meter=DoctorMeter()),
        listen=LoopbackListener,
        files=LocalClientFiles(),
        browser=_never_open_browser,
        env=config.env,
        allowlist=tuple,  # doctor never starts a consent flow, so it reads no allowlist
    )
    database = DoctorDatabase(config.data_dir / DB_FILE_NAME)
    context = DoctorContext(
        config=config,
        env_source=ENV_SOURCE_FLAG if env is not None else ENV_SOURCE_VARIABLE,
        probes=probes,
        secrets=secrets,
        google=google,
        database=database,
        unsubscribe=(
            unsubscribe
            if unsubscribe is not None
            else WorkerUnsubscribe(secrets, meter=DoctorMeter())
        ),
    )
    try:
        results = run_checks(build_doctor_checks(), context)
    finally:
        database.close()
    print(format_json(results) if as_json else format_table(results), file=out)
    failed = any(result.status is Status.FAIL for result in results)
    return EXIT_CHECKS_FAILED if failed else EXIT_OK


def open_dashboard(
    *,
    secrets: SecretsPort | None = None,
    clock: Clock | None = None,
    engine_running: EngineProbe = engine_is_running,
    browser: Browser | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Open the dashboard in the default browser (``browser``, ``open_in_browser`` unless
    injected) through a one-time code; return the exit code. Never prints or logs the code."""
    browser = browser if browser is not None else open_in_browser
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap()
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    if not engine_running(HOST, config.port):
        print(ENGINE_NOT_RUNNING, file=err)
        return EXIT_OPEN_FAILED
    entry = build_entry(config, secrets=secrets, clock=clock)
    try:
        code = entry.issue_code()
    except AdapterError:
        print(CODE_NOT_STORED, file=err)
        return EXIT_OPEN_FAILED
    try:
        opened = bool(browser(f"http://{HOST}:{config.port}/enter?t={code}"))
    except Exception:  # noqa: BLE001 - any browser failure withdraws the code
        opened = False
    if not opened:
        with contextlib.suppress(AdapterError):
            entry.withdraw_code()
        print(BROWSER_NOT_OPENED, file=err)
        return EXIT_OPEN_FAILED
    print(DASHBOARD_OPENED, file=out)
    return EXIT_OK


# Seed sync ------------------------------------------------------------------------------------


class _SeedFileProblem(Exception):
    """The seed file can't be used; the message is the reason, safe to print."""


def _read_seed_file(path: Path) -> str:
    """The text of the seed file at ``path``: UTF-8, at most ``MAX_SEED_FILE_BYTES``."""
    try:
        with path.open("rb") as file:
            data = file.read(MAX_SEED_FILE_BYTES + 1)
    except FileNotFoundError:
        raise _SeedFileProblem("the file doesn't exist") from None
    except OSError as exc:
        raise _SeedFileProblem(f"the file can't be read ({type(exc).__name__})") from None
    if len(data) > MAX_SEED_FILE_BYTES:
        raise _SeedFileProblem("the file is larger than 1 MiB")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _SeedFileProblem("the file isn't valid UTF-8") from None


def _seed_failed(exc: BaseException) -> str:
    return f"seed sync failed ({type(exc).__name__}); nothing was changed"


def _seed_database_problem(path: Path) -> str | None:
    """Why ``seed sync`` can't write the database at ``path``, or ``None`` when it exists and is
    at head. Reads it read-only, as doctor does; never creates or changes it."""
    if not path.exists():
        return SEED_DATABASE_MISSING
    try:
        engine = create_read_only_engine(path)
        try:
            revision = current_revision(engine)
        finally:
            engine.dispose()
    except (DatabaseStateError, ConnectionSettingError) as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - not a database this engine can read
        return _seed_failed(exc)
    if revision is None:  # an empty file: serve migrates it like a new one
        return SEED_DATABASE_MISSING
    head = head_revision()
    if revision == head:
        return None
    if revision not in known_revisions():
        return f"database revision {revision} is newer than this code"
    return (
        f"the database is at {revision}, not {head}: start the engine once (nextron serve) "
        "to migrate it, then run seed sync again"
    )


def _sync_block(
    name: str,
    file_label: str,
    result: SegmentSync | MarketSync | SequenceSync | OfferSync | CampaignSync,
) -> list[str]:
    """One table's summary lines: the counts, then a line per created, changed and not-in-file
    item."""
    lines = [
        f"{name}: {len(result.created) + len(result.changed)} changed, "
        f"{len(result.unchanged)} unchanged"
    ]
    lines += [f"  {key}: created (version {version})" for key, version in result.created]
    lines += [
        f"  {key}: changed (version {before} -> {after})" for key, before, after in result.changed
    ]
    lines += [
        f"  {key}: not in {file_label}, left as it is (version {version})"
        for key, version in result.not_in_file
    ]
    return lines


SEGMENTS_MANAGED: Final = "Segments are managed in the dashboard; seed/segments.yaml ignored"


SEQUENCES_MANAGED: Final = (
    "Sequences are managed in the dashboard; seed/sequences.yaml ignored"
)


CAMPAIGNS_MANAGED: Final = (
    "Campaigns are managed in the dashboard; seed/campaigns.yaml and seed/offers.yaml ignored"
)


def _segments_block(result: SegmentSync) -> list[str]:
    """The Segments' summary lines: ``_sync_block``'s while the database held none (the
    bootstrap), else (Story 11.5) the managed notice and a line per difference."""
    if not result.managed:
        return _sync_block("segments", SEGMENTS_FILE_LABEL, result)
    return _managed_block("segments", SEGMENTS_MANAGED, result.differences)


def _sequences_block(result: SequenceSync) -> list[str]:
    """The Sequences' summary lines, like the Segments' (Story 11.7)."""
    if not result.managed:
        return _sync_block("sequences", SEQUENCES_FILE_LABEL, result)
    return _managed_block("sequences", SEQUENCES_MANAGED, result.differences)


def _offers_block(result: OfferSync) -> list[str]:
    """The Offers' summary lines: ``_sync_block``'s while the database held no Campaign, else
    (Story 11.8) the Campaigns' managed notice and a line per difference."""
    if not result.managed:
        return _sync_block("offers", OFFERS_FILE_LABEL, result)
    return _managed_block("offers", CAMPAIGNS_MANAGED, result.differences)


def _campaigns_block(result: CampaignSync) -> list[str]:
    """The Campaigns' summary lines, like the Offers' (Story 11.8)."""
    if not result.managed:
        return _sync_block("campaigns", CAMPAIGNS_FILE_LABEL, result)
    return _managed_block("campaigns", CAMPAIGNS_MANAGED, result.differences)


def _managed_block(
    name: str,
    notice: str,
    differences: Iterable[
        SegmentDifference | SequenceDifference | OfferDifference | CampaignDifference
    ],
) -> list[str]:
    """A managed entity's notice and a line per difference between the file and the
    database."""
    lines = [f"{name}: {notice}"]
    for difference in differences:
        if difference.only_in == SEGMENT_ONLY_IN_FILE:
            lines.append(f"  {difference.key}: only in the file")
        elif difference.only_in == SEGMENT_ONLY_IN_DASHBOARD:
            lines.append(f"  {difference.key}: only in the dashboard")
        else:
            lines.append(f"  {difference.key}: differs in {', '.join(difference.paths)}")
    return lines


def _facts_block(result: CompanyFactSync) -> list[str]:
    """The Approved Company Facts' summary lines: the counts (with the conflicts, when there
    are any), then a line per created, changed, conflicting and not-in-file fact."""
    count = len(result.conflicts)
    conflicts = f", {count} conflict{'' if count == 1 else 's'}" if count else ""
    lines = [
        f"company_facts: {len(result.created) + len(result.changed)} changed, "
        f"{len(result.unchanged)} unchanged{conflicts}"
    ]
    lines += [f"  {fact_id}: created (version {version})" for fact_id, version in result.created]
    lines += [
        f"  {fact_id}: changed (version {before} -> {after})"
        for fact_id, before, after in result.changed
    ]
    lines += [
        f"  {fact_id}: conflict, left as the Operator set it (version {version}); "
        f"{COMPANY_FACTS_FILE_LABEL} differs"
        for fact_id, version in result.conflicts
    ]
    lines += [
        f"  {fact_id}: not in {COMPANY_FACTS_FILE_LABEL}, left as it is (version {version})"
        for fact_id, version in result.not_in_file
    ]
    return lines


def format_seed_sync(result: SeedSync) -> str:
    """``seed sync``'s summary: the Segments' block, then the Markets', the Sequences', the
    Approved Company Facts', the Offers' and the Campaigns'."""
    lines = _segments_block(result.segments)
    lines += _sync_block("markets", MARKETS_FILE_LABEL, result.markets)
    lines += _sequences_block(result.sequences)
    lines += _facts_block(result.company_facts)
    lines += _offers_block(result.offers)
    lines += _campaigns_block(result.campaigns)
    return "\n".join(lines)


def _load_seed[T](folder: Path, name: str, label: str, validate: Callable[[object], T]) -> T:
    """The validated definitions of the seed file ``folder / name``; ``_SeedFileProblem`` with
    the whole printed message (``<label>: ...``) for any problem."""
    try:
        text = _read_seed_file(folder / name)
    except _SeedFileProblem as exc:
        raise _SeedFileProblem(f"{label}: {exc}") from None
    try:
        return validate(seed_yaml.parse(text))
    except (
        SeedSyntaxError,
        InvalidSegments,
        InvalidMarkets,
        InvalidSequences,
        InvalidCompanyFacts,
        InvalidOffers,
        InvalidCampaigns,
    ) as exc:
        raise _SeedFileProblem(f"{label}: {exc.message}") from None


def seed_sync(
    *,
    env: str | None = None,
    seed_dir: Path | None = None,
    clock: Clock | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Load ``<seed_dir>\\segments.yaml``, ``<seed_dir>\\markets.yaml``,
    ``<seed_dir>\\sequences.yaml``, ``<seed_dir>\\company_facts.yaml``,
    ``<seed_dir>\\offers.yaml`` and ``<seed_dir>\\campaigns.yaml`` into the ``segments``,
    ``markets``, ``sequences``, ``step_briefs``, ``company_facts``, ``offers``, ``campaigns`` and
    ``variants`` tables for ``env`` (``NEXTRON_ENV`` unless given); return the exit code. See
    the module docstring."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap(env)
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    folder = seed_dir if seed_dir is not None else SEED_DIR
    try:
        segments = _load_seed(folder, SEGMENTS_FILE_NAME, SEGMENTS_FILE_LABEL, validate_segments)
        markets = _load_seed(folder, MARKETS_FILE_NAME, MARKETS_FILE_LABEL, validate_markets)
        sequences = _load_seed(
            folder, SEQUENCES_FILE_NAME, SEQUENCES_FILE_LABEL, validate_sequences
        )
        company_facts = _load_seed(
            folder, COMPANY_FACTS_FILE_NAME, COMPANY_FACTS_FILE_LABEL, validate_company_facts
        )
        offers = _load_seed(folder, OFFERS_FILE_NAME, OFFERS_FILE_LABEL, validate_offers)
        campaigns = _load_seed(
            folder, CAMPAIGNS_FILE_NAME, CAMPAIGNS_FILE_LABEL, validate_campaigns
        )
    except _SeedFileProblem as exc:
        print(exc, file=err)
        return EXIT_SEED_FAILED
    problem = _seed_database_problem(config.data_dir / DB_FILE_NAME)
    if problem is not None:
        print(problem, file=err)
        return EXIT_SEED_FAILED
    seeds = Seeds(clock if clock is not None else SystemClock())
    units: UnitOfWorkFactory | None = None
    try:
        units = connect_database(config)
        with units.open():
            result = seeds.sync_seed(
                segments, markets, sequences, company_facts, offers, campaigns
            )
    except InvalidSegments as exc:  # a key a stored Segment left out of the file still uses
        print(f"{SEGMENTS_FILE_LABEL}: {exc.message}", file=err)
        return EXIT_SEED_FAILED
    except InvalidMarkets as exc:  # a country or ccTLD a stored Market left out still uses
        print(f"{MARKETS_FILE_LABEL}: {exc.message}", file=err)
        return EXIT_SEED_FAILED
    except InvalidSequences as exc:  # an angle's reference, or a stored Sequence
        print(f"{SEQUENCES_FILE_LABEL}: {exc.message}", file=err)
        return EXIT_SEED_FAILED
    except InvalidCompanyFacts as exc:  # a reference, the floor, a date, or a stored fact
        print(f"{COMPANY_FACTS_FILE_LABEL}: {exc.message}", file=err)
        return EXIT_SEED_FAILED
    except InvalidOffers as exc:  # a stored Offer that no longer validates
        print(f"{OFFERS_FILE_LABEL}: {exc.message}", file=err)
        return EXIT_SEED_FAILED
    except InvalidCampaigns as exc:  # a reference, the floor, a stored Campaign's rules
        print(f"{CAMPAIGNS_FILE_LABEL}: {exc.message}", file=err)
        return EXIT_SEED_FAILED
    except Exception as exc:  # noqa: BLE001 - the transaction rolled back
        print(_seed_failed(exc), file=err)
        return EXIT_SEED_FAILED
    finally:
        if units is not None:
            units.engine.dispose()
    print(format_seed_sync(result), file=out)
    return EXIT_OK


# Restore (Story 1.19) ---------------------------------------------------------------------------


class RestoreRefused(Exception):
    """The restore refused before changing anything: ``reason`` is logged, ``text`` printed."""

    def __init__(self, reason: str, text: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.text = text


class RestoreFailed(Exception):
    """A restore step failed: ``stage`` and ``code`` are logged, ``text`` and ``outcome`` (what
    is left as it was, ``nothing was changed`` unless given) printed."""

    def __init__(self, stage: str, code: str, text: str, outcome: str = NOTHING_CHANGED) -> None:
        super().__init__(stage)
        self.stage = stage
        self.code = code
        self.text = text
        self.outcome = outcome


def _inside(path: Path, folder: Path) -> bool:
    """Whether ``path`` is ``folder`` or inside it, both resolved and compared with
    ``normcase``."""
    try:
        inner = os.path.normcase(str(path.resolve()))
        outer = os.path.normcase(str(folder.resolve()))
    except OSError:
        return False
    return inner == outer or inner.startswith(outer.rstrip("\\/") + os.sep)


def _same_file(left: Path, right: Path) -> bool:
    try:
        return right.exists() and left.samefile(right)
    except OSError:
        return False


def _check_restore_file(config: BootstrapConfig, file: Path) -> None:
    """Step 4: refuse anything but this environment's backup copy; ``RestoreRefused``."""
    name = file.name
    try:
        regular = file.is_file()
    except OSError:
        regular = False
    if not regular:
        raise RestoreRefused("file_missing", f"{file} is not a file")
    fmt = copy_format(name)
    if fmt is None:
        raise RestoreRefused(
            "not_a_copy", f"{name} is not a backup copy: the name must end .db or .db.enc"
        )
    env = copy_env(name)
    if env is not None and env != config.env:
        raise RestoreRefused(
            "other_environment", f"{name} is a backup of {env}, and this is {config.env}"
        )
    if _same_file(file, config.data_dir / DB_FILE_NAME):
        raise RestoreRefused("live_database", f"{name} is the live database itself")
    if _inside(file, config.data_dir / RESTORE_WORK_DIR_NAME):
        raise RestoreRefused("in_work_folder", f"{name} is inside the restore's work folder")
    if fmt != ENCRYPTED:
        wal = file.with_name(name + WAL_SUFFIX)
        try:
            live = wal.is_file() and wal.stat().st_size > 0
        except OSError as exc:
            raise RestoreFailed(
                "file", error_code(exc), f"{name}{WAL_SUFFIX} could not be read"
            ) from exc
        if live:
            raise RestoreRefused(
                "live_database",
                f"{name} has a non-empty {name}{WAL_SUFFIX} beside it, so it is a live "
                "database, not a backup copy",
            )


@dataclass
class _Restore:
    """One ``nextron restore`` run, with the lock held and logging configured."""

    config: BootstrapConfig
    file: Path
    yes: bool
    targets: Callable[[], BackupTargets]
    clock: Clock
    ask: Callable[[str], str]
    out: TextIO
    err: TextIO

    @property
    def db_path(self) -> Path:
        return self.config.data_dir / DB_FILE_NAME

    def run(self) -> int:
        """Steps 4 to 15; see the module docstring."""
        try:
            _check_restore_file(self.config, self.file)
            work_parent = self.config.data_dir / RESTORE_WORK_DIR_NAME
            prefix = f"nextron-{self.config.env}-"
            clear_work_folders(work_parent, prefix, RESTORE_CLEANUP_FAILED)
            try:
                work_parent.mkdir(parents=True, exist_ok=True)
                work = Path(tempfile.mkdtemp(prefix=prefix, dir=work_parent))
            except OSError as exc:
                raise RestoreFailed(
                    "work_folder", error_code(exc), "the restore's work folder could not be made"
                ) from exc
            try:
                return self._restore(work / CANDIDATE_NAME)
            finally:
                remove_work_folder(work, RESTORE_CLEANUP_FAILED)
        except RestoreRefused as refused:
            print(f"restore refused: {refused.text}; {NOTHING_CHANGED}", file=self.err)
            log.warning("restore_refused", extra={"reason": refused.reason})
            return EXIT_RESTORE_FAILED
        except RestoreFailed as failed:
            print(
                f"restore failed: {failed.text} ({failed.code}); {failed.outcome}", file=self.err
            )
            log.error("restore_failed", extra={"stage": failed.stage, "code": failed.code})
            return EXIT_RESTORE_FAILED

    def _candidate(self, candidate: Path) -> None:
        """Step 6: decrypt or copy the file into ``candidate``; the file is never changed."""
        try:
            if copy_format(self.file.name) == ENCRYPTED:
                self.targets()(self.file.parent).get(self.file.name, candidate)
            else:
                copy_candidate(self.file, candidate)
        except AdapterError as exc:
            raise RestoreRefused(exc.code, f"the copy could not be decrypted ({exc.code})") from exc
        except Exception as exc:
            raise RestoreFailed(
                "candidate", error_code(exc), "the copy could not be read"
            ) from exc

    def _confirmed(self, revision: str, head: str, backup_at: datetime) -> bool:
        """Step 9: print what will happen and ask for ``yes``."""
        kind = "an encrypted" if copy_format(self.file.name) == ENCRYPTED else "a plain"
        upgrade = "" if revision == head else f", upgraded to {head}"
        print(
            f"Restore {self.file.name} ({kind} copy at revision {revision}{upgrade}) into "
            f"{self.db_path}.",
            file=self.out,
        )
        print(
            f"The copy was taken at {backup_at:{RESTORE_TIME_FORMAT}} at the latest; recovery "
            "mode holds sending until the recovery steps have caught up from then.",
            file=self.out,
        )
        if self.db_path.exists():
            folder = self.config.backup_dir / PRE_RESTORE_DIR_NAME
            print(f"The current database is saved to {folder} first.", file=self.out)
        else:
            print("There is no current database to save.", file=self.out)
        try:
            answer = self.ask(CONFIRM_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return False
        return isinstance(answer, str) and answer.strip().casefold() == CONFIRM_ANSWER

    def _save_current(self, now: datetime) -> Path | None:
        """Step 10: the safety copy of the current database, or ``None`` when there is none."""
        if not self.db_path.exists():
            return None
        target = self.config.backup_dir / PRE_RESTORE_DIR_NAME / copy_name(self.config.env, now)
        try:
            save_current(self.db_path, target)
        except Exception as exc:
            raise RestoreFailed(
                "pre_restore",
                error_code(exc),
                f"the current database could not be saved to {target.parent}; if it is "
                f"damaged, move {DB_FILE_NAME}, {DB_FILE_NAME}-wal and {DB_FILE_NAME}-shm out "
                f"of {self.config.data_dir} yourself, then restore again",
            ) from exc
        log.info("pre_restore_backup_taken", extra={"file": target.name})
        return target

    def _prepare(
        self, candidate: Path, backup_at: datetime, pre_restore: Path | None
    ) -> tuple[str, str]:
        """Steps 11 and 12, on the candidate: upgrade it, then turn recovery mode on in it.
        Returns the revision it upgraded to and the new recovery row's ULID."""
        engine = create_engine(candidate)
        try:
            try:
                before, after = ensure_current(engine)
            except Exception as exc:
                raise RestoreFailed(
                    "migrate", error_code(exc), "the copy could not be upgraded"
                ) from exc
            if before != after:
                log.info(
                    "restore_candidate_migrated",
                    extra={"from_revision": before, "to_revision": after},
                )
            try:
                with UnitOfWorkFactory(engine, build_subscribers()).open():
                    row = Recovery(self.clock).begin(
                        backup_at=backup_at,
                        source_name=self.file.name,
                        pre_restore_name=None if pre_restore is None else pre_restore.name,
                    )
            except Exception as exc:
                raise RestoreFailed(
                    "recovery", error_code(exc), "recovery mode could not be turned on"
                ) from exc
        finally:
            engine.dispose()
        return after, row.id

    def _restore(self, candidate: Path) -> int:
        """Steps 6 to 15 in the work folder."""
        self._candidate(candidate)
        found = inspect_copy(candidate)  # step 7
        if found.integrity != INTEGRITY_PASSED:
            raise RestoreRefused(
                "integrity_failed", f"the copy failed its integrity check ({found.integrity})"
            )
        if found.revision is None:
            raise RestoreRefused("no_revision", "the copy has no Alembic revision")
        if not found.revision_known:
            raise RestoreRefused(
                "revision_unknown",
                f"the copy's revision {found.revision} is unknown to this code (newer code "
                "made it)",
            )
        now = self.clock.now()  # step 8
        try:
            backup_at = backup_time(self.file.name, newest_event_at(candidate), now)
        except Exception as exc:
            raise RestoreFailed(
                "backup_time", error_code(exc), "the copy's newest event could not be read"
            ) from exc
        if not self.yes and not self._confirmed(found.revision, found.head, backup_at):
            print(RESTORE_CANCELLED, file=self.err)
            log.warning("restore_refused", extra={"reason": "cancelled"})
            return EXIT_RESTORE_FAILED
        pre_restore = self._save_current(now)  # step 10
        try:
            revision, recovery_id = self._prepare(candidate, backup_at, pre_restore)
        except RestoreFailed:
            if pre_restore is not None:  # nothing was changed: no safety copy is left either
                discard(pre_restore)
            raise
        self._recover_ledger()  # step 12a (Story 3.16): never fails the restore
        try:  # step 13
            swap_in(candidate, self.db_path)
        except Exception as exc:
            outcome = DATABASE_UNCHANGED
            if pre_restore is not None:
                outcome += f"; its safety copy is at {pre_restore}"
            raise RestoreFailed(
                "swap", error_code(exc), "the copy could not be swapped in", outcome
            ) from exc
        problem = self._verify(revision, recovery_id)  # step 14
        if problem is not None:
            before = (
                f"the database from before the restore is at {pre_restore}; do not start the "
                "engine: run nextron restore again, or restore the safety copy"
                if pre_restore is not None
                else "there was no database before the restore; do not start the engine: run "
                "nextron restore again"
            )
            print(
                f"the restored database could not be verified ({problem}); {before}",
                file=self.err,
            )
            log.error("restore_failed", extra={"stage": "verify", "code": problem})
            return EXIT_RESTORE_FAILED
        print(  # step 15
            f"Restored {self.file.name} into {self.db_path}; recovery mode is on "
            f"(recovery {recovery_id}).",
            file=self.out,
        )
        if pre_restore is not None:
            print(f"The database from before the restore is at {pre_restore}.", file=self.out)
        print(
            "Start the engine: sending stays held until every recovery step is done.",
            file=self.out,
        )
        log.info(
            "database_restored",
            extra={
                "file": self.file.name,
                "from_revision": found.revision,
                "to_revision": revision,
                "recovery_id": recovery_id,
                "pre_restore": None if pre_restore is None else pre_restore.name,
            },
        )
        return EXIT_OK

    def _recover_ledger(self) -> None:
        """Step 12a: with no erasure ledger in the backup dir and an encrypted copy, decrypt
        the newest off-PC ledger copy beside the file into place; see the module docstring."""
        ledger = self.config.backup_dir / ERASURE_LEDGER_FILE
        if ledger.exists():
            return
        code = "not_found"
        if copy_format(self.file.name) == ENCRYPTED:
            try:
                target = self.targets()(self.file.parent)
                name = _newest_ledger_copy(self.config.env, target.list())
                if name is not None:
                    part = ledger.with_name(ledger.name + LEDGER_PART_SUFFIX)
                    _fresh_part(part)
                    target.get(name, part)
                    if ledger.exists():  # never over an existing ledger
                        raise FileExistsError(ledger.name)
                    os.rename(part, ledger)
                    print(f"erasure ledger recovered from {name}", file=self.out)
                    log.info("erasure_ledger_recovered", extra={"file": name})
                    return
            except Exception as exc:  # noqa: BLE001 - recovery mode still holds sending
                code = error_code(exc)
        print(
            f"no erasure ledger found: recovery mode holds sending until {ledger} exists",
            file=self.out,
        )
        log.warning("erasure_ledger_not_recovered", extra={"code": code})

    def _verify(self, revision: str, recovery_id: str) -> str | None:
        """Step 14: what is wrong with the live database after the swap, or ``None``."""
        try:
            check = restored_check(self.db_path)
        except Exception as exc:  # noqa: BLE001 - reported, with the safety copy's path
            return error_code(exc)
        if check.integrity != INTEGRITY_PASSED:
            return "integrity_failed"
        if check.revision != revision:
            return "revision_mismatch"
        if check.active_ids != (recovery_id,):
            return "recovery_row_mismatch"
        return None


LEDGER_PART_SUFFIX: Final = ".part"
_LEDGER_COPY_SHAPE: Final = ".ledger.jsonl.enc"


def _newest_ledger_copy(env: str, names: Iterable[str]) -> str | None:
    """The newest complete off-PC ledger copy of ``env`` among ``names``, or ``None``."""
    found = [
        (stamp, name)
        for name in names
        if name.endswith(_LEDGER_COPY_SHAPE) and (stamp := offpc_stamp(env, name)) is not None
    ]
    return max(found)[1] if found else None


def _fresh_part(part: Path) -> None:
    """Make the backup dir (a new PC has none yet) and clear a ``.part`` file an interrupted
    restore left, so the decryption can write it."""
    part.parent.mkdir(parents=True, exist_ok=True)
    if part.exists():
        discard(part)


def restore(
    file: Path | str,
    *,
    yes: bool = False,
    secrets: SecretsPort | None = None,
    targets: BackupTargets | None = None,
    clock: Clock | None = None,
    probes: Probes | None = None,
    ask: Callable[[str], str] = input,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Restore the backup copy ``file`` into this environment's database with the engine
    stopped, and turn recovery mode on; return 0 when restored, 1 when refused, cancelled or
    failed, 2 for an invalid bootstrap config or a failed environment check. An encrypted copy
    is decrypted through ``targets`` (``FolderBackupTarget`` with ``secrets``, keyring unless
    injected); the clock is the system clock unless injected. Without ``yes`` it asks
    ``ask("Type yes to restore: ")`` first."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap()
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    probes = probes if probes is not None else Probes.system()
    if _report_failures(environment_checks(config.data_dir, probes), err):
        return EXIT_STARTUP_FAILED
    lock = InstanceLock(config.data_dir / LOCK_FILE_NAME)
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        (config.data_dir / LOGS_DIR_NAME).mkdir(exist_ok=True)
        lock.acquire()
    except LockHeldError:
        print(STOP_THE_ENGINE, file=err)
        return EXIT_RESTORE_FAILED
    except OSError as exc:
        print(
            f"cannot prepare {exc.filename or config.data_dir}: {exc.strerror or exc}; "
            f"{NOTHING_CHANGED}",
            file=err,
        )
        return EXIT_RESTORE_FAILED
    try:
        configure_logging(config.data_dir / LOGS_DIR_NAME)

        def backup_targets() -> BackupTargets:  # built only for an encrypted copy
            if targets is not None:
                return targets
            return functools.partial(
                FolderBackupTarget,
                secrets=secrets if secrets is not None else KeyringSecrets(config.env),
            )

        return _Restore(
            config,
            Path(file),
            yes,
            backup_targets,
            clock if clock is not None else SystemClock(),
            ask,
            out,
            err,
        ).run()
    finally:
        lock.release()
