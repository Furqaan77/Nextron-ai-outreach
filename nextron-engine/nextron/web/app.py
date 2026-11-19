"""The dashboard's FastAPI application factory (AD-13, FR-56, FR-58 seed).

Routes, each a sync ``def`` declaring ``@personal_data`` (``nextron.web.routing``); every GET
runs in a read-only unit of work and every POST is CSRF-checked (``dashboard_route_class``):

- ``GET /enter`` (``enter``): only a navigation the browser started itself is accepted: a
  ``Sec-Fetch-Site`` header other than exactly one ``none`` (a link from another website, or
  from a server on another ``127.0.0.1`` port) gets 401 with the locked page before the code is
  looked at, logged with ``reason=cross_site``, and the pending code stays. Otherwise exactly
  one ``t`` that ``DashboardEntry.consume_code`` accepts starts a session and redirects (303)
  to ``/`` with the ``nextron_session`` cookie (``HttpOnly; Path=/; SameSite=Strict``).
  Anything else gets 401 with the locked page and no session. Both are
  ``Cache-Control: no-store``, and both are logged (``dashboard_session_opened``, or
  ``dashboard_entry_refused`` with ``reason``), never with the code or the token.
- ``GET /`` (``home``): the Dashboard. While today (in ``sender_timezone``) is an away day
  (Story 5.11), ``away_banner.html`` shows "Away until <end date>: no new Sequences are
  selected", the end date being ``Setup.away_until()`` (``queries.setup.away_until`` at the
  clock's now, in the GET's read-only unit of work) as ``YYYY-MM-DD``; otherwise no banner.
- ``GET /health`` (``health``): the environment, the engine version and the process uptime in
  whole seconds, and nothing else.
- ``GET /static/{name}`` (``static``): the vendored HTMX build and ``app.css``, read once when
  the app is built; any other name is 404.
- ``GET /settings`` (``settings_page``) and ``POST /settings`` (``settings_save``): the Settings
  page and its save, both ``@personal_data(True)``; see ``nextron.web.settings_page``. The POST
  saves through ``setup`` (``Setup.save``) in its own unit of work. The page also includes the
  Campaigns section (``campaigns_panel.html``, Story 4.2), built from ``queries.setup`` in the
  GET's read-only unit of work; see ``nextron.web.campaigns_page``. A stored row it reads that no
  longer decodes (``InvalidCampaigns``, ``InvalidOffers``, ``InvalidSequences``,
  ``InvalidSegments`` or ``InvalidMarkets``) shows "The Campaigns can't be shown: <field>:
  <reason>" in that section instead, and the settings form still renders.
- ``POST /settings/campaigns/activate`` (``campaign_activate``), ``POST
  /settings/campaigns/pause`` (``campaign_pause``), ``POST /settings/campaigns/resume``
  (``campaign_resume``) and ``POST /settings/campaigns/lia`` (``campaign_lia``), all
  ``@personal_data(False)``: the Campaigns section's controls. Each runs its use case through
  ``campaigns`` (``Campaigns``, the ``setup`` service) in its own unit of work, then renders the
  panel alone in a read-only one; a refused one (``CampaignRefused``) answers 200 with the
  refusal re-rendered and nothing changed.
- The Settings page also includes the LinkedIn account panel (``assist_accounts_panel.html``,
  Story 10.2), built from ``queries.pipeline.accounts()`` in the GET's read-only unit of work;
  see ``nextron.web.assist_accounts_page``. ``POST /settings/linkedin/add``
  (``assist_account_add``) and ``POST /settings/linkedin/save`` (``assist_account_save``), both
  ``@personal_data(True)`` (the owner's name), run ``assist_accounts`` (``AssistAccounts``, the
  ``pipeline`` service) in their own unit of work, then render the panel alone in a read-only
  one; a refused one (``AccountRefused``) answers 200 with the refusal re-rendered and nothing
  stored.
- ``GET /credentials`` (``credentials_page``, ``@personal_data(True)``, because its Google
  section shows each grant's account address), ``POST /credentials/save`` (``credential_save``)
  and ``POST /credentials/remove`` (``credential_remove``): the write-only Credentials page and
  its per-row save and two-step remove, the POSTs ``@personal_data(False)``; see
  ``nextron.web.credentials_page``. They go through ``credentials`` (``Credentials``), which
  touches no database, so the POSTs open no unit of work. The page's "Unsubscribe token keys"
  section (Story 3.8) reads the ``unsub_active_kid`` setting and the newest token of each kid in
  the GET's read-only unit of work, after every other Credential Manager read of the page, so
  only its own keys' reads run inside the read transaction.
- ``POST /credentials/unsub/save`` (``unsub_key_save``) and ``POST /credentials/unsub/remove``
  (``unsub_key_remove``), both ``@personal_data(False)``: the unsubscribe token keys' per-row
  save and two-step remove (Story 3.8), each running its ``Credentials`` call inside
  ``units.open(read_only=True)``, so the newest token is read before any keyring write; see
  ``nextron.web.credentials_page``.
- ``POST /credentials/google/client`` (``google_client_import``), ``POST
  /credentials/google/grant`` (``google_grant_start``), ``GET /credentials/google/grant/{grant}``
  (``google_grant_row``) and ``POST /credentials/google/remove`` (``google_grant_remove``): the
  Google section's client import, Grant, polled row and two-step remove; see
  ``nextron.web.google_page``. They go through ``google`` (``GoogleAccess``), which touches no
  database. Only ``google_grant_start`` starts a consent flow; Google's redirect is served by
  the flow's own listener, never by a dashboard route.
- ``GET /notifications`` (``notifications_area``) and ``POST /notifications/dismiss``
  (``notification_dismiss``), both ``@personal_data(True)``: the notification area (UX-DR5),
  which the base layout loads once per page view (``hx-trigger="load, call-outcome-saved
  from:body"``: it isn't polled, and it loads again when a Call Outcome is saved), and its
  Dismiss button. While recovery mode is on (``queries.ops.recovery_mode()``, Story 1.19),
  the area (``notifications.html``) starts with the banner ``<p class="recovery-banner"
  role="alert">``, ``RECOVERY_BANNER`` linked to the health panel; every page shows it, because
  every page loads the area. Then it shows "Engine health: <n> error
  needs attention" (or "<n> errors need attention"), linked to the health panel, when
  ``health.issue_count()`` is above 0. Then (Story 4.10) "signal shortfall: <n>" while today's
  remaining new-Sequence capacity (``selection.today().remaining``) is above 0. Then (Story 9.4)
  one "Call Outcome due: <Lead name>" line per due booking (``pipeline.call_outcomes_due()``,
  worked out at each render: no job or stored flag), linked to ``/leads/<id>#call-outcome``. It
  is empty when none of these shows and nothing is undismissed; otherwise it shows
  "Notifications (<count>)", the newest ``AREA_LIMIT`` (5), each with its text (``|plain``), its
  time as ``YYYY-MM-DD HH:MM UTC`` and a Dismiss form, and
  "and <n> more" when there are more. The POST's fields, apart from ``csrf_token``, must be
  exactly one ``notification`` holding a ULID (anything else is ``MalformedForm``, 400); it
  dismisses through ``notify`` (``Notify.dismiss``) in its own unit of work, then renders the
  area again in a read-only one.
- ``GET /facts`` (``company_facts_page``), ``POST /facts/add`` (``company_fact_add``), ``POST
  /facts/edit`` (``company_fact_edit``) and ``POST /facts/retract`` (``company_fact_retract``),
  all ``@personal_data(False)``: the Approved Company Facts page (FR-72, Story 4.3), linked from
  the Settings page; see ``nextron.web.facts_page``. Each POST runs its use case through
  ``facts`` (``CompanyFacts``, the ``playbook`` service) in its own unit of work, then renders
  the panel again in a read-only one; a refused one (``InvalidFact``) answers 200 with the
  refused form re-rendered.
- ``GET /segments`` (``segments_page``), ``GET /segments/{segment_id}`` (``segment_page``),
  ``GET /segments/{segment_id}/versions`` (``segment_versions_page``), ``POST /segments/create``
  (``segment_create``), ``POST /segments/{segment_id}/edit`` (``segment_edit``) and ``POST
  /segments/{segment_id}/rescore-current`` (``segment_rescore_current``), all
  ``@personal_data(False)``: the Segments section of Settings (FR-1, Story 11.5), linked from
  the Settings page; see ``nextron.web.segments_page``. The create and edit POSTs run
  ``segment_editor`` (``SegmentEditor``, the ``setup`` service) and the re-score POST
  ``lead_scores.request_segment_rescore`` in their own unit of work, then render the panel again
  in a read-only one; a refused save (``SegmentRefused``) answers 200 with the form re-rendered
  as submitted. An unknown Segment (a text that isn't a ULID included) answers 404 with the
  plain text "That Segment doesn't exist.", which never echoes the id.
- ``GET /sequences`` (``sequences_page``), ``GET /sequences/{sequence_id}`` (``sequence_page``),
  ``GET /sequences/{sequence_id}/versions`` (``sequence_versions_page``) and ``POST
  /sequences/{sequence_id}/edit`` (``sequence_edit``), all ``@personal_data(False)``: the
  Sequences section of Settings (FR-4, Story 11.7), linked from the Settings page; see
  ``nextron.web.sequences_page``. The edit POST runs ``sequence_editor`` (``SequenceEditor``, the
  ``setup`` service) in its own unit of work, then renders the panel again in a read-only one; a
  refused save (``SequenceRefused``) answers 200 with the form re-rendered as submitted. An
  unknown Sequence (a text that isn't a ULID included) answers 404 with the plain text "That
  Sequence doesn't exist.", which never echoes the id.
- ``GET /campaigns`` (``campaigns_list``), ``GET /campaigns/{campaign_id}``
  (``campaign_editor``), and ``POST /campaigns/create``, ``/campaigns/{campaign_id}/edit``,
  ``/campaigns/{campaign_id}/variants/add``, ``/campaigns/{campaign_id}/variants/{variant_id}/
  edit`` and ``.../retire``, all ``@personal_data(False)``: the Campaigns page (FR-3, Story
  11.8), linked from the Settings page; see ``nextron.web.campaign_editor_page``. Each POST runs
  its ``campaigns`` (``Campaigns``) use case in its own unit of work, then renders the panel
  again in a read-only one; a refusal (``CampaignRefused``) answers 200 with the form
  re-rendered as submitted, a stale one with the current stored Campaign. An unknown Campaign (a
  text that isn't a ULID included) answers 404 with the plain text "That Campaign doesn't
  exist.", which never echoes the id.
- ``GET /ai`` (``ai_content_page``) and ``POST /ai/check`` (``ai_content_check``), both
  ``@personal_data(False)``: the AI content view (Story 4.4), linked from the Settings page; see
  ``nextron.web.ai_content_page``. The GET reads ``queries.playbook.route_tuples()`` and
  ``playbook_status()``. The POST's only field is ``csrf_token`` (anything else is
  ``MalformedForm``, 400); it runs ``ai_content`` (the loader ``nextron.main`` binds: the
  verified prompt files, each route's default and the Sales Playbook read) outside any unit of
  work, then ``playbook.sync_prompts(..., trigger=SyncTrigger.OPERATOR_CHECK)`` (``Playbook``)
  in its own unit of work, then renders the panel again in a read-only one, with the check's
  notice and, when the Playbook read failed, its alert. ``InvalidPrompts`` answers 200 with the
  alert "Nothing was stored: <message>". After a sync it also runs ``test_runs.queue_runs()``
  (``AiTestRuns``, Story 4.5) in a unit of work of its own, so each new tuple gets its test
  run, and the notice adds "Queued <n> test run(s)." when it queued any.
- ``POST /ai/rollback`` (``ai_content_rollback``, ``@personal_data(False)``, Story 4.5): its
  only fields are ``csrf_token`` and one ``tuple_id`` that is a ULID (anything else is
  ``MalformedForm``, 400). It runs ``playbook.activate(tuple_id,
  trigger=ActivationTrigger.ROLLBACK)`` in its own unit of work, then renders the panel again in
  a read-only one with the notice "Rolled back <route> to tuple <id>." ("<route> already runs
  tuple <id>." when it already served); a refusal (``ActivationRefused``) answers 200 with the
  panel and an alert of its text, and nothing changed.
- ``GET /quotas`` (``quotas_page``, ``@personal_data(False)``): the Quotas and cost page
  (UX-DR28), rendered from ``budget.status()`` (the ``ops`` service's ``Budget``) through
  ``nextron.web.quotas_page``, and linked from the Settings page; see that module.
- ``GET /health/panel`` (``health_page``, the navigation's Health section), ``GET
  /health/panel/live`` (``health_panel``, the panel alone, which HTMX re-fetches every 30 s) and
  ``POST /health/jobs/dismiss`` (``health_job_dismiss``), all ``@personal_data(True)``: the
  health panel (FR-58, UX-DR29), rendered from ``health.report()`` (the ``ops`` service's
  ``EngineHealth``) through ``nextron.web.health_page``; see that module. The POST's fields,
  apart from ``csrf_token``, must be exactly one ``job`` holding a ULID (anything else is
  ``MalformedForm``, 400); it dismisses the dead job through ``queue`` (``JobQueue.dismiss``) in
  its own unit of work, then renders the panel again in a read-only one. (``GET /health`` keeps
  its JSON.)
- ``POST /health/backup`` (``health_backup_now``, ``@personal_data(True)``): the health panel's
  "Back up now" (Story 1.16). Its only field is ``csrf_token`` (anything else is
  ``MalformedForm``, 400). It queues a manual backup through ``backups``
  (``Backups.request_backup``, the ``backup_db`` job) in its own unit of work, then renders the
  panel again in a read-only one, with "Backup queued; its result shows here when it
  finishes." when the job was created, else "A backup is already queued or running.". The copy
  is taken by the job runner's bulk lane, never in the request.
- ``POST /health/restore-test`` (``health_restore_test_now``, ``@personal_data(True)``): the
  health panel's "Run restore test now" (Story 1.18). Its only field is ``csrf_token``
  (anything else is ``MalformedForm``, 400). It queues a manual restore test through
  ``restore_tests`` (``RestoreTests.request_restore_test``, the ``restore_test`` job) in its own
  unit of work, then renders the panel again in a read-only one, with "Restore test queued; its
  result shows here when it finishes." when the job was created, else "A restore test is
  already queued or running.". The test runs on the job runner's bulk lane, never in the
  request.
- ``GET /leads/import`` (``lead_import_page``, ``@personal_data(False)``: Segment names only)
  and ``POST /leads/import`` (``lead_import``, ``@personal_data(True)``): the CSV Lead import
  (Story 2.3), linked from the home page; see ``nextron.web.lead_import_page``. The POST reads
  its ``multipart/form-data`` body through ``nextron.web.forms.upload_form``, runs
  ``lead_imports.run`` (``LeadImports``) in its own unit of work, then renders the import block
  again, with the Segment choices read in a read-only one: a refusal by its field, the summary
  above a fresh form, or a stopped import's summary above the form with the origin and default
  kept, always 200.
- ``GET /leads/discovery`` (``discovery_page``) and ``POST /leads/discovery/places``
  (``discovery_places``), both ``@personal_data(False)`` and registered before
  ``/leads/{lead_id}``: the Discovery page (Story 8.3), linked from the Leads list; see
  ``nextron.web.discovery_page``. The POST runs ``place_discovery.request_run`` in its own unit
  of work, then renders the panel again in a read-only one with its notice, always 200.
- ``GET /signals`` (``signal_inbox_page``), ``POST /signals`` (``signal_inbox_submit``) and
  ``POST /signals/{item_id}/reprocess`` (``signal_inbox_reprocess``), all
  ``@personal_data(True)``: the Signal Inbox (Story 2.16, the nav's "Signal Inbox"); see
  ``nextron.web.signal_inbox_page``. The submit POST checks the fieldsets
  (``check_items``) and runs ``signal_inbox.submit`` in its own unit of work; the reprocess POST
  runs ``signal_inbox.reprocess`` (an ``item_id`` that isn't a ULID is a malformed form). Both
  answer 200 with the panel, read in a read-only unit of work.
- ``GET /inboxes`` (``inboxes_page``) and ``POST /inboxes`` (``inbox_register``), both
  ``@personal_data(True)``: the Sending Inboxes panel (Story 4.7, FR-32, UX-DR26), linked from
  the Settings page; see ``nextron.web.inboxes_page``. The GET lists
  ``queries.sending.inboxes()``. The POST's fields, apart from ``csrf_token``, must be exactly
  one ``address`` and one ``label`` (anything else is ``MalformedForm``, 400); it registers the
  inbox through ``sending`` (``Sending.register_inbox``) in its own unit of work, then renders
  the panel again in a read-only one, with "Inbox registered." or the ``InboxRefused`` message
  and the typed values. The page also includes the Sending Domain authentication section.
- ``POST /inboxes/warmup`` (``inbox_warmup``, ``@personal_data(True)``): one inbox's warm-up
  form (Story 6.4, FR-33, FR-34). Its fields, apart from ``csrf_token``, must be exactly one
  ``inbox_id`` (a ULID) and one each of ``warmup_started_on``, ``warmup_method``,
  ``warm_level``, ``warmup_tool_daily``, ``ramp_step`` and ``ramp_target`` (anything else is
  ``MalformedForm``, 400); it records them through ``Sending.record_warmup`` in its own unit of
  work, then renders the panel again in a read-only one, with "Warm-up saved." or the
  ``WarmupRefused`` message and that inbox's typed values. The panel shows each inbox's
  warm-up facts and caps from ``Sending.inbox_caps()``.
- ``GET /inboxes/auth`` (``inbox_auth_panel``) and ``POST /inboxes/auth/recheck``
  (``inbox_auth_recheck``), both ``@personal_data(False)`` (domain names and check results
  only): the Sending Domain authentication section (Story 6.5, FR-64), which HTMX re-fetches
  every 30 s; see ``nextron.web.inboxes_page``. The GET renders ``domain_checks.auths()``
  (``DomainChecks``). The POST's fields, apart from ``csrf_token``, must be exactly one
  ``sending_domain_id``, a ULID (anything else is ``MalformedForm``, 400); it runs
  ``DomainChecks.recheck`` in its own unit of work, then renders the section again in a
  read-only one, with "Recheck queued for <domain>; the result appears here when it lands." or
  "That Sending Domain has no inboxes to check.". The check itself runs as a ``check_domains``
  job on the interactive lane, never in the request.
- ``GET /suppression`` (``suppression_page``), ``POST /suppression/add`` (``suppression_add``),
  ``POST /suppression/import`` (``suppression_import``), ``POST /suppression/search``
  (``suppression_search``) and ``POST /suppression/reverse`` (``suppression_reverse``), all
  ``@personal_data(True)``: the Suppression List panel (Story 3.5, FR-50, FR-41, UX-DR25),
  linked from the Settings page; see ``nextron.web.suppression_page``. The GET reads its query
  string through ``suppression_page.list_filters`` (a bad one is ``MalformedForm``, 400), then
  ``queries.compliance.entry_list``. Each POST takes its form (anything else is
  ``MalformedForm``, 400) and runs one use case of ``compliance`` (``Compliance``) in its own
  unit of work: ``add_value``, ``import_list`` (the upload read through
  ``nextron.web.forms.upload_form``; a body over the limit shows "The file is larger than 5 MB")
  or ``reverse_entry``; the search runs no use case: it hashes the value
  (``suppression_list.search_key``) and reads ``queries.compliance.active_entry``. Then it
  renders the panel again in a read-only one, at page 1 with no filter, with the notice: the
  ``SuppressionRefused`` message, or what was done ("Added to the Suppression List" with the
  written entries' rows, read through ``queries.compliance.entries``; "Already on the list
  (...)"; the import's counts and invalid lines; "On the list (...)" with the matching entry's
  row, or "Not on the list"; "Opt-out reversed. ..." with the rows of the same opt-out's other
  ``active`` entries, each with its Reverse form). Always 200, and nothing typed is rendered
  back.
- ``GET /leads/pipeline`` (``pipeline_board``, ``@personal_data(True)``, Story 9.7,
  registered before ``/leads/{lead_id}``): the Pipeline board; see ``nextron.web.pipeline_page``.
  It reads its query string through ``pipeline_page.board_filters`` against
  ``queries.setup.segments`` and the stored Markets (``lead_markets``; a bad parameter is
  ``MalformedForm``, 400), then ``queries.pipeline.board``; a start date after the end date
  answers 400 with the page, its alert and no groups.
- ``GET /reports/calls`` (``calls_report_page``, ``@personal_data(False)``, Story 9.6): the
  Calls report; see ``nextron.web.calls_report_page``. It reads
  ``nextron.app.analytics.offer.offer_validation()`` and writes nothing. The Pipeline board links
  to it; it isn't a navigation section.
- ``GET /leads`` (``pipeline_page``, the navigation's Leads/Pipeline section) and ``GET
  /leads/{lead_id}`` (``lead_page``, registered after ``/leads/import``), both
  ``@personal_data(True)``: the Leads list and the Lead page (Story 2.4); see
  ``nextron.web.leads_page``. The list reads its query string through
  ``leads_page.list_filters`` (a bad one is ``MalformedForm``, 400), then
  ``queries.discovery.lead_list`` and ``queries.setup.segments``. The Lead page reads
  ``queries.discovery.survivor_of``: ``None`` (not a ULID, unknown, a broken or over-long merge
  chain) answers 404 with ``lead_missing.html``, a merged Lead a 303 ``RedirectResponse`` to
  ``/leads/<survivor id>``; otherwise it renders from ``queries.pipeline.stage``,
  ``queries.setup.segments``, ``queries.research.contacts``,
  ``queries.research.lead_provenance`` and ``lead_signals.view`` (Story 2.9: the Signals, the
  Evidence and the headquarters zone), ``queries.research.no_decision_maker`` (Story 2.10),
  ``queries.research.legal_form`` (Story 2.12: a GB Lead's legal-form section),
  each Contact's live Eligibility from ``compliance.evaluate_eligibility`` (Story 3.6), evaluated
  on every render and never stored, the stored Markets (``lead_markets``:
  ``queries.setup.markets``, or none when one no longer decodes: ``InvalidMarkets``), which name
  each Contact's Market (Story 3.7), and ``queries.compliance.completed_erasures``
  (``lead_erasures``, Story 3.13: an erased Contact's tombstone line names its request) and
  ``queries.compliance.receipts`` (``lead_receipts``, Story 3.14: an erased Contact's erasure
  receipts follow its tombstone line), and
  ``queries.scoring.stored`` (Story 2.15: the Score panel, labelled from the Segment version
  the score used, ``scored_segment``, Story 11.5), with one "Export data (JSON)" form
  per Contact (``export_page.lead_forms``, Story 3.15), and the Pipeline panel (Story 9.1,
  ``lead_pipeline.panel_context``) from ``queries.pipeline.lead_pipeline`` and, for its opt-out
  line, the suppression entry its latest close names (``queries.compliance.entries``). The list
  also passes its ``sort`` to ``lead_list``. Under the Lead's name, the tab bar (Story 9.8,
  ``history_page.lead_tabs``) links Overview and History.
- ``GET /leads/ready`` (``ready_page``) and ``POST /leads/ready/pick`` (``ready_pick``), Story
  4.10, both ``@personal_data(True)`` and registered before ``/leads/{lead_id}``: the "Ready to
  draft" page (``leads_ready.html``; see ``nextron.web.ready_page``), from ``selection``
  (``Selection.today()`` and ``ready_list()``, read in the GET's read-only unit of work). The
  form posts the checked ``lead`` ULIDs in page order (``ready_page.submitted_picks``: 1 to 200,
  each a ULID and each once, else ``MalformedForm``, 400). The POST runs ``selection.pick`` in its
  own unit of work, then answers 200 with the list alone (``leads_ready_list.html``, read in a
  read-only unit of work) and one result row per picked Lead: "Enrolled" or "Refused: <plain
  reason>". The Leads list links to the page; the navigation is unchanged.
- ``GET /leads/{lead_id}/history`` (``lead_history_page``, ``@personal_data(True)``, Story 9.8):
  a Lead's History tab; see ``nextron.web.history_page``. It reads its query string through
  ``history_page.history_query`` (a bad one is ``MalformedForm``, 400), then, like the Lead
  page, ``queries.discovery.survivor_of`` (404 ``lead_missing.html`` never echoing the id; a
  merged Lead a 303 to ``/leads/<survivor id>/history``, keeping the query), then
  ``lead_history.view`` (``nextron.app.analytics.history.LeadHistory``), which writes nothing.
- ``POST /leads/{lead_id}/rescore`` (``lead_rescore``, Story 2.15, ``@personal_data(True)``):
  the Score panel's "Re-score". The form holds nothing but the CSRF token
  (``leads_page.submitted_rescore``). It runs ``lead_scores.request_rescore(lead_id)`` in its
  own unit of work, which queues ``score_lead`` on the interactive lane, then answers 200 with
  the Score panel (``lead_score.html``, read in a read-only unit of work) and the notice
  "Re-score queued."; a merged or closed Lead queues nothing and the panel carries the refusal
  notice (``leads_page.rescore_refused``). An unknown Lead (a text that isn't a ULID
  included) answers 404 with the plain text "That Lead doesn't exist.", which never echoes the
  id. "Re-score" keeps the Lead's stored Segment version (Story 11.5).
- ``POST /leads/{lead_id}/stage`` (``lead_stage``), ``POST /leads/{lead_id}/call``
  (``lead_call``) and ``POST /leads/{lead_id}/deal`` (``lead_deal``), Story 9.1, all
  ``@personal_data(True)``: the Pipeline panel's forms; see ``nextron.web.lead_pipeline``. Each
  parses its form first (a missing, unknown or repeated field, or an unknown ``move``, is
  ``MalformedForm``, 400), then answers 404 with the plain text "That Lead doesn't exist." (never
  echoing the id) for a Lead that isn't a live survivor (``queries.discovery.survivor_of``). The
  stage form runs ``pipeline.operator_move``; the call form first reads the Contact in a
  read-only unit of work (``lead_pipeline.call_details``: its zone when none is typed), then runs
  ``pipeline.operator_book_call``; the deal form parses the value and notes
  (``manual_stages.parse_deal_value`` and ``clean_notes``), then runs
  ``pipeline.operator_save_deal``. Each runs in its own unit of work; a ``StageRefused`` (nothing
  written) becomes the panel's alert, and a refused call or deal form renders back what was
  typed. Then it answers 200 with the panel
  (``lead_pipeline.html``, read in a read-only unit of work), so HTMX swaps it in.
- ``POST /leads/{lead_id}/outcome`` (``lead_outcome``, Story 9.4, ``@personal_data(True)``):
  the panel's Call Outcome form (``lead_pipeline.submitted_outcome``: malformed is 400; then the
  same 404 for a Lead that isn't a live survivor). It runs
  ``pipeline.operator_record_call_outcome`` with the form in its own unit of work, which checks
  the booking first: one that isn't this Lead's, is unknown or is superseded
  (``NotThisLeadsBooking``) is ``MalformedForm`` (400) whatever the fields hold. Then
  ``call_outcomes.check_outcome``'s ``OutcomeRefused`` (nothing written) renders the form again
  with what was typed, the posted ``booking`` id and the error next to its field. It answers 200
  with the panel; a saved outcome also sends ``HX-Trigger: call-outcome-saved``, on which the
  layout's notification area loads again, so the due line clears.
- ``POST /leads/{lead_id}/rescore-current`` (``lead_rescore_current``, Story 11.5,
  ``@personal_data(True)``): the Score panel's "Re-score with the current definition", shaped
  like ``lead_rescore`` but running ``lead_scores.request_rescore_current(lead_id)``, whose job
  applies the Segment's current version; its notice is "Re-score with the current definition
  queued.".
- ``POST /leads/{lead_id}/primary`` (``lead_make_primary``, Story 2.10,
  ``@personal_data(True)``): the Contacts panel's "Make primary". The form is exactly
  ``contact_id`` (``leads_page.submitted_contact``). It runs
  ``primary_contacts.make_primary(lead_id, contact_id)`` in its own unit of work, then answers
  200 with the Contacts panel (``lead_contacts.html``, read in a read-only unit of work, each
  Contact's live Eligibility evaluated and the Markets, erasures and receipts read there too)
  and the notice "Primary Contact set.", or, when it was refused (nothing written), the refusal's
  notice. An unknown Lead (a text that isn't a ULID included) answers 404 with the plain text
  "That Lead doesn't exist.", which never echoes the id.
- ``GET /leads/{lead_id}/contacts/{contact_id}/erase`` (``contact_erase_page``) and ``POST`` to
  the same path (``contact_erase``), both ``@personal_data(True)`` and registered before
  ``/leads/{lead_id}``: the erase confirmation step (Story 3.13, FR-52); see
  ``nextron.web.erase_page``. A Contact that isn't on the Lead's survivor
  (``queries.discovery.survivor_of``) answers 404 with ``lead_missing.html``. The POST's fields,
  apart from ``csrf_token``, must be exactly one ``request_ref`` (anything else is
  ``MalformedForm``, 400); it checks the reference, runs ``erasures.erase_on_request``
  (``Erasures``, the ``compliance`` service) in its own unit of work, then renders the panel
  again in a read-only one, always 200: the refusal, the outcome (linked to its receipt on the
  Lead page, Story 3.14), or, for any exception, the failure (logged as
  ``contact_erasure_failed`` with ``contact_id`` and ``error_type``). For an erased Contact both
  show its erasure receipts (``queries.compliance.receipts``).
- ``GET /leads/{lead_id}/contacts/{contact_id}/permissions`` (``contact_permissions_page``),
  ``POST`` to the same path (``contact_permission_record``) and ``POST
  .../permissions/{permission_id}/inbound``, ``/revoke`` and ``/do-not-contact``
  (``contact_permission_inbound``, ``contact_permission_revoke``,
  ``contact_permission_do_not_contact``), every one ``@personal_data(True)`` and registered before
  ``/leads/{lead_id}``: a Contact's Channel Permissions for WhatsApp and Instagram (Story 10.7,
  FR-24, FR-47); see ``nextron.web.permissions_page``. A Contact that isn't on the Lead's
  survivor answers 404 with ``lead_missing.html``. Each POST checks its fields
  (``permissions_page.submitted``: anything else is ``MalformedForm``, 400), does nothing for an
  erased Contact, otherwise runs its ``ChannelPermissions`` use case in its own unit of work
  (a ``PermissionRefused`` stores nothing), then renders the panel in a read-only one, 200.
- ``POST /contacts/{contact_id}/export`` (``contact_export``) and ``GET /exports/{ticket}``
  (``contact_export_download``), both ``@personal_data(True)``: the subject access export
  (Story 3.15, FR-52); see ``nextron.web.export_page``. The POST's fields, apart from
  ``csrf_token``, must be exactly one ``request_ref`` (anything else is ``MalformedForm``,
  400). In a read-only unit of work it reads the Contact (an unknown one, a text that isn't
  a ULID included, answers a plain 404 "Contact not found", which never echoes the id) and
  checks the reference with Story 3.13's ``check_request_ref`` (the Contact's name, or
  ``None`` once it is erased): a refusal answers 200 with the form alone and its message,
  never the typed reference, and nothing runs. Then it runs ``erasures.export_on_request``
  (``Erasures``, the ``compliance`` service) in its own unit of work, which opens and
  completes a request of kind ``export`` and builds the file; it holds the file in
  ``ExportTickets`` (in memory only, made here, bound to the session's CSRF token, one fetch
  within 60 s, at most 16) and answers 204 with ``HX-Redirect: /exports/<ticket>``. A
  ``LookupError`` there (the Contact gone since the check) is answered 404 only when a
  read-only re-read finds no such Contact, so a ``KeyError`` from a bug stays a 500. The GET
  takes the ticket for the session (``ExportTickets.take``) and answers the attachment
  (``export_page.download_response``), or a plain 404 "Export expired: export again from
  the Lead page". Like ``GET /enter``, it changes in-memory state only. Nothing is logged,
  and the file is never written to disk.
- ``POST /leads/{lead_id}/find-email`` (``lead_find_email``, Story 2.13,
  ``@personal_data(True)``): the Contacts panel's "Find email", on a Contact without a usable
  address. The form is exactly ``contact_id``. It runs ``email_finding.queue_search(lead_id,
  contact_id)`` in its own unit of work and answers 200 with the Contacts panel and a notice:
  "Looking for an email address. Reload the page in a minute." when the search was queued,
  else "This Lead was merged into another Lead.", "That Contact isn't on this Lead." or "That
  Contact already has an address." (nothing written). An unknown Lead answers 404 with "That
  Lead doesn't exist.".

``Guard`` (``nextron.web.guard``) checks ``Host``, the session, and ``Origin`` and
``Sec-Fetch-Site`` on unsafe methods in front of every route, and stamps the security headers on
every response. Sessions live in a ``SessionStore`` made here, so a restart drops them all. A
``SecurityCheckFailed`` from ``require_csrf`` is answered with 403 by
``nextron.web.csrf.security_check_failed``, and a ``MalformedForm`` with 400 by
``nextron.web.forms.malformed_form``. The interactive API docs and the OpenAPI schema are off,
because the dashboard serves no third-party scripts.

Pages render with ``app.state.templates`` (``template_environment()``: autoescaping on, plus the
``nextron.web.templating`` helpers). ``page(request, template, **context)`` passes ``context``,
``nav`` and the session's ``csrf_token``, which ``base.html`` puts in ``<body hx-headers>`` for
HTMX and ``csrf_field()`` puts in forms, and answers with ``Cache-Control: no-store``, whatever
the route declared, because the page carries that token.

**Error notices.** ``base.html``'s ``<meta name="htmx-config">`` sets HTMX's
``responseHandling``: a 204 swaps nothing, a 2xx or 3xx swaps as usual, and any 4xx or 5xx body
is put, as text (``swapOverride: textContent``, never parsed as HTML), in
``#request-error`` inside ``section#notifications``. So a refused action (a 403 CSRF refusal,
a 400 malformed form, a 500) shows its plain-text answer, and a request without a session gets
the guard's plain-text 401 (``nextron.web.guard``). A request that gets no answer at all (the
engine is down) shows nothing, and a notice stays until the page reloads.

The base layout's navigation lists, in ``NAV_SECTIONS`` order, each section whose page route
(a GET route with that name) exists; a later story adds the route and its link appears.

``create_app(..., lifespan=None)`` hands ``lifespan`` to ``FastAPI``; ``Guard`` passes lifespan
scopes through untouched, so the job runner, the scheduler and the heartbeat writer that
``nextron.main`` wires there start and stop with the server.
"""

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader
from starlette.routing import BaseRoute

from nextron.app.analytics.history import LeadHistory
from nextron.app.analytics.offer import offer_validation
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.discovery.api import ImportRequest, ImportStopped, LeadImports
from nextron.app.discovery.place_ingest import (
    PlaceDiscovery,
    PlacesRunRefused,
    configured_locations,
)
from nextron.app.discovery.signal_inbox import SignalInbox
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Backups, Budget, DashboardEntry, EngineHealth, RestoreTests
from nextron.app.pipeline.api import NotThisLeadsBooking, Pipeline
from nextron.app.pipeline.assist_accounts import AssistAccounts
from nextron.app.pipeline.channel_permissions import ChannelPermissions
from nextron.app.playbook.api import (
    ActivationRefused,
    ActivationTrigger,
    AiTestRuns,
    CompanyFacts,
    Playbook,
)
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import scoring as scoring_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.scoring import StoredScore
from nextron.app.research.extraction import LeadSignals
from nextron.app.research.finding import EmailFinding, SearchQueued
from nextron.app.research.people import PrimaryContacts
from nextron.app.scoring.lead_scores import LeadScores, RescoreRequest
from nextron.app.scoring.selection import Selection
from nextron.app.sending.api import Sending
from nextron.app.sending.checks import DomainChecks
from nextron.app.setup.api import (
    Campaigns,
    Credentials,
    CredentialStoreFailed,
    GoogleAccess,
    GrantStatus,
    Setup,
)
from nextron.app.setup.segment_editing import SegmentEditor
from nextron.app.setup.sequence_editing import STALE as SEQUENCE_STALE
from nextron.app.setup.sequence_editing import SequenceEditor
from nextron.domain.campaign import STALE_CAMPAIGN, CampaignRefused, InvalidCampaigns
from nextron.domain.channel_permission import PermissionRefused
from nextron.domain.company_facts import InvalidFact
from nextron.domain.contacts import PrimaryRefusal, PrimaryRefused
from nextron.domain.data_subject_request import check_request_ref
from nextron.domain.credentials import InvalidCredentialValue
from nextron.domain.google_oauth import ClientNotImported, InvalidClientFile
from nextron.domain.ids import is_ulid
from nextron.domain.lead_import import ImportRefused
from nextron.domain.linkedin_caps import LABEL, OWNER_NAME, OWNER_ROLE, STATUS, AccountRefused
from nextron.domain.call_outcomes import OutcomeForm, OutcomeRefused
from nextron.domain.manual_stages import StageRefused, clean_notes, parse_deal_value
from nextron.domain.markets import InvalidMarkets
from nextron.domain.offers import InvalidOffers
from nextron.domain.open_code import CodeCheck
from nextron.domain.playbook import SyncTrigger
from nextron.domain.prompts import InvalidPrompts
from nextron.domain.recovery import RECOVERY_BANNER
from nextron.domain.render import footer_complete
from nextron.domain.segments import InvalidSegments, SegmentDef, SegmentRefused
from nextron.domain.sending_inbox import InboxRefused, WarmupRefused
from nextron.domain.sequences import InvalidSequences, SequenceRefused
from nextron.domain.signal_inbox_item import ItemsRefused, check_items
from nextron.domain.settings_schema import InvalidSettings
from nextron.domain.suppression_list import (
    ADDED,
    FILE_TOO_LARGE,
    NOT_ON_THE_LIST,
    SuppressionRefused,
    already_on_the_list,
    import_message,
    invalid_lines_message,
    on_the_list,
    reversed_message,
    search_key,
)
from nextron.domain.unsub_token import UnsubKeyInUse, UnsubKeysHeldInRecovery
from nextron.web import ai_content_page as ai_rows
from nextron.web import assist_accounts_page as assist_rows
from nextron.web import calls_report_page as report_rows
from nextron.web import campaign_editor_page as campaign_editor_rows
from nextron.web import campaigns_page as campaign_rows
from nextron.web import credentials_page as credential_rows
from nextron.web import discovery_page as discovery_rows
from nextron.web import erase_page as erase_rows
from nextron.web import permissions_page as permission_rows
from nextron.web import export_page as export_rows
from nextron.web import facts_page as fact_rows
from nextron.web import google_page as google_rows
from nextron.web import health_page as health_rows
from nextron.web import history_page as history_rows
from nextron.web import inboxes_page as inbox_rows
from nextron.web import lead_import_page as import_rows
from nextron.web import lead_pipeline as pipeline_rows
from nextron.web import leads_page as lead_rows
from nextron.web import pipeline_page as board_rows
from nextron.web import quotas_page as quota_rows
from nextron.web import ready_page as ready_rows
from nextron.web import segments_page as segment_rows
from nextron.web import sequences_page as sequence_rows
from nextron.web import signal_inbox_page as inbox_page_rows
from nextron.web import suppression_page as suppression_rows
from nextron.web import templating
from nextron.web.credentials_page import Notice
from nextron.web.csrf import CSRF_SCOPE_KEY, SecurityCheckFailed, security_check_failed
from nextron.web.forms import (
    REPEATED_FIELD,
    WRONG_FIELDS,
    FormFields,
    MalformedForm,
    UploadForm,
    form_fields,
    malformed_form,
    upload_form,
)
from nextron.web.guard import Guard
from nextron.web.routing import (
    Units,
    dashboard_route_class,
    personal_data,
    require_declared,
    require_route_rules,
)
from nextron.web.sessions import SESSION_COOKIE, SessionStore
from nextron.web.settings_page import (
    FORM_TEMPLATE,
    PAGE_TEMPLATE,
    form_context,
    shown_values,
    stored_problems,
    submitted_texts,
)

WEB_DIR: Final = Path(__file__).resolve().parent
TEMPLATES_DIR: Final = WEB_DIR / "templates"
STATIC_DIR: Final = WEB_DIR / "static"
STATIC_TYPES: Final = {
    "htmx-2.0.11.min.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
# (route name, label), in navigation order
NAV_SECTIONS: Final = (
    ("queue_page", "Queue"),
    ("signal_inbox_page", "Signal Inbox"),
    ("pipeline_page", "Leads/Pipeline"),
    ("funnel_page", "Funnel"),
    ("settings_page", "Settings"),
    ("health_page", "Health"),
)
NO_STORE: Final = {"Cache-Control": "no-store"}
BROWSER_STARTED: Final = "none"  # Sec-Fetch-Site of a navigation the user started
CROSS_SITE: Final = "cross_site"
NOTIFICATIONS_TEMPLATE: Final = "notifications.html"
AREA_LIMIT: Final = 5  # the newest notifications the area lists
NOTIFICATION_FIELD: Final = "notification"
CSRF_FIELD_NAME: Final = "csrf_token"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"
# What a stored row the Campaigns panel reads raises when it no longer decodes.
STORED_ROW_REFUSED: Final = (
    InvalidCampaigns,
    InvalidOffers,
    InvalidSequences,
    InvalidSegments,
    InvalidMarkets,
)

# An async context manager factory taking the app: what FastAPI(lifespan=...) accepts.
type Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NavLink:
    label: str
    href: str


@dataclass(frozen=True, slots=True)
class NotificationItem:
    """One notification as the area shows it: its id, its text and its time in UTC."""

    id: str
    text: str
    time: str


def scored_segment(segment_id: str, score: StoredScore | None) -> SegmentDef | None:
    """The definition of the Segment version ``score`` used, when that version is stored and
    is not the current one (Story 11.5); else ``None``, so the Score panel uses the current
    definition. Runs inside the caller's read unit."""
    if score is None or score.segment_version is None:
        return None
    current = setup_queries.segment_by_id(segment_id)
    if current is not None and current.version == score.segment_version:
        return None
    stored = setup_queries.segment_version(segment_id, score.segment_version)
    return None if stored is None else stored.definition


def health_count_text(errors: int) -> str | None:
    """The area's engine health line for ``errors`` errors, or ``None`` when there are none."""
    if errors <= 0:
        return None
    if errors == 1:
        return "Engine health: 1 error needs attention"
    return f"Engine health: {errors:,} errors need attention"


def shortfall_text(shortfall: int) -> str | None:
    """The area's signal shortfall line (Story 4.10), or ``None`` when there is none."""
    if shortfall <= 0:
        return None
    return f"signal shortfall: {shortfall:,}"


def area_context(
    found: notify_queries.OpenNotifications,
    *,
    errors: int = 0,
    recovery: bool = False,
    shortfall: int = 0,
    due: Iterable[pipeline_rows.DueItem] = (),
) -> dict[str, object]:
    """What ``notifications.html`` renders: the recovery banner while ``recovery`` mode is on,
    the engine health line, the signal shortfall line (today's remaining capacity), the Call
    Outcome due lines (Story 9.4), the count, the listed items and how many more."""
    items = [
        NotificationItem(row.id, row.text, f"{row.created_at:{TIME_FORMAT}}")
        for row in found.newest
    ]
    return {
        "recovery": recovery,
        "RECOVERY_BANNER": RECOVERY_BANNER,
        "health_text": health_count_text(errors),
        "shortfall_text": shortfall_text(shortfall),
        "due_items": list(due),
        "count": found.count,
        "items": items,
        "more": max(found.count - len(items), 0),
    }


def submitted_notification(fields: FormFields) -> str:
    """The one ``notification`` ULID a Dismiss form posts; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD_NAME} != {NOTIFICATION_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    values = fields[NOTIFICATION_FIELD]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    if not is_ulid(values[0]):
        raise MalformedForm(WRONG_FIELDS)
    return values[0]


def template_environment() -> Environment:
    """The Jinja2 environment of the dashboard's templates: autoescaping on, plus the
    ``plain``, ``safe_link`` and ``csrf_field`` helpers (``nextron.web.templating``)."""
    environment = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    return templating.install(environment)


def _route_path(routes: list[BaseRoute], name: str) -> str | None:
    for route in routes:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "name", None) == name and "GET" in methods:
            return str(getattr(route, "path"))
    return None


def nav_links(app: FastAPI) -> list[NavLink]:
    """The ``NAV_SECTIONS`` whose page route exists in ``app``, in order."""
    links = []
    for name, label in NAV_SECTIONS:
        path = _route_path(app.routes, name)
        if path is not None:
            links.append(NavLink(label, path))
    return links


def create_app(
    *,
    env: str,
    version: str,
    port: int,
    entry: DashboardEntry,
    setup: Setup,
    campaigns: Campaigns,
    assist_accounts: AssistAccounts,
    facts: CompanyFacts,
    playbook: Playbook,
    ai_content: ai_rows.ContentLoader,
    test_runs: AiTestRuns,
    credentials: Credentials,
    google: GoogleAccess,
    notify: Notify,
    budget: Budget,
    health: EngineHealth,
    queue: JobQueue,
    backups: Backups,
    restore_tests: RestoreTests,
    lead_imports: LeadImports,
    sending: Sending,
    lead_signals: LeadSignals,
    lead_history: LeadHistory,
    compliance: Compliance,
    primary_contacts: PrimaryContacts,
    pipeline: Pipeline,
    erasures: Erasures,
    channel_permissions: ChannelPermissions,
    domain_checks: DomainChecks,
    lead_scores: LeadScores,
    place_discovery: PlaceDiscovery,
    selection: Selection,
    signal_inbox: SignalInbox,
    segment_editor: SegmentEditor,
    sequence_editor: SequenceEditor,
    units: Units,
    email_finding: EmailFinding | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    lifespan: Lifespan | None = None,
) -> FastAPI:
    """Build the dashboard app for ``127.0.0.1:<port>``; its GET handlers read through
    ``units.open(read_only=True)`` and its POST handlers that write the database open
    ``units.open()``. ``setup`` stores the settings, ``campaigns`` runs the Settings page's
    Campaign controls (activate, pause, resume, save the LIA), ``assist_accounts`` its
    LinkedIn account panel's add and save (Story 10.2), ``facts`` the Approved Company Facts,
    ``playbook`` the AI content view's "Check for changes" with the inputs ``ai_content`` loads
    and its rollback, ``test_runs`` queues the test runs of new tuples after a check,
    ``credentials`` the external credentials and ``google`` the Google client and grants (both
    in Credential Manager, not the database);
    ``notify`` dismisses notifications; ``budget`` gives the Quotas and cost page its figures;
    ``health`` gives the health panel its report and the notification area its error count;
    ``queue`` dismisses a dead job; ``backups`` queues the health panel's "Back up now" and
    ``restore_tests`` its "Run restore test now"; ``lead_imports`` runs the CSV Lead import;
    ``sending`` registers the Sending Inboxes panel's inboxes; ``lead_signals`` gives the Lead
    page its Signals, Evidence and headquarters zone, ``lead_history`` its History tab's
    timeline (Story 9.8), ``primary_contacts`` runs its "Make
    primary" and ``email_finding`` its "Find email" (Story 2.13; without one, "Find email"
    answers 503) and ``lead_scores`` its "Re-score" (Story 2.15) and both "Re-score with the
    current definition" controls (Story 11.5); ``segment_editor`` creates and edits Segments
    (Story 11.5); ``sequence_editor`` edits Sequences (Story 11.7); ``pipeline`` runs its
    Pipeline panel's moves, bookings and deal saves (Story 9.1); ``selection`` gives the "Ready to draft"
    page its list, capacity and picks and the notification area its signal shortfall (Story
    4.10); ``signal_inbox`` stores the Signal Inbox's pasted items and reprocesses a flagged one
    (Story 2.16); ``compliance`` runs the Suppression List panel's add, import and Reverse, and
    evaluates each Contact's live Eligibility on the Lead page and its Contacts panel (that only
    reads); ``erasures`` erases a Contact from the Lead page's erase step and answers its
    subject access export; ``channel_permissions`` runs a Contact's Channel Permission page
    (Story 10.7); ``domain_checks`` reads the Sending Domain authentication section and queues
    its Recheck; ``place_discovery`` queues the Discovery page's Places runs.
    ``monotonic`` is injectable so tests control the uptime and the export tickets' 60 s.
    ``lifespan`` goes to ``FastAPI(lifespan=...)``: ``nextron.main`` passes the one that starts
    and stops the job runner, the scheduler and the heartbeat writer (``engine_lifespan``).
    Raises ``RuntimeError`` when a route doesn't declare ``personal_data`` or breaks the route
    rules."""
    started = monotonic()
    engine_health = health  # the name health is the GET /health handler's below
    templates = template_environment()
    sessions = SessionStore()
    tickets = export_rows.ExportTickets(monotonic)  # built export files, in memory only
    assets = {name: (STATIC_DIR / name).read_bytes() for name in STATIC_TYPES}
    locked_page = templates.get_template("locked.html").render().encode("utf-8")
    app = FastAPI(
        title="Nextron Outreach Engine",
        version=version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.router.route_class = dashboard_route_class(units)
    app.add_exception_handler(SecurityCheckFailed, security_check_failed)
    app.add_exception_handler(MalformedForm, malformed_form)
    app.state.templates = templates

    def page(request: Request, template: str, **context: object) -> HTMLResponse:
        body = templates.get_template(template).render(
            **context, nav=nav_links(request.app), csrf_token=request.scope.get(CSRF_SCOPE_KEY)
        )
        return HTMLResponse(body, headers=NO_STORE)  # it carries the session's CSRF token

    def locked() -> HTMLResponse:
        return HTMLResponse(locked_page, status_code=401, headers=NO_STORE)

    def not_found_text(text: str) -> PlainTextResponse:
        return PlainTextResponse(text, status_code=404, headers=NO_STORE)

    @app.get("/enter", name="enter")
    @personal_data(False)
    def enter(request: Request) -> Response:
        fetch_sites = request.headers.getlist("sec-fetch-site")
        if fetch_sites and fetch_sites != [BROWSER_STARTED]:
            # Not a navigation the browser started: refused before the code is looked at.
            log.warning("dashboard_entry_refused", extra={"reason": CROSS_SITE})
            return locked()
        codes = request.query_params.getlist("t")
        # Not exactly one code: refused without touching the pending code.
        result = entry.consume_code(codes[0]) if len(codes) == 1 else CodeCheck.MISMATCH
        if result is not CodeCheck.ACCEPTED:
            log.warning("dashboard_entry_refused", extra={"reason": result})
            return locked()
        token = sessions.create()
        log.info("dashboard_session_opened")
        response = RedirectResponse("/", status_code=303, headers=NO_STORE)
        response.headers.append(
            "set-cookie", f"{SESSION_COOKIE}={token}; HttpOnly; Path=/; SameSite=Strict"
        )
        return response

    @app.get("/", name="home")
    @personal_data(False)
    def home(request: Request) -> HTMLResponse:
        return page(request, "home.html", away_until=setup.away_until())

    @app.get("/health", name="health")
    @personal_data(False)
    def health() -> dict[str, str | int]:
        return {"env": env, "version": version, "uptime_s": int(monotonic() - started)}

    @app.get("/static/{name}", name="static")
    @personal_data(False)
    def static(name: str) -> Response:
        content = assets.get(name)
        if content is None:
            return PlainTextResponse("Not found", status_code=404, headers=NO_STORE)
        return Response(content, media_type=STATIC_TYPES[name])

    def campaigns_context(
        *,
        outcome: campaign_rows.Outcome | None = None,
        refusal: campaign_rows.Refusal | None = None,
    ) -> dict[str, object]:
        return campaign_rows.panel_context(
            setup_queries.campaigns(),
            setup_queries.segments(),
            setup_queries.markets(),
            setup_queries.offers(),
            setup_queries.sequences(),
            outcome=outcome,
            refusal=refusal,
        )

    @app.get("/settings", name="settings_page")
    @personal_data(True)
    def settings_page(request: Request) -> HTMLResponse:
        stored = setup_queries.stored_texts()
        context = form_context(shown_values(stored), problems=stored_problems(stored))
        try:
            panel = campaigns_context()
        except STORED_ROW_REFUSED as refused:  # the settings stay reachable
            panel = campaign_rows.unavailable_context(refused.message)
        accounts = assist_rows.panel_context(pipeline_queries.accounts())
        return page(request, PAGE_TEMPLATE, **context, **panel, **accounts)

    @app.post("/settings", name="settings_save")
    @personal_data(True)
    def settings_save(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        texts = submitted_texts(fields)
        try:
            with units.open():
                stored = setup.save(texts)
        except InvalidSettings as refused:
            # 200, so HTMX swaps the form in; nothing was saved
            context = form_context(texts, problems=refused.problems, refused=True)
            return page(request, FORM_TEMPLATE, **context)
        return page(request, FORM_TEMPLATE, **form_context(shown_values(stored), saved=True))

    def campaigns_answer(
        request: Request,
        run: Callable[[], object],
        outcome: Callable[[object], campaign_rows.Outcome],
        refusal: Callable[[CampaignRefused], campaign_rows.Refusal],
    ) -> HTMLResponse:
        """Run a Campaign use case in its own unit of work, then render the panel alone in a
        read-only one: with the outcome's notice, or, refused, with the refusal (200, so HTMX
        swaps the panel in; nothing was changed)."""
        try:
            with units.open():
                result = run()
        except CampaignRefused as refused:
            with units.open(read_only=True):
                context = campaigns_context(refusal=refusal(refused))
                return page(request, campaign_rows.PANEL_TEMPLATE, **context)
        with units.open(read_only=True):
            context = campaigns_context(outcome=outcome(result))
            return page(request, campaign_rows.PANEL_TEMPLATE, **context)

    @app.post("/settings/campaigns/activate", name="campaign_activate")
    @personal_data(False)  # Campaigns and the LIA reference are configuration, not a person
    def campaign_activate(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        campaign_id, lia_ref, lia_date = campaign_rows.submitted_activate(fields)
        return campaigns_answer(
            request,
            lambda: campaigns.activate(campaign_id, lia_ref, lia_date),
            lambda _: campaign_rows.Outcome(campaign_rows.ACTIVATED, campaign_id),
            lambda refused: campaign_rows.Refusal(
                campaign_rows.ACTIVATE, campaign_id, refused.problems, lia_ref, lia_date
            ),
        )

    @app.post("/settings/campaigns/pause", name="campaign_pause")
    @personal_data(False)
    def campaign_pause(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        campaign_id = campaign_rows.submitted_campaign(fields)
        return campaigns_answer(
            request,
            lambda: campaigns.pause(campaign_id),
            lambda _: campaign_rows.Outcome(campaign_rows.PAUSED, campaign_id),
            lambda refused: campaign_rows.Refusal(
                campaign_rows.PAUSE, campaign_id, refused.problems
            ),
        )

    @app.post("/settings/campaigns/resume", name="campaign_resume")
    @personal_data(False)
    def campaign_resume(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        campaign_id = campaign_rows.submitted_campaign(fields)
        return campaigns_answer(
            request,
            lambda: campaigns.resume(campaign_id),
            lambda _: campaign_rows.Outcome(campaign_rows.RESUMED, campaign_id),
            lambda refused: campaign_rows.Refusal(
                campaign_rows.RESUME, campaign_id, refused.problems
            ),
        )

    @app.post("/settings/campaigns/lia", name="campaign_lia")
    @personal_data(False)
    def campaign_lia(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        campaign_id, lia_ref, lia_date = campaign_rows.submitted_lia(fields)
        return campaigns_answer(
            request,
            lambda: campaigns.record_lia(campaign_id, lia_ref, lia_date),
            lambda saved: campaign_rows.lia_outcome(saved, campaign_id),
            lambda refused: campaign_rows.Refusal(
                campaign_rows.SAVE_LIA, campaign_id, refused.problems, lia_ref, lia_date
            ),
        )

    def assist_answer(
        request: Request,
        action: str,
        submitted: Mapping[str, str],
        run: Callable[[], str],
    ) -> HTMLResponse:
        """Run a LinkedIn account use case in its own unit of work, then render the panel alone
        in a read-only one: with the outcome's notice, or, refused, with the refusal (200, so
        HTMX swaps the panel in; nothing was stored)."""
        account_id = submitted.get(assist_rows.ACCOUNT)
        try:
            with units.open():
                result = run()
        except AccountRefused as refused:
            refusal = assist_rows.Refusal(action, account_id, refused.problems, submitted)
            with units.open(read_only=True):
                context = assist_rows.panel_context(pipeline_queries.accounts(), refusal=refusal)
                return page(request, assist_rows.PANEL_TEMPLATE, **context)
        outcome = assist_rows.Outcome(action, result)
        with units.open(read_only=True):
            context = assist_rows.panel_context(pipeline_queries.accounts(), outcome=outcome)
            return page(request, assist_rows.PANEL_TEMPLATE, **context)

    @app.post("/settings/linkedin/add", name="assist_account_add")
    @personal_data(True)  # the owner's name is a Nextron staff member's
    def assist_account_add(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        submitted = assist_rows.submitted_add(fields)
        return assist_answer(
            request,
            assist_rows.ADD,
            submitted,
            lambda: assist_accounts.add_account(
                submitted[LABEL],
                submitted[OWNER_NAME],
                submitted[OWNER_ROLE],
                assist_rows.caps_of(submitted),
            ),
        )

    @app.post("/settings/linkedin/save", name="assist_account_save")
    @personal_data(True)
    def assist_account_save(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        submitted = assist_rows.submitted_save(fields)
        account_id = submitted[assist_rows.ACCOUNT]

        def save() -> str:
            assist_accounts.save_account(
                account_id,
                submitted[LABEL],
                submitted[OWNER_NAME],
                submitted[OWNER_ROLE],
                submitted[STATUS],
                assist_rows.caps_of(submitted),
            )
            return account_id

        return assist_answer(request, assist_rows.SAVE, submitted, save)

    def campaign_references() -> campaign_editor_rows.References:
        return campaign_editor_rows.References(
            setup_queries.segments(),
            setup_queries.markets(),
            setup_queries.offers(),
            setup_queries.sequences(),
        )

    def campaign_list_answer(
        request: Request,
        template: str,
        *,
        notice: str | None = None,
        created_id: str | None = None,
        refusal: campaign_editor_rows.Refusal | None = None,
    ) -> HTMLResponse:
        """The Campaigns list (a page or its panel), in the caller's read-only unit of work."""
        try:
            context = campaign_editor_rows.list_context(
                setup_queries.campaigns(),
                campaign_references(),
                notice=notice,
                created_id=created_id,
                refusal=refusal,
            )
        except STORED_ROW_REFUSED as unreadable:
            context = campaign_editor_rows.unavailable_context(unreadable.message)
        return page(request, template, **context)

    def campaign_editor_answer(
        request: Request,
        campaign_id: str,
        template: str,
        *,
        notice: str | None = None,
        alert: str | None = None,
        refusal: campaign_editor_rows.Refusal | None = None,
    ) -> Response:
        """One Campaign's page or panel, in the caller's read-only unit of work; 404 for an
        unknown id (never echoed); the unavailable notice when a stored row no longer
        decodes (on the full list page for the full-page GET, else the list panel)."""
        try:
            seeded = setup_queries.campaign(campaign_id)
            if seeded is None:
                return not_found_text(campaign_editor_rows.NOT_FOUND)
            context = campaign_editor_rows.edit_context(
                seeded,
                campaign_references(),
                sequencing_queries.enrollment_counts(seeded.id),
                notice=notice,
                alert=alert,
                refusal=refusal,
            )
        except STORED_ROW_REFUSED as unreadable:
            return campaign_unavailable(request, template, unreadable.message)
        return page(request, template, **context)

    def campaign_unavailable(request: Request, template: str, problem: str) -> HTMLResponse:
        """The unavailable notice: the full list page in place of a full page, else the list
        panel."""
        full = template == campaign_editor_rows.CAMPAIGN_TEMPLATE
        shown = campaign_editor_rows.unavailable_context(problem)
        if full:
            return page(request, campaign_editor_rows.LIST_TEMPLATE, **shown)
        return page(request, campaign_editor_rows.LIST_PANEL_TEMPLATE, **shown)

    def campaign_editor_run(
        request: Request,
        campaign_id: str,
        run: Callable[[], object],
        notice: Callable[[object], str],
        refusal: Callable[[CampaignRefused], campaign_editor_rows.Refusal],
    ) -> Response:
        """Run a Campaign page use case in its own unit of work, then render the panel in a
        read-only one: with its notice, refused with the refused form, or stale with the
        current stored Campaign."""
        try:
            with units.open():
                result = run()
        except CampaignRefused as refused:  # 200, so HTMX swaps the panel in; nothing was saved
            with units.open(read_only=True):
                if campaign_editor_rows.is_stale(refused.problems, STALE_CAMPAIGN):
                    return campaign_editor_answer(
                        request,
                        campaign_id,
                        campaign_editor_rows.CAMPAIGN_PANEL_TEMPLATE,
                        alert=campaign_editor_rows.stale_notice(refused.problems),
                    )
                return campaign_editor_answer(
                    request,
                    campaign_id,
                    campaign_editor_rows.CAMPAIGN_PANEL_TEMPLATE,
                    refusal=refusal(refused),
                )
        except STORED_ROW_REFUSED as unreadable:  # a stored row no longer decodes; no write
            with units.open(read_only=True):
                return campaign_unavailable(
                    request, campaign_editor_rows.CAMPAIGN_PANEL_TEMPLATE, unreadable.message
                )
        with units.open(read_only=True):
            return campaign_editor_answer(
                request,
                campaign_id,
                campaign_editor_rows.CAMPAIGN_PANEL_TEMPLATE,
                notice=notice(result),
            )

    @app.get("/campaigns", name="campaigns_list")
    @personal_data(False)  # Campaigns, Variants and their references are configuration
    def campaigns_list(request: Request) -> HTMLResponse:
        return campaign_list_answer(request, campaign_editor_rows.LIST_TEMPLATE)

    @app.post("/campaigns/create", name="campaign_create")
    @personal_data(False)
    def campaign_create(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        texts = campaign_editor_rows.submitted_create(fields)
        try:
            with units.open():
                saved = campaigns.create_campaign(
                    texts["key"],
                    texts["name"],
                    texts["segment"],
                    texts["market"],
                    texts["offer"],
                    texts["sequence"],
                )
        except CampaignRefused as refused:
            refusal = campaign_editor_rows.Refusal(
                campaign_editor_rows.CREATE, refused.problems, texts
            )
            with units.open(read_only=True):
                return campaign_list_answer(
                    request, campaign_editor_rows.LIST_PANEL_TEMPLATE, refusal=refusal
                )
        except STORED_ROW_REFUSED as unreadable:  # a stored row no longer decodes; no write
            with units.open(read_only=True):
                return campaign_unavailable(
                    request, campaign_editor_rows.LIST_PANEL_TEMPLATE, unreadable.message
                )
        with units.open(read_only=True):
            return campaign_list_answer(
                request,
                campaign_editor_rows.LIST_PANEL_TEMPLATE,
                notice=campaign_editor_rows.created_notice(saved.key),
                created_id=saved.campaign_id,
            )

    @app.get("/campaigns/{campaign_id}", name="campaign_editor")
    @personal_data(False)
    def campaign_editor(request: Request, campaign_id: str) -> Response:
        return campaign_editor_answer(request, campaign_id, campaign_editor_rows.CAMPAIGN_TEMPLATE)

    @app.post("/campaigns/{campaign_id}/edit", name="campaign_edit")
    @personal_data(False)
    def campaign_edit(
        request: Request, campaign_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        based_on, texts = campaign_editor_rows.submitted_edit(fields)
        return campaign_editor_run(
            request,
            campaign_id,
            lambda: campaigns.edit_campaign(
                campaign_id,
                based_on,
                texts["name"],
                texts["segment"],
                texts["market"],
                texts["offer"],
                texts["sequence"],
            ),
            lambda saved: campaign_editor_rows.saved_notice(
                saved.key, saved.version, saved.changed  # type: ignore[attr-defined]
            ),
            lambda refused: campaign_editor_rows.Refusal(
                campaign_editor_rows.EDIT, refused.problems, texts
            ),
        )

    @app.post("/campaigns/{campaign_id}/variants/add", name="campaign_variant_add")
    @personal_data(False)
    def campaign_variant_add(
        request: Request, campaign_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        based_on, submitted = campaign_editor_rows.submitted_variant(fields)
        return campaign_editor_run(
            request,
            campaign_id,
            lambda: campaigns.add_variant(
                campaign_id,
                submitted.name,
                campaign_editor_rows.step_angles(submitted),
                based_on=based_on,
            ),
            lambda added: campaign_editor_rows.variant_added_notice(
                added.name  # type: ignore[attr-defined]
            ),
            lambda refused: campaign_editor_rows.Refusal(
                campaign_editor_rows.ADD_VARIANT,
                refused.problems,
                {"name": submitted.name},
                submitted.angles,
            ),
        )

    @app.post(
        "/campaigns/{campaign_id}/variants/{variant_id}/edit", name="campaign_variant_edit"
    )
    @personal_data(False)
    def campaign_variant_edit(
        request: Request,
        campaign_id: str,
        variant_id: str,
        fields: Annotated[FormFields, Depends(form_fields)],
    ) -> Response:
        based_on, submitted = campaign_editor_rows.submitted_variant(fields)
        return campaign_editor_run(
            request,
            campaign_id,
            lambda: campaigns.edit_variant(
                campaign_id,
                variant_id,
                based_on,
                submitted.name,
                campaign_editor_rows.step_angles(submitted),
            ),
            lambda saved: campaign_editor_rows.variant_saved_notice(
                saved.name, saved.changed  # type: ignore[attr-defined]
            ),
            lambda refused: campaign_editor_rows.Refusal(
                campaign_editor_rows.EDIT_VARIANT,
                refused.problems,
                {"name": submitted.name},
                submitted.angles,
                variant_id,
            ),
        )

    @app.post(
        "/campaigns/{campaign_id}/variants/{variant_id}/retire", name="campaign_variant_retire"
    )
    @personal_data(False)
    def campaign_variant_retire(
        request: Request,
        campaign_id: str,
        variant_id: str,
        fields: Annotated[FormFields, Depends(form_fields)],
    ) -> Response:
        based_on = campaign_editor_rows.submitted_retire(fields)
        return campaign_editor_run(
            request,
            campaign_id,
            lambda: campaigns.retire_variant(campaign_id, variant_id, based_on),
            lambda saved: campaign_editor_rows.variant_retired_notice(
                saved.name  # type: ignore[attr-defined]
            ),
            lambda refused: campaign_editor_rows.Refusal(
                campaign_editor_rows.RETIRE_VARIANT, refused.problems, variant_id=variant_id
            ),
        )

    def facts_answer(
        request: Request,
        template: str,
        *,
        notice: str | None = None,
        refusal: fact_rows.Refusal | None = None,
    ) -> HTMLResponse:
        segments = [
            fact_rows.Named(seeded.id, seeded.definition.name)
            for seeded in setup_queries.segments()
        ]
        markets = [
            fact_rows.Named(seeded.id, seeded.definition.name)
            for seeded in setup_queries.markets()
        ]
        context = fact_rows.panel_context(
            playbook_queries.company_facts(),
            segments,
            markets,
            facts.today(),
            notice=notice,
            refusal=refusal,
        )
        return page(request, template, **context)

    @app.get("/facts", name="company_facts_page")
    @personal_data(False)  # facts are claims about Nextron, never about a person
    def company_facts_page(request: Request) -> HTMLResponse:
        return facts_answer(request, fact_rows.PAGE_TEMPLATE)

    @app.post("/facts/add", name="company_fact_add")
    @personal_data(False)
    def company_fact_add(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        fact_id, submitted = fact_rows.submitted_add(fields)
        try:
            with units.open():
                saved = facts.add(fact_id, submitted)
        except InvalidFact as refused:  # 200, so HTMX swaps the panel in; nothing was saved
            refusal = fact_rows.Refusal("add", fact_id, refused.problems, submitted)
            with units.open(read_only=True):
                return facts_answer(request, fact_rows.PANEL_TEMPLATE, refusal=refusal)
        notice = fact_rows.saved_notice(saved)
        with units.open(read_only=True):
            return facts_answer(request, fact_rows.PANEL_TEMPLATE, notice=notice)

    @app.post("/facts/edit", name="company_fact_edit")
    @personal_data(False)
    def company_fact_edit(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        fact_id, based_on, submitted = fact_rows.submitted_edit(fields)
        try:
            with units.open():
                saved = facts.edit(fact_id, based_on, submitted)
        except InvalidFact as refused:  # 200, so HTMX swaps the panel in; nothing was saved
            refusal = fact_rows.Refusal("edit", fact_id, refused.problems, submitted, based_on)
            with units.open(read_only=True):
                return facts_answer(request, fact_rows.PANEL_TEMPLATE, refusal=refusal)
        notice = fact_rows.saved_notice(saved)
        with units.open(read_only=True):
            return facts_answer(request, fact_rows.PANEL_TEMPLATE, notice=notice)

    @app.post("/facts/retract", name="company_fact_retract")
    @personal_data(False)
    def company_fact_retract(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        fact_id = fact_rows.submitted_retract(fields)
        try:
            with units.open():
                retracted = facts.retract(fact_id)
        except InvalidFact as refused:  # 200: nothing was retracted
            refusal = fact_rows.Refusal("retract", fact_id, refused.problems)
            with units.open(read_only=True):
                return facts_answer(request, fact_rows.PANEL_TEMPLATE, refusal=refusal)
        notice = fact_rows.retracted_notice(retracted)
        with units.open(read_only=True):
            return facts_answer(request, fact_rows.PANEL_TEMPLATE, notice=notice)

    def segments_answer(
        request: Request,
        template: str,
        *,
        notice: str | None = None,
        refused: tuple[segment_rows.SubmittedSegment, Mapping[str, str]] | None = None,
    ) -> HTMLResponse:
        """The Segments list (page or panel), in the caller's read-only unit of work."""
        try:
            rows = segment_rows.segment_rows(setup_queries.segments(), setup_queries.campaigns())
        except STORED_ROW_REFUSED as unreadable:  # the create form stays reachable
            context = segment_rows.list_context(
                (), notice=notice, refused=refused, unavailable=unreadable.message
            )
        else:
            context = segment_rows.list_context(rows, notice=notice, refused=refused)
        return page(request, template, **context)

    def segment_answer(
        request: Request,
        segment_id: str,
        template: str,
        *,
        notice: str | None = None,
        refused: tuple[segment_rows.SubmittedSegment, int, Mapping[str, str]] | None = None,
    ) -> Response:
        """One Segment's page or panel, in the caller's read-only unit of work; 404 for an
        unknown id (never echoed)."""
        seeded = setup_queries.segment_by_id(segment_id)
        if seeded is None:
            return not_found_text(segment_rows.NOT_FOUND)
        campaigns = sum(1 for each in setup_queries.campaigns() if each.segment_id == seeded.id)
        context = segment_rows.edit_context(
            seeded,
            campaigns,
            len(scoring_queries.segment_leads(seeded.id)),
            notice=notice,
            refused=refused,
        )
        return page(request, template, **context)

    @app.get("/segments", name="segments_page")
    @personal_data(False)  # role titles, keywords and rules, never a person
    def segments_page(request: Request) -> HTMLResponse:
        return segments_answer(request, segment_rows.PAGE_TEMPLATE)

    @app.post("/segments/create", name="segment_create")
    @personal_data(False)
    def segment_create(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        submitted = segment_rows.submitted_segment(fields, with_key=True)
        proposed = segment_rows.proposed_form(submitted)
        try:
            with units.open():
                saved = segment_editor.create(proposed.form)
        except SegmentRefused as refusal:  # 200, so HTMX swaps the panel in; nothing was saved
            problems = segment_rows.form_problems(refusal.problems, proposed)
            with units.open(read_only=True):
                return segments_answer(
                    request, segment_rows.PANEL_TEMPLATE, refused=(submitted, problems)
                )
        notice = segment_rows.created_notice(saved.key)
        with units.open(read_only=True):
            return segments_answer(request, segment_rows.PANEL_TEMPLATE, notice=notice)

    @app.get("/segments/{segment_id}", name="segment_page")
    @personal_data(False)
    def segment_page(request: Request, segment_id: str) -> Response:
        return segment_answer(request, segment_id, segment_rows.SEGMENT_TEMPLATE)

    @app.get("/segments/{segment_id}/versions", name="segment_versions_page")
    @personal_data(False)
    def segment_versions_page(request: Request, segment_id: str) -> Response:
        seeded = setup_queries.segment_by_id(segment_id)
        if seeded is None:
            return not_found_text(segment_rows.NOT_FOUND)
        context = segment_rows.versions_context(
            seeded, setup_queries.segment_versions(seeded.id)
        )
        return page(request, segment_rows.VERSIONS_TEMPLATE, **context)

    @app.post("/segments/{segment_id}/edit", name="segment_edit")
    @personal_data(False)
    def segment_edit(
        request: Request, segment_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        submitted = segment_rows.submitted_segment(fields, with_key=False)
        based_on = segment_rows.submitted_based_on(fields)
        proposed = segment_rows.proposed_form(submitted, key=None)
        proposed.form.pop(segment_rows.KEY, None)  # the editor keeps the stored key
        try:
            with units.open():
                saved = segment_editor.edit(segment_id, based_on, proposed.form)
        except SegmentRefused as refusal:  # 200, so HTMX swaps the panel in; nothing was saved
            problems = segment_rows.form_problems(refusal.problems, proposed)
            with units.open(read_only=True):
                return segment_answer(
                    request,
                    segment_id,
                    segment_rows.SEGMENT_PANEL_TEMPLATE,
                    refused=(submitted, based_on, problems),
                )
        notice = segment_rows.saved_notice(saved.key, saved.version, saved.changed)
        with units.open(read_only=True):
            return segment_answer(
                request, segment_id, segment_rows.SEGMENT_PANEL_TEMPLATE, notice=notice
            )

    @app.post("/segments/{segment_id}/rescore-current", name="segment_rescore_current")
    @personal_data(False)
    def segment_rescore_current(
        request: Request, segment_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        segment_rows.submitted_rescore(fields)
        with units.open():  # an unknown Segment covers no Lead, then answers 404 below
            leads = lead_scores.request_segment_rescore(segment_id)
        notice = segment_rows.rescore_notice(leads)
        with units.open(read_only=True):
            return segment_answer(
                request, segment_id, segment_rows.SEGMENT_PANEL_TEMPLATE, notice=notice
            )

    def sequence_answer(
        request: Request,
        sequence_id: str,
        template: str,
        *,
        notice: str | None = None,
        alert: str | None = None,
        refused: tuple[sequence_rows.SubmittedSequence, int, Mapping[str, str]] | None = None,
    ) -> Response:
        """One Sequence's page or panel, in the caller's read-only unit of work; 404 for an
        unknown id (never echoed); the unavailable notice when a stored row no longer
        decodes."""
        try:
            seeded = setup_queries.sequence_by_id(sequence_id)
            if seeded is None:
                return not_found_text(sequence_rows.NOT_FOUND)
            context = sequence_rows.edit_context(
                seeded,
                setup_queries.campaigns(),
                sequencing_queries.live_enrollments_by_version(seeded.id),
                notice=notice,
                alert=alert,
                refused=refused,
            )
        except STORED_ROW_REFUSED as unreadable:
            return sequence_unavailable(request, template, unreadable.message)
        return page(request, template, **context)

    def sequence_unavailable(request: Request, template: str, message: str) -> HTMLResponse:
        """The Sequences list's unavailable notice, as a page (a page ``template``) or a panel."""
        shown = (
            sequence_rows.PANEL_TEMPLATE
            if template == sequence_rows.SEQUENCE_PANEL_TEMPLATE
            else sequence_rows.PAGE_TEMPLATE
        )
        return page(request, shown, **sequence_rows.list_context((), unavailable=message))

    @app.get("/sequences", name="sequences_page")
    @personal_data(False)  # Step briefs and rules, never a person
    def sequences_page(request: Request) -> HTMLResponse:
        try:
            rows = sequence_rows.sequence_rows(
                setup_queries.sequences(), setup_queries.campaigns()
            )
        except STORED_ROW_REFUSED as unreadable:
            context = sequence_rows.list_context((), unavailable=unreadable.message)
        else:
            context = sequence_rows.list_context(rows)
        return page(request, sequence_rows.PAGE_TEMPLATE, **context)

    @app.get("/sequences/{sequence_id}", name="sequence_page")
    @personal_data(False)
    def sequence_page(request: Request, sequence_id: str) -> Response:
        return sequence_answer(request, sequence_id, sequence_rows.SEQUENCE_TEMPLATE)

    @app.get("/sequences/{sequence_id}/versions", name="sequence_versions_page")
    @personal_data(False)
    def sequence_versions_page(request: Request, sequence_id: str) -> Response:
        try:
            seeded = setup_queries.sequence_by_id(sequence_id)
            if seeded is None:
                return not_found_text(sequence_rows.NOT_FOUND)
            context = sequence_rows.versions_context(
                seeded, setup_queries.sequence_versions(seeded.id)
            )
        except STORED_ROW_REFUSED as unreadable:
            return sequence_unavailable(request, sequence_rows.PAGE_TEMPLATE, unreadable.message)
        return page(request, sequence_rows.VERSIONS_TEMPLATE, **context)

    @app.post("/sequences/{sequence_id}/edit", name="sequence_edit")
    @personal_data(False)
    def sequence_edit(
        request: Request, sequence_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        submitted = sequence_rows.submitted_sequence(fields)
        based_on = sequence_rows.submitted_based_on(fields)
        proposed = sequence_rows.proposed_fields(submitted)
        try:
            with units.open():
                saved = sequence_editor.edit(sequence_id, based_on, proposed.fields)
        except SequenceRefused as refusal:  # 200, so HTMX swaps the panel in; nothing was saved
            if sequence_rows.is_stale(refusal.problems, SEQUENCE_STALE):
                # The current stored version's form, so the next save isn't stale too.
                with units.open(read_only=True):
                    return sequence_answer(
                        request,
                        sequence_id,
                        sequence_rows.SEQUENCE_PANEL_TEMPLATE,
                        alert=sequence_rows.stale_notice(refusal.problems),
                    )
            problems = sequence_rows.form_problems(refusal.problems, proposed)
            with units.open(read_only=True):
                return sequence_answer(
                    request,
                    sequence_id,
                    sequence_rows.SEQUENCE_PANEL_TEMPLATE,
                    refused=(submitted, based_on, problems),
                )
        notice = sequence_rows.saved_notice(saved.key, saved.version, saved.changed)
        with units.open(read_only=True):
            return sequence_answer(
                request, sequence_id, sequence_rows.SEQUENCE_PANEL_TEMPLATE, notice=notice
            )

    def ai_content_answer(
        request: Request, template: str, *, notice: str | None = None, alert: str | None = None
    ) -> HTMLResponse:
        context = ai_rows.panel_context(
            playbook_queries.route_tuples(),
            playbook_queries.playbook_status(),
            notice=notice,
            alert=alert,
        )
        return page(request, template, **context)

    @app.get("/ai", name="ai_content_page")
    @personal_data(False)  # route codes, models, versions, hashes and times
    def ai_content_page(request: Request) -> HTMLResponse:
        return ai_content_answer(request, ai_rows.PAGE_TEMPLATE)

    @app.post("/ai/check", name="ai_content_check")
    @personal_data(False)
    def ai_content_check(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        ai_rows.submitted_check(fields)
        try:
            files, defaults, read = ai_content()  # file I/O, outside any unit of work
            with units.open():
                result = playbook.sync_prompts(
                    files, defaults, read, trigger=SyncTrigger.OPERATOR_CHECK
                )
        except InvalidPrompts as refused:  # 200, so HTMX swaps the panel in; nothing stored
            with units.open(read_only=True):
                return ai_content_answer(
                    request, ai_rows.PANEL_TEMPLATE, alert=ai_rows.prompts_refused(refused)
                )
        with units.open():  # decision 4: each new tuple's test run, in a unit of work of its own
            queued = test_runs.queue_runs()
        with units.open(read_only=True):
            return ai_content_answer(
                request,
                ai_rows.PANEL_TEMPLATE,
                notice=ai_rows.check_notice(result, queued),
                alert=ai_rows.check_alert(result),
            )

    @app.post("/ai/rollback", name="ai_content_rollback")
    @personal_data(False)
    def ai_content_rollback(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        tuple_id = ai_rows.submitted_rollback(fields)
        try:
            with units.open():
                activated = playbook.activate(tuple_id, trigger=ActivationTrigger.ROLLBACK)
        except ActivationRefused as refused:  # 200, so HTMX swaps the panel in; nothing changed
            with units.open(read_only=True):
                return ai_content_answer(request, ai_rows.PANEL_TEMPLATE, alert=refused.message)
        with units.open(read_only=True):
            return ai_content_answer(
                request, ai_rows.PANEL_TEMPLATE, notice=ai_rows.rollback_notice(activated)
            )

    def row_answer(request: Request, row: credential_rows.CredentialRow) -> HTMLResponse:
        return page(request, credential_rows.ROW_TEMPLATE, row=row)

    def store_failed_row(request: Request, key: str) -> HTMLResponse:
        # 200, so HTMX swaps the row in, showing what Credential Manager holds now
        notice = credential_rows.store_failed()
        return row_answer(
            request, credential_rows.credential_row(credentials.status(key), notice=notice)
        )

    @app.get("/credentials", name="credentials_page")
    @personal_data(True)  # the Google section shows each grant's account address
    def credentials_page(request: Request) -> HTMLResponse:
        rows = credential_rows.page_rows(credentials.statuses())
        client = google.client_status()
        google_section = google_rows.grant_rows(google.grant_statuses(), client)
        # last: its database read begins the read transaction, so every Credential Manager read
        # but its own unsubscribe keys' runs before it
        unsub_groups = credential_rows.unsub_groups(credentials.unsub_keys())
        return page(
            request,
            credential_rows.PAGE_TEMPLATE,
            rows=rows,
            unsub_groups=unsub_groups,
            env=env,
            client=google_rows.client_row(client),
            google_rows=google_section,
        )

    @app.post("/credentials/save", name="credential_save")
    @personal_data(False)
    def credential_save(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        key, text = credential_rows.submitted_save(fields)
        try:
            status = credentials.save(key, text)
        except InvalidCredentialValue as refused:
            # 200, so HTMX swaps the row in; nothing was written, and the field is empty again
            notice = credential_rows.refused(refused.reason)
            return row_answer(
                request, credential_rows.credential_row(credentials.status(key), notice=notice)
            )
        except CredentialStoreFailed:
            return store_failed_row(request, key)
        return row_answer(
            request, credential_rows.credential_row(status, notice=credential_rows.saved())
        )

    @app.post("/credentials/remove", name="credential_remove")
    @personal_data(False)
    def credential_remove(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        key, confirmed = credential_rows.submitted_remove(fields)
        if not confirmed:  # the first click only asks; nothing is deleted
            row = credential_rows.credential_row(credentials.status(key), confirming=True)
            return row_answer(request, row)
        try:
            status = credentials.remove(key)
        except CredentialStoreFailed:
            return store_failed_row(request, key)
        return row_answer(
            request, credential_rows.credential_row(status, notice=credential_rows.removed())
        )

    def unsub_row_answer(
        request: Request,
        kid: int,
        key: str,
        *,
        notice: credential_rows.Notice | None = None,
        confirming: bool = False,
    ) -> HTMLResponse:
        # 200, so HTMX swaps the row in, showing what Credential Manager holds now
        status = credentials.unsub_key_status(kid, key)
        row = credential_rows.unsub_key_row(status, notice=notice, confirming=confirming)
        return page(request, credential_rows.UNSUB_ROW_TEMPLATE, row=row)

    @app.post("/credentials/unsub/save", name="unsub_key_save")
    @personal_data(False)
    def unsub_key_save(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        kid, key, text = credential_rows.submitted_unsub_save(fields)
        try:
            with units.open(read_only=True):
                status = credentials.save_unsub_key(kid, key, text)
        except InvalidCredentialValue as refused:  # nothing was written; the field is empty
            return unsub_row_answer(
                request, kid, key, notice=credential_rows.refused(refused.reason)
            )
        except (UnsubKeyInUse, UnsubKeysHeldInRecovery) as in_use:
            return unsub_row_answer(request, kid, key, notice=credential_rows.refused(str(in_use)))
        except CredentialStoreFailed:
            return unsub_row_answer(request, kid, key, notice=credential_rows.store_failed())
        row = credential_rows.unsub_key_row(status, notice=credential_rows.saved())
        return page(request, credential_rows.UNSUB_ROW_TEMPLATE, row=row)

    @app.post("/credentials/unsub/remove", name="unsub_key_remove")
    @personal_data(False)
    def unsub_key_remove(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        kid, key, confirmed = credential_rows.submitted_unsub_remove(fields)
        if not confirmed:  # the first click only asks; nothing is deleted
            return unsub_row_answer(request, kid, key, confirming=True)
        try:
            with units.open(read_only=True):
                status = credentials.remove_unsub_key(kid, key)
        except (UnsubKeyInUse, UnsubKeysHeldInRecovery) as in_use:
            return unsub_row_answer(
                request, kid, key, notice=credential_rows.not_removed(str(in_use))
            )
        except CredentialStoreFailed:
            return unsub_row_answer(request, kid, key, notice=credential_rows.store_failed())
        row = credential_rows.unsub_key_row(status, notice=credential_rows.removed())
        return page(request, credential_rows.UNSUB_ROW_TEMPLATE, row=row)

    def client_answer(request: Request, row: google_rows.ClientRow) -> HTMLResponse:
        return page(request, google_rows.CLIENT_ROW_TEMPLATE, client=row)

    def client_refused(request: Request, notice: credential_rows.Notice) -> HTMLResponse:
        # 200, so HTMX swaps the row in; it shows what Credential Manager holds now
        return client_answer(request, google_rows.client_row(google.client_status(), notice=notice))

    def grant_answer(
        request: Request,
        grant: str,
        *,
        status: GrantStatus | None = None,
        notice: credential_rows.Notice | None = None,
        confirming: bool = False,
    ) -> HTMLResponse:
        current = status if status is not None else google.grant_status(grant)
        row = google_rows.grant_row(
            current, google.client_status(), notice=notice, confirming=confirming
        )
        return page(request, google_rows.GRANT_ROW_TEMPLATE, row=row)

    @app.post("/credentials/google/client", name="google_client_import")
    @personal_data(False)
    def google_client_import(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        path_text = google_rows.submitted_path(fields)
        try:
            result = google.import_client(path_text)
        except InvalidClientFile as refused:  # nothing stored; the file is where it was
            return client_refused(request, google_rows.not_imported(refused.reason))
        except CredentialStoreFailed:
            return client_refused(request, google_rows.store_failed())
        notice = google_rows.imported(result.file_deleted)
        return client_answer(
            request, google_rows.client_row(result.status, notice=notice, reload_hint=True)
        )

    @app.post("/credentials/google/grant", name="google_grant_start")
    @personal_data(True)
    def google_grant_start(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        grant = google_rows.submitted_grant(fields)
        try:
            status = google.start_grant(grant)  # the only call that starts a consent flow
        except ClientNotImported:
            return grant_answer(request, grant, notice=google_rows.import_first())
        except CredentialStoreFailed:
            return grant_answer(request, grant, notice=google_rows.store_failed())
        return grant_answer(request, grant, status=status)

    @app.get("/credentials/google/grant/{grant}", name="google_grant_row")
    @personal_data(True)
    def google_grant_row(request: Request, grant: str) -> Response:
        if not google_rows.is_grant(grant):
            return PlainTextResponse("Not found", status_code=404, headers=NO_STORE)
        return grant_answer(request, grant)

    @app.post("/credentials/google/remove", name="google_grant_remove")
    @personal_data(True)
    def google_grant_remove(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        grant, confirmed = google_rows.submitted_remove(fields)
        if not confirmed:  # the first click only asks; nothing is revoked or deleted
            return grant_answer(request, grant, confirming=True)
        try:
            removal = google.remove_grant(grant)
        except CredentialStoreFailed:
            return grant_answer(request, grant, notice=google_rows.store_failed())
        notice = google_rows.removed_notice(removal.revocation)
        return grant_answer(request, grant, status=removal.status, notice=notice)

    def notification_list(request: Request) -> HTMLResponse:
        found = notify_queries.open_notifications(AREA_LIMIT)
        due = pipeline.call_outcomes_due()  # Story 9.4: worked out now, never stored
        context = area_context(
            found,
            errors=engine_health.issue_count(),
            recovery=ops_queries.recovery_mode(),
            shortfall=selection.today().remaining,
            due=pipeline_rows.due_items((each.lead_id, each.lead_name) for each in due),
        )
        return page(request, NOTIFICATIONS_TEMPLATE, **context)

    @app.get("/notifications", name="notifications_area")
    @personal_data(True)
    def notifications_area(request: Request) -> HTMLResponse:
        return notification_list(request)

    @app.post("/notifications/dismiss", name="notification_dismiss")
    @personal_data(True)
    def notification_dismiss(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        notification_id = submitted_notification(fields)
        with units.open():
            notify.dismiss(notification_id)
        with units.open(read_only=True):
            return notification_list(request)

    @app.get("/quotas", name="quotas_page")
    @personal_data(False)  # usage rows hold codes and counts, never personal data
    def quotas_page(request: Request) -> HTMLResponse:
        context = quota_rows.page_context(
            budget.status(), kv_list_calls=budget.kv_list_calls()
        )
        return page(request, quota_rows.PAGE_TEMPLATE, **context)

    def health_answer(
        request: Request,
        template: str,
        *,
        notice: str | None = None,
        restore_notice: str | None = None,
    ) -> HTMLResponse:
        context = health_rows.panel_context(
            engine_health.report(), notice=notice, restore_notice=restore_notice
        )
        return page(request, template, **context)

    @app.get("/health/panel", name="health_page")
    @personal_data(True)  # a dead job's last error is free text
    def health_page(request: Request) -> HTMLResponse:
        return health_answer(request, health_rows.PAGE_TEMPLATE)

    @app.get("/health/panel/live", name="health_panel")
    @personal_data(True)
    def health_panel(request: Request) -> HTMLResponse:
        return health_answer(request, health_rows.PANEL_TEMPLATE)

    @app.post("/health/jobs/dismiss", name="health_job_dismiss")
    @personal_data(True)
    def health_job_dismiss(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        job_id = health_rows.submitted_job(fields)
        with units.open():
            queue.dismiss(job_id)
        with units.open(read_only=True):
            return health_answer(request, health_rows.PANEL_TEMPLATE)

    @app.post("/health/backup", name="health_backup_now")
    @personal_data(True)
    def health_backup_now(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        health_rows.submitted_backup(fields)
        with units.open():
            enqueued = backups.request_backup()
        notice = (
            health_rows.BACKUP_QUEUED if enqueued.created else health_rows.BACKUP_ALREADY_QUEUED
        )
        with units.open(read_only=True):
            return health_answer(request, health_rows.PANEL_TEMPLATE, notice=notice)

    @app.post("/health/restore-test", name="health_restore_test_now")
    @personal_data(True)
    def health_restore_test_now(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        health_rows.submitted_restore_test(fields)
        with units.open():
            enqueued = restore_tests.request_restore_test()
        notice = (
            health_rows.RESTORE_TEST_QUEUED
            if enqueued.created
            else health_rows.RESTORE_TEST_ALREADY_QUEUED
        )
        with units.open(read_only=True):
            return health_answer(request, health_rows.PANEL_TEMPLATE, restore_notice=notice)

    def import_answer(request: Request, **context: object) -> HTMLResponse:
        with units.open(read_only=True):
            options = import_rows.segment_options(setup_queries.segments())
            return page(request, import_rows.FORM_TEMPLATE, segments=options, **context)

    @app.get("/leads/import", name="lead_import_page")
    @personal_data(False)  # Segment names only
    def lead_import_page(request: Request) -> HTMLResponse:
        options = import_rows.segment_options(setup_queries.segments())
        context = import_rows.form_context()
        return page(request, import_rows.PAGE_TEMPLATE, segments=options, **context)

    @app.post("/leads/import", name="lead_import")
    @personal_data(True)
    def lead_import(
        request: Request, form: Annotated[UploadForm, Depends(upload_form)]
    ) -> HTMLResponse:
        if form.too_large:  # the body was discarded unread, so nothing typed can be kept
            return import_answer(request, **import_rows.too_large_context())
        submitted = import_rows.submitted_import(form)
        imported = ImportRequest(submitted.origin, submitted.default_segment, submitted.data)
        try:
            with units.open():
                summary = lead_imports.run(imported)
        except ImportRefused as refused:
            # 200, so HTMX swaps the block in; nothing was imported
            return import_answer(request, **import_rows.refused_context(submitted, refused))
        except ImportStopped as stopped:
            # keep what was typed: the notice says to import the same file again
            context = import_rows.form_context(
                origin=submitted.origin,
                default_segment=submitted.default_segment or "",
                summary=stopped.summary,
                stopped=import_rows.stop_notice(stopped.at_row),
            )
            return import_answer(request, **context)
        return import_answer(request, **import_rows.form_context(summary=summary))

    def signal_inbox_answer(
        request: Request, template: str, **context: object
    ) -> HTMLResponse:
        found = inbox_page_rows.panel_context(
            discovery_queries.signal_items(), setup_queries.segments(), **context
        )
        return page(request, template, **found)

    @app.get("/signals", name="signal_inbox_page")
    @personal_data(True)  # pasted text and the names in it
    def signal_inbox_page(request: Request) -> HTMLResponse:
        return signal_inbox_answer(request, inbox_page_rows.PAGE_TEMPLATE)

    @app.post("/signals", name="signal_inbox_submit")
    @personal_data(True)
    def signal_inbox_submit(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        rows = inbox_page_rows.submitted_rows(fields)
        try:
            items = check_items(rows)
        except ItemsRefused as refused:
            # 200, so HTMX swaps the panel in; nothing was stored, and the typed values stay
            with units.open(read_only=True):
                return signal_inbox_answer(
                    request, inbox_page_rows.PANEL_TEMPLATE, rows=rows, errors=refused.errors
                )
        with units.open():
            signal_inbox.submit(items)
        with units.open(read_only=True):
            return signal_inbox_answer(
                request,
                inbox_page_rows.PANEL_TEMPLATE,
                notice=inbox_page_rows.Notice("status", inbox_page_rows.SUBMITTED),
            )

    @app.post("/signals/{item_id}/reprocess", name="signal_inbox_reprocess")
    @personal_data(True)
    def signal_inbox_reprocess(
        request: Request, item_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        choice = inbox_page_rows.submitted_reprocess(fields)
        if not is_ulid(item_id):
            raise MalformedForm(WRONG_FIELDS)
        with units.open():
            outcome = signal_inbox.reprocess(item_id, choice)
        with units.open(read_only=True):
            return signal_inbox_answer(
                request,
                inbox_page_rows.PANEL_TEMPLATE,
                notice=inbox_page_rows.REPROCESS_NOTICES[outcome],
            )

    def inboxes_answer(
        request: Request,
        template: str,
        *,
        notice: credential_rows.Notice | None = None,
        address: str = "",
        label: str = "",
        typed_warmup: inbox_rows.WarmupForm | None = None,
    ) -> HTMLResponse:
        context = inbox_rows.panel_context(
            sending_queries.inboxes(),
            caps=sending.inbox_caps().caps,
            notice=notice,
            address=address,
            label=label,
            typed_warmup=typed_warmup,
        )
        if template == inbox_rows.PAGE_TEMPLATE:
            context.update(inbox_rows.auth_context(domain_checks.auths()))
        return page(request, template, **context)

    def inbox_auth_answer(
        request: Request, *, notice: credential_rows.Notice | None = None
    ) -> HTMLResponse:
        context = inbox_rows.auth_context(domain_checks.auths(), notice=notice)
        return page(request, inbox_rows.AUTH_TEMPLATE, **context)

    @app.get("/inboxes", name="inboxes_page")
    @personal_data(True)  # the panel shows inbox addresses
    def inboxes_page(request: Request) -> HTMLResponse:
        return inboxes_answer(request, inbox_rows.PAGE_TEMPLATE)

    @app.post("/inboxes", name="inbox_register")
    @personal_data(True)
    def inbox_register(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        address, label = inbox_rows.submitted_inbox(fields)
        try:
            with units.open():
                sending.register_inbox(address, label)
        except InboxRefused as refused:
            # 200, so HTMX swaps the panel in; nothing was stored, and the typed values stay
            with units.open(read_only=True):
                return inboxes_answer(
                    request,
                    inbox_rows.PANEL_TEMPLATE,
                    notice=inbox_rows.refused(refused),
                    address=address,
                    label=label,
                )
        with units.open(read_only=True):
            return inboxes_answer(
                request, inbox_rows.PANEL_TEMPLATE, notice=inbox_rows.registered()
            )

    @app.post("/inboxes/warmup", name="inbox_warmup")
    @personal_data(True)  # answers with the whole panel, which shows inbox addresses
    def inbox_warmup(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        typed = inbox_rows.submitted_warmup(fields)
        try:
            with units.open():
                sending.record_warmup(
                    typed.inbox_id,
                    typed.warmup_started_on,
                    typed.warmup_method,
                    typed.warm_level,
                    typed.warmup_tool_daily,
                    typed.ramp_step,
                    typed.ramp_target,
                )
        except WarmupRefused as refused:
            # 200, so HTMX swaps the panel in; nothing was stored, and that inbox's typed
            # values stay
            with units.open(read_only=True):
                return inboxes_answer(
                    request,
                    inbox_rows.PANEL_TEMPLATE,
                    notice=inbox_rows.refused(refused),
                    typed_warmup=typed,
                )
        with units.open(read_only=True):
            return inboxes_answer(
                request, inbox_rows.PANEL_TEMPLATE, notice=inbox_rows.warmup_saved()
            )

    @app.get("/inboxes/auth", name="inbox_auth_panel")
    @personal_data(False)  # Sending Domain names and their check results only
    def inbox_auth_panel(request: Request) -> HTMLResponse:
        return inbox_auth_answer(request)

    @app.post("/inboxes/auth/recheck", name="inbox_auth_recheck")
    @personal_data(False)
    def inbox_auth_recheck(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        sending_domain_id = inbox_rows.submitted_recheck(fields)
        with units.open():
            queued = domain_checks.recheck(sending_domain_id)
        with units.open(read_only=True):
            if not queued:
                return inbox_auth_answer(request, notice=inbox_rows.recheck_refused())
            shown = next(
                (
                    view.domain
                    for view in domain_checks.auths()
                    if view.sending_domain_id == sending_domain_id
                ),
                sending_domain_id,
            )
            return inbox_auth_answer(request, notice=inbox_rows.recheck_queued(shown))

    def suppression_answer(
        request: Request,
        template: str,
        *,
        filters: suppression_rows.ListFilters | None = None,
        notice: credential_rows.Notice | None = None,
        featured: tuple[str, ...] = (),
        caption: str | None = None,
        invalid_lines: str | None = None,
    ) -> HTMLResponse:
        shown = filters if filters is not None else suppression_rows.ListFilters()
        found = compliance_queries.entry_list(
            kind=shown.kind, source=shown.source, status=shown.status, page=shown.page
        )
        context = suppression_rows.panel_context(
            found,
            shown,
            notice=notice,
            featured=compliance_queries.entries(featured),
            caption=caption,
            invalid_lines=invalid_lines,
        )
        return page(request, template, **context)

    def suppression_refused(request: Request, refusal: SuppressionRefused) -> HTMLResponse:
        # 200, so HTMX swaps the panel in; nothing was written
        with units.open(read_only=True):
            return suppression_answer(
                request, suppression_rows.PANEL_TEMPLATE, notice=suppression_rows.refused(refusal)
            )

    @app.get("/suppression", name="suppression_page")
    @personal_data(True)  # hash prefixes, and a firm's domain that may be a sole trader's
    def suppression_page(request: Request) -> HTMLResponse:
        filters = suppression_rows.list_filters(request.query_params.multi_items())
        return suppression_answer(request, suppression_rows.PAGE_TEMPLATE, filters=filters)

    @app.post("/suppression/add", name="suppression_add")
    @personal_data(True)
    def suppression_add(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        text = suppression_rows.submitted_value(fields)
        try:
            with units.open():
                added = compliance.add_value(text)
        except SuppressionRefused as refusal:
            return suppression_refused(request, refusal)
        if added.written:
            notice = suppression_rows.status(ADDED)
        else:
            notice = suppression_rows.status(already_on_the_list(added.since, added.source))
        with units.open(read_only=True):
            return suppression_answer(
                request,
                suppression_rows.PANEL_TEMPLATE,
                notice=notice,
                featured=added.written,
                caption=suppression_rows.ADDED_CAPTION,
            )

    @app.post("/suppression/import", name="suppression_import")
    @personal_data(True)
    def suppression_import(
        request: Request, form: Annotated[UploadForm, Depends(upload_form)]
    ) -> HTMLResponse:
        if form.too_large:  # the body was discarded unread
            return suppression_refused(request, SuppressionRefused(FILE_TOO_LARGE))
        data = suppression_rows.submitted_file(form)
        try:
            with units.open():
                outcome = compliance.import_list(data)
        except SuppressionRefused as refusal:
            return suppression_refused(request, refusal)
        notice = suppression_rows.status(
            import_message(outcome.added, outcome.already, len(outcome.invalid_lines))
        )
        with units.open(read_only=True):
            return suppression_answer(
                request,
                suppression_rows.PANEL_TEMPLATE,
                notice=notice,
                invalid_lines=invalid_lines_message(outcome.invalid_lines),
            )

    @app.post("/suppression/search", name="suppression_search")
    @personal_data(True)
    def suppression_search(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        text = suppression_rows.submitted_value(fields)
        try:
            key = search_key(text)  # hashed here; the value goes no further
        except SuppressionRefused as refusal:
            return suppression_refused(request, refusal)
        with units.open(read_only=True):
            found = (
                None if key is None else compliance_queries.active_entry(key.kind, key.value_hash)
            )
            if found is None:
                return suppression_answer(
                    request,
                    suppression_rows.PANEL_TEMPLATE,
                    notice=suppression_rows.status(NOT_ON_THE_LIST),
                )
            return suppression_answer(
                request,
                suppression_rows.PANEL_TEMPLATE,
                notice=suppression_rows.status(on_the_list(found.created_at, found.source)),
                featured=(found.id,),
                caption=suppression_rows.MATCHING_CAPTION,
            )

    @app.post("/suppression/reverse", name="suppression_reverse")
    @personal_data(True)
    def suppression_reverse(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        entry_id, reason = suppression_rows.submitted_reversal(fields)
        try:
            with units.open():
                others = compliance.reverse_entry(entry_id, reason)
        except SuppressionRefused as refusal:
            return suppression_refused(request, refusal)
        with units.open(read_only=True):
            return suppression_answer(
                request,
                suppression_rows.PANEL_TEMPLATE,
                notice=suppression_rows.status(reversed_message(len(others))),
                featured=others,
                caption=suppression_rows.SAME_OPT_OUT_CAPTION,
            )

    @app.post(export_rows.EXPORT_PATH, name="contact_export")
    @personal_data(True)
    def contact_export(
        request: Request, contact_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        reference = export_rows.submitted_reference(fields)
        with units.open(read_only=True):
            contact = research_queries.contact(contact_id)
            if contact is None:
                return not_found_text(export_rows.CONTACT_NOT_FOUND)  # never echoes the id
            name = contact.name if contact.erased_at is None else None  # a tombstone is no name
            try:  # checked before the unit of work, so a ValueError from a bug isn't a refusal
                check_request_ref(reference, name=name)
            except ValueError as refused:  # nothing ran; the typed text isn't shown
                context = export_rows.form_context(
                    contact.id, contact.name, contact.email, notice=str(refused)
                )
                return page(request, export_rows.FORM_TEMPLATE, **context)
        try:
            with units.open():
                export = erasures.export_on_request(contact_id, reference)
        except LookupError:  # a KeyError or IndexError from a bug is no missing Contact
            with units.open(read_only=True):
                missing = research_queries.contact(contact_id) is None
            if not missing:
                raise
            return not_found_text(export_rows.CONTACT_NOT_FOUND)  # never echoes the id
        ticket = tickets.issue(str(request.scope[CSRF_SCOPE_KEY]), export)
        return Response(status_code=204, headers={"HX-Redirect": f"/exports/{ticket}", **NO_STORE})

    @app.get(export_rows.DOWNLOAD_PATH, name="contact_export_download")
    @personal_data(True)
    def contact_export_download(request: Request, ticket: str) -> Response:
        export = tickets.take(ticket, request.scope.get(CSRF_SCOPE_KEY))
        if export is None:  # never echoes the ticket
            return not_found_text(export_rows.EXPORT_EXPIRED)
        return export_rows.download_response(export)

    def discovery_answer(
        request: Request,
        template: str,
        *,
        notice: discovery_rows.Notice | None = None,
        chosen: discovery_rows.SubmittedRun | None = None,
    ) -> HTMLResponse:
        campaigns = setup_queries.campaigns()
        active = [found for found in campaigns if setup_queries.campaign_active(found.id)]
        names = {found.id: found.name for found in campaigns}
        context = discovery_rows.panel_context(
            campaigns=discovery_rows.campaign_options(active),
            locations=discovery_rows.location_options(configured_locations()),
            runs=discovery_rows.run_rows(discovery_queries.recent_runs(), names),
            notice=notice,
            chosen=chosen,
        )
        return page(request, template, **context)

    # before /leads/{lead_id}, like every /leads/<word> path
    @app.get("/leads/discovery", name="discovery_page")
    @personal_data(False)  # Campaign names, Operator locations, statuses and counts
    def discovery_page(request: Request) -> HTMLResponse:
        return discovery_answer(request, discovery_rows.PAGE_TEMPLATE)

    @app.post("/leads/discovery/places", name="discovery_places")
    @personal_data(False)
    def discovery_places(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        submitted = discovery_rows.submitted_run(fields)
        try:
            with units.open():
                enqueued = place_discovery.request_run(submitted.campaign_id, submitted.location)
        except PlacesRunRefused as refused:
            notice = discovery_rows.Notice("alert", refused.message)
        else:
            notice = discovery_rows.Notice(
                "status",
                discovery_rows.QUEUED if enqueued.created else discovery_rows.ALREADY_QUEUED,
            )
        with units.open(read_only=True):
            return discovery_answer(
                request, discovery_rows.PANEL_TEMPLATE, notice=notice, chosen=submitted
            )

    @app.get("/leads", name="pipeline_page")
    @personal_data(True)  # a Lead's name can be a sole trader's name
    def pipeline_page(request: Request) -> HTMLResponse:
        segments = setup_queries.segments()
        filters = lead_rows.list_filters(request.query_params.multi_items(), segments)
        found = discovery_queries.lead_list(
            stage=filters.stage,
            segment_id=filters.segment_id,
            search=filters.search,
            page=filters.page,
            sort=filters.sort,
        )
        context = lead_rows.list_context(filters, found, segments)
        return page(request, lead_rows.LIST_TEMPLATE, **context)

    def lead_markets() -> tuple[setup_queries.SeededMarket, ...]:
        """The stored Markets that name each Contact's Market on the Lead page (Story 3.7)."""
        try:
            return setup_queries.markets()
        except InvalidMarkets:  # unreadable: each stored Market shows as "Unknown Market"
            return ()

    def lead_erasures(
        contacts: Iterable[research_queries.StoredContact],
    ) -> Mapping[str, compliance_queries.StoredRequest]:
        """The request that erased each erased Contact, for its tombstone line (Story 3.13)."""
        return compliance_queries.completed_erasures(
            contact.id for contact in contacts if contact.erased_at is not None
        )

    def lead_receipts(
        contacts: Iterable[research_queries.StoredContact],
    ) -> Mapping[str, tuple[compliance_queries.StoredReceipt, ...]]:
        """Each erased Contact's erasure receipts, oldest first (Story 3.14)."""
        return compliance_queries.receipts(
            contact.id for contact in contacts if contact.erased_at is not None
        )

    def lead_drafts(lead_id: str) -> tuple[lead_rows.DraftSource, ...]:
        """What the Lead page's Drafts section shows of each Draft of the Lead's Enrollments
        (Story 4.12), oldest Enrollment first, each by Step, with its current revision's
        grounding result (Story 4.14)."""
        settings = setup_queries.footer_settings()
        found: list[lead_rows.DraftSource] = []
        for enrollment in sequencing_queries.enrollments_of_lead(lead_id):
            contact = research_queries.contact(enrollment.contact_id)
            check = None
            if contact is not None and contact.market_id is not None:
                try:
                    market = setup_queries.market_by_id(contact.market_id)
                except InvalidMarkets:  # unreadable: no footer check to show
                    market = None
                if market is not None:
                    check = footer_complete(settings, market.definition)
            erased = contact is None or contact.erased_at is not None
            for draft in drafting_queries.drafts_of(enrollment.id):
                revisions = drafting_queries.revisions(draft.id)
                current = next((each for each in revisions if each.rev == draft.current_rev), None)
                footer = (
                    None
                    if current is None
                    else drafting_queries.footer_snapshot(current.footer_snapshot_id)
                )
                found.append(
                    lead_rows.DraftSource(
                        draft,
                        revisions,
                        footer,
                        None if erased or contact is None else contact.email,
                        check,
                        None
                        if current is None
                        else drafting_queries.grounding_result(draft.id, current.rev),
                    )
                )
        return tuple(found)

    def missing_lead(request: Request) -> HTMLResponse:
        missing = page(request, lead_rows.MISSING_TEMPLATE)  # never echoes an id
        missing.status_code = 404
        return missing

    def erase_target(
        lead_id: str, contact_id: str
    ) -> tuple[discovery_queries.StoredLead, research_queries.StoredContact] | None:
        """The Lead's survivor and the Contact, when the Contact is on it."""
        lead = discovery_queries.survivor_of(lead_id)
        contact = research_queries.contact(contact_id)
        if lead is None or contact is None or contact.lead_id != lead.id:
            return None
        return lead, contact

    def erase_answer(
        request: Request,
        template: str,
        lead_id: str,
        contact_id: str,
        *,
        notice: erase_rows.Notice | None = None,
        request_ref: str = "",
    ) -> HTMLResponse:
        found = erase_target(lead_id, contact_id)
        if found is None:
            return missing_lead(request)
        lead, contact = found
        receipts = ()
        if contact.erased_at is not None:  # Story 3.14: an erased Contact shows its receipts
            receipts = compliance_queries.receipts([contact.id]).get(contact.id, ())
        context = erase_rows.panel_context(
            lead, contact, notice=notice, request_ref=request_ref, receipts=receipts
        )
        return page(request, template, **context)

    def ready_answer(
        request: Request, template: str, results: tuple[ready_rows.ResultView, ...] = ()
    ) -> HTMLResponse:
        context = ready_rows.page_context(selection.today(), selection.ready_list(), results)
        return page(request, template, **context)

    # Story 4.10: before /leads/{lead_id}, like every /leads/<word> path
    @app.get("/leads/ready", name="ready_page")
    @personal_data(True)  # a Lead's name can be a sole trader's name; a Contact's role
    def ready_page(request: Request) -> HTMLResponse:
        return ready_answer(request, ready_rows.PAGE_TEMPLATE)

    @app.post("/leads/ready/pick", name="ready_pick")
    @personal_data(True)  # the list's Leads and Contacts' roles, and the picked Leads' names
    def ready_pick(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        lead_ids = ready_rows.submitted_picks(fields)
        with units.open():
            outcomes = selection.pick(lead_ids)
        with units.open(read_only=True):  # 200, so HTMX swaps the list in
            names = {}
            for outcome in outcomes:
                found = discovery_queries.lead(outcome.lead_id)
                names[outcome.lead_id] = None if found is None else found.name
            results = tuple(
                ready_rows.result_row(outcome, names[outcome.lead_id]) for outcome in outcomes
            )
            return ready_answer(request, ready_rows.LIST_TEMPLATE, results)

    # before /leads/{lead_id}, like every /leads/<word> path
    @app.get(board_rows.BOARD_PATH, name="pipeline_board")
    @personal_data(True)  # a Lead's name can be a sole trader's name
    def pipeline_board(request: Request) -> HTMLResponse:
        segments = setup_queries.segments()
        markets = lead_markets()
        filters = board_rows.board_filters(
            request.query_params.multi_items(), segments, markets
        )
        board = None
        if not filters.range_refused:
            board = pipeline_queries.board(
                segment_id=filters.segment_id,
                market_id=filters.market_id,
                entered_from=filters.entered_from,
                entered_to=filters.entered_to,
                pages=filters.pages,
            )
        context = board_rows.board_context(filters, board, segments, markets)
        answer = page(request, board_rows.TEMPLATE, **context)
        if board is None:
            answer.status_code = 400
        return answer

    @app.get(report_rows.REPORT_PATH, name="calls_report_page")
    @personal_data(False)  # counts, codes and prices only: no free text, no names
    def calls_report_page(request: Request) -> HTMLResponse:
        context = report_rows.report_context(offer_validation())
        return page(request, report_rows.TEMPLATE, **context)

    # before /leads/{lead_id}, like every /leads/<word> path
    @app.get("/leads/{lead_id}/contacts/{contact_id}/erase", name="contact_erase_page")
    @personal_data(True)  # it names the Contact
    def contact_erase_page(request: Request, lead_id: str, contact_id: str) -> HTMLResponse:
        return erase_answer(request, erase_rows.PAGE_TEMPLATE, lead_id, contact_id)

    @app.post("/leads/{lead_id}/contacts/{contact_id}/erase", name="contact_erase")
    @personal_data(True)
    def contact_erase(
        request: Request,
        lead_id: str,
        contact_id: str,
        fields: Annotated[FormFields, Depends(form_fields)],
    ) -> HTMLResponse:
        request_ref = erase_rows.submitted_ref(fields)
        with units.open(read_only=True):
            found = erase_target(lead_id, contact_id)
            if found is None:
                return missing_lead(request)
            survivor, contact = found
            name = contact.name if contact.erased_at is None else None  # a tombstone is no name
            try:
                check_request_ref(request_ref, name=name)
            except ValueError as refused:  # 200, so HTMX swaps the panel in; nothing ran
                return erase_answer(
                    request,
                    erase_rows.PANEL_TEMPLATE,
                    lead_id,
                    contact_id,
                    notice=erase_rows.refused(str(refused)),
                    request_ref=request_ref,
                )
        try:
            with units.open():
                result = erasures.erase_on_request(contact_id, request_ref)
        except Exception as error:  # rolled back: the request stays open
            error_type = type(error).__name__
            log.warning(
                erase_rows.FAILED_EVENT,
                extra={"contact_id": contact_id, "error_type": error_type},
            )
            with units.open(read_only=True):
                return erase_answer(
                    request,
                    erase_rows.PANEL_TEMPLATE,
                    lead_id,
                    contact_id,
                    notice=erase_rows.failed(error_type),
                    request_ref=request_ref,
                )
        href = (
            None
            if result.receipt_id is None
            else erase_rows.receipt_href(survivor.id, result.receipt_id)
        )
        notice = (
            erase_rows.erased(request_ref, href)
            if result.erased
            else erase_rows.already_erased(href)
        )
        with units.open(read_only=True):
            return erase_answer(
                request, erase_rows.PANEL_TEMPLATE, lead_id, contact_id, notice=notice
            )

    # Story 10.7: a Contact's Channel Permissions, before /leads/{lead_id} like the erase step

    def permissions_answer(
        request: Request,
        template: str,
        lead_id: str,
        contact_id: str,
        *,
        notice: permission_rows.Notice | None = None,
        refusal: permission_rows.Refusal | None = None,
    ) -> HTMLResponse:
        found = erase_target(lead_id, contact_id)
        if found is None:
            return missing_lead(request)
        lead, contact = found
        quotes = permission_rows.evidence_quotes(
            contact.id, research_queries.evidence(contact.lead_id)
        )
        context = permission_rows.panel_context(
            lead,
            contact,
            channel_permissions.view(contact.id),
            quotes,
            notice=notice,
            refusal=refusal,
        )
        return page(request, template, **context)

    def permission_action(
        request: Request,
        lead_id: str,
        contact_id: str,
        action: str,
        permission_id: str | None,
        values: Mapping[str, str],
        run: Callable[[], permission_rows.Notice],
    ) -> HTMLResponse:
        """Run one Channel Permission use case, then answer the panel."""
        with units.open(read_only=True):
            found = erase_target(lead_id, contact_id)
            if found is None:
                return missing_lead(request)
            if found[1].erased_at is not None:  # nothing runs for a tombstone
                return permissions_answer(
                    request,
                    permission_rows.PANEL_TEMPLATE,
                    lead_id,
                    contact_id,
                    notice=permission_rows.erased_unchanged(),
                )
        notice: permission_rows.Notice | None = None
        refusal: permission_rows.Refusal | None = None
        try:
            with units.open():
                notice = run()
        except PermissionRefused as refused:  # nothing was stored
            refusal = permission_rows.Refusal(action, permission_id, refused.problems, values)
        with units.open(read_only=True):
            return permissions_answer(
                request,
                permission_rows.PANEL_TEMPLATE,
                lead_id,
                contact_id,
                notice=notice,
                refusal=refusal,
            )

    @app.get(
        "/leads/{lead_id}/contacts/{contact_id}/permissions", name="contact_permissions_page"
    )
    @personal_data(True)  # it names the Contact and shows their number or handle
    def contact_permissions_page(request: Request, lead_id: str, contact_id: str) -> HTMLResponse:
        return permissions_answer(request, permission_rows.PAGE_TEMPLATE, lead_id, contact_id)

    @app.post(
        "/leads/{lead_id}/contacts/{contact_id}/permissions", name="contact_permission_record"
    )
    @personal_data(True)
    def contact_permission_record(
        request: Request,
        lead_id: str,
        contact_id: str,
        fields: Annotated[FormFields, Depends(form_fields)],
    ) -> HTMLResponse:
        values = permission_rows.submitted(fields, permission_rows.RECORD_FIELDS)

        def run() -> permission_rows.Notice:
            channel_permissions.record(contact_id, *permission_rows.record_arguments(values))
            return permission_rows.recorded()

        return permission_action(
            request, lead_id, contact_id, permission_rows.RECORD, None, values, run
        )

    @app.post(
        "/leads/{lead_id}/contacts/{contact_id}/permissions/{permission_id}/inbound",
        name="contact_permission_inbound",
    )
    @personal_data(True)
    def contact_permission_inbound(
        request: Request,
        lead_id: str,
        contact_id: str,
        permission_id: str,
        fields: Annotated[FormFields, Depends(form_fields)],
    ) -> HTMLResponse:
        values = permission_rows.submitted(fields, permission_rows.INBOUND_FIELDS)

        def run() -> permission_rows.Notice:
            channel_permissions.update_inbound(
                contact_id, permission_id, values["last_inbound_at"]
            )
            return permission_rows.inbound_updated()

        return permission_action(
            request, lead_id, contact_id, permission_rows.INBOUND, permission_id, values, run
        )

    @app.post(
        "/leads/{lead_id}/contacts/{contact_id}/permissions/{permission_id}/revoke",
        name="contact_permission_revoke",
    )
    @personal_data(True)
    def contact_permission_revoke(
        request: Request,
        lead_id: str,
        contact_id: str,
        permission_id: str,
        fields: Annotated[FormFields, Depends(form_fields)],
    ) -> HTMLResponse:
        values = permission_rows.submitted(fields, permission_rows.NO_FIELDS)

        def run() -> permission_rows.Notice:
            channel_permissions.revoke(contact_id, permission_id)
            return permission_rows.revoked()

        return permission_action(
            request, lead_id, contact_id, permission_rows.REVOKE, permission_id, values, run
        )

    @app.post(
        "/leads/{lead_id}/contacts/{contact_id}/permissions/{permission_id}/do-not-contact",
        name="contact_permission_do_not_contact",
    )
    @personal_data(True)
    def contact_permission_do_not_contact(
        request: Request,
        lead_id: str,
        contact_id: str,
        permission_id: str,
        fields: Annotated[FormFields, Depends(form_fields)],
    ) -> HTMLResponse:
        values = permission_rows.submitted(fields, permission_rows.NO_FIELDS)

        def run() -> permission_rows.Notice:
            return permission_rows.opted_out(
                channel_permissions.opt_out(contact_id, permission_id)
            )

        return permission_action(
            request, lead_id, contact_id, permission_rows.OPT_OUT, permission_id, values, run
        )

    # after /leads/import, so that path stays the import page
    @app.get("/leads/{lead_id}", name="lead_page")
    @personal_data(True)
    def lead_page(request: Request, lead_id: str) -> Response:
        found = discovery_queries.survivor_of(lead_id)
        if found is None:  # never echoes the id
            return missing_lead(request)
        if found.id != lead_id:
            return RedirectResponse(f"/leads/{found.id}", status_code=303, headers=NO_STORE)
        contacts = research_queries.contacts(found.id)
        # live on every render, never stored (FR-18, AD-16)
        decisions = {each.id: compliance.evaluate_eligibility(each.id) for each in contacts}
        score = scoring_queries.stored(found.id)
        context = lead_rows.lead_context(
            found,
            pipeline_queries.stage(found.id),
            setup_queries.segments(),
            contacts,
            research_queries.lead_provenance(found.id),
            lead_signals.view(found.id),
            decisions,
            lead_markets(),
            research_queries.no_decision_maker(found.id),
            lead_erasures(contacts),
            postings=research_queries.job_postings(found.id),
            legal_form=research_queries.legal_form(found.id),
            receipts=lead_receipts(contacts),
            score=score,
            score_segment=scored_segment(found.segment_id, score),
            drafts=lead_drafts(found.id),
        )
        return page(
            request,
            lead_rows.LEAD_TEMPLATE,
            **context,
            **pipeline_context(found.id, contacts),
            export_forms=export_rows.lead_forms(contacts),
            tabs=history_rows.lead_tabs(found.id, history_rows.OVERVIEW),
        )

    @app.get(history_rows.HISTORY_ROUTE, name="lead_history_page")
    @personal_data(True)  # Contact names and Evidence quotes
    def lead_history_page(request: Request, lead_id: str) -> Response:
        query = history_rows.history_query(request.query_params.multi_items())
        found = discovery_queries.survivor_of(lead_id)
        if found is None:  # never echoes the id
            return missing_lead(request)
        if found.id != lead_id:
            survivor = history_rows.history_href(found.id, query.type, query.page)
            return RedirectResponse(survivor, status_code=303, headers=NO_STORE)
        view = lead_history.view(found.id, type=query.type, page=query.page)
        if view is None:  # the survivor was checked first
            return missing_lead(request)
        context = history_rows.history_context(view, found.name)
        return page(request, history_rows.TEMPLATE, **context)

    @app.post("/leads/{lead_id}/rescore", name="lead_rescore")
    @personal_data(True)  # the Lead page's panel; a Lead's name can be a sole trader's name
    def lead_rescore(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        lead_rows.submitted_rescore(fields)
        with units.open():
            requested = lead_scores.request_rescore(lead_id)
        return rescore_answer(request, lead_id, requested, lead_rows.rescore_queued())

    def rescore_answer(
        request: Request, lead_id: str, requested: RescoreRequest, queued: lead_rows.Notice
    ) -> Response:
        """The Score panel after a "Re-score" (either kind): 404 for an unknown Lead, else 200
        with ``queued`` or the refusal notice."""
        if requested is RescoreRequest.UNKNOWN:  # never echoes the id
            text = lead_rows.REFUSALS[PrimaryRefusal.UNKNOWN_LEAD]
            return PlainTextResponse(text, status_code=404, headers=NO_STORE)
        if requested is RescoreRequest.QUEUED:
            notice = queued
        else:  # nothing was queued
            notice = lead_rows.rescore_refused(requested is RescoreRequest.MERGED)
        with units.open(read_only=True):  # 200, so HTMX swaps the panel in
            found = discovery_queries.lead(lead_id)
            seeded = None if found is None else setup_queries.segment_by_id(found.segment_id)
            score = scoring_queries.stored(lead_id)
            scored = None if found is None else scored_segment(found.segment_id, score)
            context = lead_rows.score_context(
                lead_id,
                score,
                scored or (None if seeded is None else seeded.definition),
                notice,
                live=frozenset(row.id for row in research_queries.evidence(lead_id)),
                current_version=None if seeded is None else seeded.version,
            )
            return page(request, lead_rows.SCORE_TEMPLATE, **context)

    def pipeline_context(
        lead_id: str,
        contacts: Iterable[research_queries.StoredContact],
        notice: Notice | None = None,
        *,
        typed_call: pipeline_rows.CallForm | None = None,
        typed_deal: pipeline_rows.DealForm | None = None,
        typed_outcome: OutcomeForm | None = None,
        outcome_refusal: OutcomeRefused | None = None,
        typed_booking_id: str | None = None,
    ) -> dict[str, object]:
        """The Pipeline panel's context (Story 9.1), read in the open unit of work."""
        found = pipeline.lead_panel(lead_id)  # Story 9.4: with the Call Outcome form's booking
        if found is None:  # the Lead was checked first
            raise LookupError("the Lead is unknown")
        close_entries = (
            compliance_queries.entries([found.close_ref]) if found.close_ref is not None else ()
        )
        return pipeline_rows.panel_context(
            lead_id,
            found,
            contacts,
            close_entries,
            notice,
            typed_call=typed_call,
            typed_deal=typed_deal,
            typed_outcome=typed_outcome,
            outcome_refusal=outcome_refusal,
            typed_booking_id=typed_booking_id,
        )

    def live_lead(lead_id: str) -> bool:
        """Whether ``lead_id`` is a Lead that isn't merged, read in a read-only unit of work."""
        with units.open(read_only=True):
            found = discovery_queries.survivor_of(lead_id)
        return found is not None and found.id == lead_id

    def pipeline_answer(
        request: Request,
        lead_id: str,
        notice: Notice,
        *,
        typed_call: pipeline_rows.CallForm | None = None,
        typed_deal: pipeline_rows.DealForm | None = None,
        typed_outcome: OutcomeForm | None = None,
        outcome_refusal: OutcomeRefused | None = None,
        typed_booking_id: str | None = None,
    ) -> HTMLResponse:
        """The Pipeline panel alone, with ``notice``: 200, so HTMX swaps it in. A refused form
        passes what was typed, so it renders back into the form."""
        with units.open(read_only=True):
            context = pipeline_context(
                lead_id,
                research_queries.contacts(lead_id),
                notice,
                typed_call=typed_call,
                typed_deal=typed_deal,
                typed_outcome=typed_outcome,
                outcome_refusal=outcome_refusal,
                typed_booking_id=typed_booking_id,
            )
            return page(request, pipeline_rows.PANEL_TEMPLATE, **context)

    def lead_gone() -> PlainTextResponse:
        """404 for a POST to a Lead that doesn't exist (never echoing the id)."""
        text = lead_rows.REFUSALS[PrimaryRefusal.UNKNOWN_LEAD]
        return PlainTextResponse(text, status_code=404, headers=NO_STORE)

    @app.post(pipeline_rows.STAGE_ROUTE, name="lead_stage")
    @personal_data(True)  # the panel names the Lead's Contacts and shows the notes
    def lead_stage(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        move = pipeline_rows.submitted_move(fields)
        if not live_lead(lead_id):
            return lead_gone()
        try:
            with units.open():
                stage = pipeline.operator_move(lead_id, move)
        except StageRefused as refused:  # nothing was written
            notice = pipeline_rows.refused(refused.reason)
        except LookupError:
            return lead_gone()
        else:
            notice = (
                pipeline_rows.reopened(stage)
                if move == pipeline_rows.REOPEN
                else pipeline_rows.moved(stage)
            )
        return pipeline_answer(request, lead_id, notice)

    @app.post(pipeline_rows.CALL_ROUTE, name="lead_call")
    @personal_data(True)
    def lead_call(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        form = pipeline_rows.submitted_call(fields)
        if not live_lead(lead_id):
            return lead_gone()
        try:
            with units.open(read_only=True):
                contact = research_queries.contact(form.contact_id) if form.contact_id else None
            if contact is not None and contact.lead_id != lead_id:
                contact = None
            scheduled_at, zone = pipeline_rows.call_details(form, contact)
            with units.open():
                rebooked = pipeline.operator_book_call(
                    lead_id, form.contact_id, scheduled_at, zone
                )
        except StageRefused as refused:  # nothing was written; the form keeps what was typed
            notice = pipeline_rows.refused(refused.reason)
            return pipeline_answer(request, lead_id, notice, typed_call=form)
        except LookupError:
            return lead_gone()
        notice = pipeline_rows.booked(rebooked=rebooked)
        return pipeline_answer(request, lead_id, notice)

    @app.post(pipeline_rows.DEAL_ROUTE, name="lead_deal")
    @personal_data(True)
    def lead_deal(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        form = pipeline_rows.submitted_deal(fields)
        if not live_lead(lead_id):
            return lead_gone()
        try:
            deal_value = parse_deal_value(form.deal_value)
            notes = clean_notes(form.notes)
            with units.open():
                pipeline.operator_save_deal(lead_id, deal_value, notes)
        except StageRefused as refused:  # nothing was written; the form keeps what was typed
            notice = pipeline_rows.refused(refused.reason)
            return pipeline_answer(request, lead_id, notice, typed_deal=form)
        except LookupError:
            return lead_gone()
        notice = pipeline_rows.deal_saved()
        return pipeline_answer(request, lead_id, notice)

    @app.post(pipeline_rows.OUTCOME_ROUTE, name="lead_outcome")
    @personal_data(True)  # the panel names the Lead's Contacts and shows typed text
    def lead_outcome(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        booking_id, form = pipeline_rows.submitted_outcome(fields)
        if not live_lead(lead_id):
            return lead_gone()
        try:
            with units.open():
                saved = pipeline.operator_record_call_outcome(lead_id, booking_id, form)
        except NotThisLeadsBooking:  # checked before the fields; nothing was written
            raise MalformedForm(WRONG_FIELDS) from None
        except OutcomeRefused as refused:  # nothing was written; the form keeps what was typed
            return pipeline_answer(
                request,
                lead_id,
                pipeline_rows.outcome_refused(),
                typed_outcome=form,
                outcome_refusal=refused,
                typed_booking_id=booking_id,
            )
        answer = pipeline_answer(request, lead_id, pipeline_rows.outcome_saved(saved))
        answer.headers["HX-Trigger"] = pipeline_rows.OUTCOME_SAVED_EVENT  # refresh the due lines
        return answer

    @app.post("/leads/{lead_id}/rescore-current", name="lead_rescore_current")
    @personal_data(True)  # the Lead page's panel; a Lead's name can be a sole trader's name
    def lead_rescore_current(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        lead_rows.submitted_rescore(fields)
        with units.open():
            requested = lead_scores.request_rescore_current(lead_id)
        return rescore_answer(request, lead_id, requested, lead_rows.rescore_current_queued())

    @app.post("/leads/{lead_id}/primary", name="lead_make_primary")
    @personal_data(True)  # the panel shows Contacts, who are people
    def lead_make_primary(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        contact_id = lead_rows.submitted_contact(fields)
        notice = lead_rows.primary_set()
        try:
            with units.open():
                primary_contacts.make_primary(lead_id, contact_id)
        except PrimaryRefused as refused:  # nothing was written
            if refused.reason is PrimaryRefusal.UNKNOWN_LEAD:  # never echoes the id
                text = lead_rows.REFUSALS[PrimaryRefusal.UNKNOWN_LEAD]
                return PlainTextResponse(text, status_code=404, headers=NO_STORE)
            notice = lead_rows.primary_refused(refused)
        with units.open(read_only=True):  # 200, so HTMX swaps the panel in
            contacts = research_queries.contacts(lead_id)
            # live on every render, never stored (FR-18, AD-16)
            decisions = {each.id: compliance.evaluate_eligibility(each.id) for each in contacts}
            context = lead_rows.contacts_context(
                lead_id,
                contacts,
                research_queries.lead_provenance(lead_id),
                decisions,
                lead_markets(),
                research_queries.no_decision_maker(lead_id),
                notice,
                erasures=lead_erasures(contacts),
                receipts=lead_receipts(contacts),
            )
            return page(request, lead_rows.CONTACTS_TEMPLATE, **context)

    @app.post("/leads/{lead_id}/find-email", name="lead_find_email")
    @personal_data(True)  # the panel shows Contacts, who are people
    def lead_find_email(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        contact_id = lead_rows.submitted_contact(fields)
        if email_finding is None:
            return PlainTextResponse(
                "Finding email addresses isn't set up.", status_code=503, headers=NO_STORE
            )
        with units.open():
            result = email_finding.queue_search(lead_id, contact_id)
        if result is SearchQueued.UNKNOWN_LEAD:  # never echoes the id
            text = lead_rows.REFUSALS[PrimaryRefusal.UNKNOWN_LEAD]
            return PlainTextResponse(text, status_code=404, headers=NO_STORE)
        notice = lead_rows.search_answer(str(result))
        with units.open(read_only=True):  # 200, so HTMX swaps the panel in
            contacts = research_queries.contacts(lead_id)
            # live on every render, never stored (FR-18, AD-16)
            decisions = {each.id: compliance.evaluate_eligibility(each.id) for each in contacts}
            context = lead_rows.contacts_context(
                lead_id,
                contacts,
                research_queries.lead_provenance(lead_id),
                decisions,
                lead_markets(),
                research_queries.no_decision_maker(lead_id),
                notice,
                erasures=lead_erasures(contacts),
                receipts=lead_receipts(contacts),
            )
            return page(request, lead_rows.CONTACTS_TEMPLATE, **context)

    require_declared(app)
    require_route_rules(app)
    app.add_middleware(Guard, port=port, sessions=sessions, locked_page=locked_page)
    return app
