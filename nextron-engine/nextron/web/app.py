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
- ``GET /`` (``home``): the base layout.
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
  which the base layout loads once per page view (``hx-trigger="load"``; it isn't polled), and
  its Dismiss button. While recovery mode is on (``queries.ops.recovery_mode()``, Story 1.19),
  the area (``notifications.html``) starts with the banner ``<p class="recovery-banner"
  role="alert">``, ``RECOVERY_BANNER`` linked to the health panel; every page shows it, because
  every page loads the area. Then it shows "Engine health: <n> error
  needs attention" (or "<n> errors need attention"), linked to the health panel, when
  ``health.issue_count()`` is above 0. It is empty when nothing is undismissed and no error
  needs attention; otherwise it shows "Notifications (<count>)", the newest ``AREA_LIMIT`` (5),
  each with its text (``|plain``), its time as ``YYYY-MM-DD HH:MM UTC`` and a Dismiss form, and
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
- ``POST /inboxes/connect`` (``inbox_connect``), ``POST /inboxes/reconnect``
  (``inbox_reconnect``) and ``GET /inboxes/connect`` (``inbox_connect_status``), all
  ``@personal_data(True)``: connecting a Sending Inbox through Gmail OAuth (Story 6.2, FR-32,
  AD-12). Only the two POSTs (CSRF-checked) start a consent flow, through
  ``inbox_connections`` (``InboxConnections.start_connect(address, label)``, fields exactly as
  ``POST /inboxes``; ``start_reconnect(inbox_id)``, exactly one ``inbox_id`` ULID), which open
  their own units of work; then the panel is rendered again in a read-only one, with the
  connect status or the ``ConnectRefused`` message (and, for Connect inbox, the typed values).
  The GET renders the in-memory connect status alone (``inbox_connect_status.html``), which
  HTMX polls every 2 s while a flow waits; it starts nothing.
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
  ``queries.scoring.stored`` (Story 2.15: the Score panel), with one "Export data (JSON)" form
  per Contact (``export_page.lead_forms``, Story 3.15). The list also passes its ``sort`` to
  ``lead_list``.
- ``POST /leads/{lead_id}/rescore`` (``lead_rescore``, Story 2.15, ``@personal_data(True)``):
  the Score panel's "Re-score". The form holds nothing but the CSRF token
  (``leads_page.submitted_rescore``). It runs ``lead_scores.request_rescore(lead_id)`` in its
  own unit of work, which queues ``score_lead`` on the interactive lane, then answers 200 with
  the Score panel (``lead_score.html``, read in a read-only unit of work) and the notice
  "Re-score queued."; a merged or closed Lead queues nothing and the panel carries the refusal
  notice (``leads_page.rescore_refused``). An unknown Lead (a text that isn't a ULID
  included) answers 404 with the plain text "That Lead doesn't exist.", which never echoes the
  id.
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
- ``POST /leads/{lead_id}/stop-sequence`` (``lead_stop_sequence``, Story 6.9,
  ``@personal_data(True)``): the Lead page's Stop Sequence panel (``lead_sequence.html``). The
  form is empty (the first click) or exactly ``confirm=yes`` (``leads_page.submitted_stop``;
  anything else is ``MalformedForm``, 400). An unknown Lead (a text that isn't a ULID
  included) answers 404 with "That Lead doesn't exist.", which never echoes the id. The first
  click writes nothing and answers 200 with the panel asking for the confirmation (or, when the
  Lead no longer has a live Enrollment, the "This Lead has no live Sequence." notice). With
  ``confirm=yes`` it runs ``sequencing.stop_sequence(lead_id)`` in its own unit of work
  (``Sequencing.stop(cause=operator_stop, lead_id=...)``, which cancels the stopped
  Enrollment's ``ready`` Drafts; Stories 5.2 and 6.10 add ``sending.cancel`` for its
  ``queued`` OMs), then answers 200 with the panel, read in a read-only unit of work, and the
  notice "Sequence stopped. Its ready Drafts were cancelled; approved Drafts are blocked from
  sending." or, when nothing was live, "This Lead has no live Sequence.". The Lead page shows
  the panel only while the Lead has a live Enrollment (``queries.sequencing.live_enrollment``).

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

from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.discovery.api import ImportRequest, ImportStopped, LeadImports
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Backups, Budget, DashboardEntry, EngineHealth, RestoreTests
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
from nextron.app.research.extraction import LeadSignals
from nextron.app.research.finding import EmailFinding, SearchQueued
from nextron.app.research.people import PrimaryContacts
from nextron.app.scoring.lead_scores import LeadScores, RescoreRequest
from nextron.app.sending.api import Sending
from nextron.app.sequencing.api import Sequencing
from nextron.app.sending.checks import DomainChecks
from nextron.app.sending.connect import InboxConnections
from nextron.app.setup.api import (
    Campaigns,
    Credentials,
    CredentialStoreFailed,
    GoogleAccess,
    GrantStatus,
    Setup,
)
from nextron.domain.campaign import CampaignRefused, InvalidCampaigns
from nextron.domain.company_facts import InvalidFact
from nextron.domain.contacts import PrimaryRefusal, PrimaryRefused
from nextron.domain.data_subject_request import check_request_ref
from nextron.domain.credentials import InvalidCredentialValue
from nextron.domain.google_oauth import ClientNotImported, InvalidClientFile
from nextron.domain.ids import is_ulid
from nextron.domain.lead_import import ImportRefused
from nextron.domain.markets import InvalidMarkets
from nextron.domain.offers import InvalidOffers
from nextron.domain.open_code import CodeCheck
from nextron.domain.playbook import SyncTrigger
from nextron.domain.prompts import InvalidPrompts
from nextron.domain.recovery import RECOVERY_BANNER
from nextron.domain.render import footer_complete
from nextron.domain.segments import InvalidSegments
from nextron.domain.sending_inbox import ConnectRefused, InboxRefused, WarmupRefused
from nextron.domain.sequences import InvalidSequences
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
from nextron.domain.unsub_token import UnsubKeyInUse
from nextron.web import ai_content_page as ai_rows
from nextron.web import campaigns_page as campaign_rows
from nextron.web import credentials_page as credential_rows
from nextron.web import erase_page as erase_rows
from nextron.web import export_page as export_rows
from nextron.web import facts_page as fact_rows
from nextron.web import google_page as google_rows
from nextron.web import health_page as health_rows
from nextron.web import inboxes_page as inbox_rows
from nextron.web import lead_import_page as import_rows
from nextron.web import leads_page as lead_rows
from nextron.web import pipeline_page as board_rows
from nextron.web import quotas_page as quota_rows
from nextron.web import suppression_page as suppression_rows
from nextron.web import templating
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


def health_count_text(errors: int) -> str | None:
    """The area's engine health line for ``errors`` errors, or ``None`` when there are none."""
    if errors <= 0:
        return None
    if errors == 1:
        return "Engine health: 1 error needs attention"
    return f"Engine health: {errors:,} errors need attention"


def area_context(
    found: notify_queries.OpenNotifications, *, errors: int = 0, recovery: bool = False
) -> dict[str, object]:
    """What ``notifications.html`` renders: the recovery banner while ``recovery`` mode is on,
    the engine health line, the count, the listed items and how many more."""
    items = [
        NotificationItem(row.id, row.text, f"{row.created_at:{TIME_FORMAT}}")
        for row in found.newest
    ]
    return {
        "recovery": recovery,
        "RECOVERY_BANNER": RECOVERY_BANNER,
        "health_text": health_count_text(errors),
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
    inbox_connections: InboxConnections,
    lead_signals: LeadSignals,
    compliance: Compliance,
    primary_contacts: PrimaryContacts,
    erasures: Erasures,
    domain_checks: DomainChecks,
    lead_scores: LeadScores,
    sequencing: Sequencing,
    units: Units,
    email_finding: EmailFinding | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    lifespan: Lifespan | None = None,
) -> FastAPI:
    """Build the dashboard app for ``127.0.0.1:<port>``; its GET handlers read through
    ``units.open(read_only=True)`` and its POST handlers that write the database open
    ``units.open()``. ``setup`` stores the settings, ``campaigns`` runs the Settings page's
    Campaign controls (activate, pause, resume, save the LIA), ``facts`` the Approved Company Facts,
    ``playbook`` the AI content view's "Check for changes" with the inputs ``ai_content`` loads
    and its rollback, ``test_runs`` queues the test runs of new tuples after a check,
    ``credentials`` the external credentials and ``google`` the Google client and grants (both
    in Credential Manager, not the database);
    ``notify`` dismisses notifications; ``budget`` gives the Quotas and cost page its figures;
    ``health`` gives the health panel its report and the notification area its error count;
    ``queue`` dismisses a dead job; ``backups`` queues the health panel's "Back up now" and
    ``restore_tests`` its "Run restore test now"; ``lead_imports`` runs the CSV Lead import;
    ``sending`` registers the Sending Inboxes panel's inboxes and ``inbox_connections`` connects
    them through Gmail OAuth (Story 6.2); ``lead_signals`` gives the Lead
    page its Signals, Evidence and headquarters zone, ``primary_contacts`` runs its "Make
    primary" and ``email_finding`` its "Find email" (Story 2.13; without one, "Find email"
    answers 503) and ``lead_scores`` its "Re-score" (Story 2.15); ``compliance`` runs the
    Suppression List panel's add, import and Reverse, and
    evaluates each Contact's live Eligibility on the Lead page and its Contacts panel (that only
    reads); ``erasures`` erases a Contact from the Lead page's erase step and answers its
    subject access export; ``domain_checks`` reads the Sending Domain authentication section
    and queues its Recheck; ``sequencing`` runs the Lead page's Stop Sequence (Story 6.9).
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
        return page(request, "home.html")

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
        return page(request, PAGE_TEMPLATE, **context, **panel)

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
        except UnsubKeyInUse as in_use:
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
        except UnsubKeyInUse as in_use:
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
        context = area_context(
            found, errors=engine_health.issue_count(), recovery=ops_queries.recovery_mode()
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
        context = quota_rows.page_context(budget.status())
        return page(request, quota_rows.PAGE_TEMPLATE, **context)

    def health_answer(
        request: Request,
        template: str,
        *,
        notice: str | None = None,
        restore_notice: str | None = None,
    ) -> HTMLResponse:
        context = health_rows.panel_context(
            engine_health.report(),
            notice=notice,
            restore_notice=restore_notice,
            inboxes=sending_queries.inboxes(),  # Story 6.2: the inbox status list
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

    def inboxes_answer(
        request: Request,
        template: str,
        *,
        notice: credential_rows.Notice | None = None,
        address: str = "",
        label: str = "",
        typed_warmup: inbox_rows.WarmupForm | None = None,
        connect_refused: ConnectRefused | None = None,
    ) -> HTMLResponse:
        context = inbox_rows.panel_context(
            sending_queries.inboxes(),
            caps=sending.inbox_caps().caps,
            notice=notice,
            address=address,
            label=label,
            typed_warmup=typed_warmup,
            connect=inbox_connections.status(),
            connect_refused=connect_refused,
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

    @app.post("/inboxes/connect", name="inbox_connect")
    @personal_data(True)  # answers with the whole panel, which shows inbox addresses
    def inbox_connect(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        address, label = inbox_rows.submitted_inbox(fields)
        try:
            # the only call that starts a consent flow for a new address; it opens its own
            # units of work, so none is open here
            inbox_connections.start_connect(address, label)
        except ConnectRefused as refused:  # nothing started; the typed values stay
            with units.open(read_only=True):
                return inboxes_answer(
                    request,
                    inbox_rows.PANEL_TEMPLATE,
                    address=address,
                    label=label,
                    connect_refused=refused,
                )
        with units.open(read_only=True):
            return inboxes_answer(request, inbox_rows.PANEL_TEMPLATE)

    @app.post("/inboxes/reconnect", name="inbox_reconnect")
    @personal_data(True)  # answers with the whole panel, which shows inbox addresses
    def inbox_reconnect(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        inbox_id = inbox_rows.submitted_reconnect(fields)
        refused: ConnectRefused | None = None
        try:
            inbox_connections.start_reconnect(inbox_id)  # opens its own units of work
        except ConnectRefused as caught:
            refused = caught
        with units.open(read_only=True):
            return inboxes_answer(request, inbox_rows.PANEL_TEMPLATE, connect_refused=refused)

    @app.get("/inboxes/connect", name="inbox_connect_status")
    @personal_data(True)  # the waiting notice names the inbox's address
    def inbox_connect_status(request: Request) -> HTMLResponse:
        # reads the in-memory flow status only: no GET starts a consent flow
        return page(
            request,
            inbox_rows.CONNECT_TEMPLATE,
            **inbox_rows.connect_context(inbox_connections.status()),
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
        (Story 4.12), oldest Enrollment first, each by Step."""
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
            score=scoring_queries.stored(found.id),
            drafts=lead_drafts(found.id),
        )
        return page(
            request,
            lead_rows.LEAD_TEMPLATE,
            **context,
            **lead_rows.sequence_context(
                sequencing_queries.live_enrollment(found.id) is not None
            ),
            export_forms=export_rows.lead_forms(contacts),
        )

    @app.post("/leads/{lead_id}/rescore", name="lead_rescore")
    @personal_data(True)  # the Lead page's panel; a Lead's name can be a sole trader's name
    def lead_rescore(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        lead_rows.submitted_rescore(fields)
        with units.open():
            requested = lead_scores.request_rescore(lead_id)
        if requested is RescoreRequest.UNKNOWN:  # never echoes the id
            text = lead_rows.REFUSALS[PrimaryRefusal.UNKNOWN_LEAD]
            return PlainTextResponse(text, status_code=404, headers=NO_STORE)
        if requested is RescoreRequest.QUEUED:
            notice = lead_rows.rescore_queued()
        else:  # nothing was queued
            notice = lead_rows.rescore_refused(requested is RescoreRequest.MERGED)
        with units.open(read_only=True):  # 200, so HTMX swaps the panel in
            found = discovery_queries.lead(lead_id)
            seeded = None if found is None else setup_queries.segment_by_id(found.segment_id)
            context = lead_rows.score_context(
                lead_id,
                scoring_queries.stored(lead_id),
                None if seeded is None else seeded.definition,
                notice,
                live=frozenset(row.id for row in research_queries.evidence(lead_id)),
            )
            return page(request, lead_rows.SCORE_TEMPLATE, **context)

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

    @app.post("/leads/{lead_id}/stop-sequence", name="lead_stop_sequence")
    @personal_data(True)  # the Lead page's panel; a Lead's name can be a sole trader's name
    def lead_stop_sequence(
        request: Request, lead_id: str, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> Response:
        confirmed = lead_rows.submitted_stop(fields)
        with units.open(read_only=True):
            known = discovery_queries.lead(lead_id) is not None
        if not known:  # never echoes the id
            text = lead_rows.REFUSALS[PrimaryRefusal.UNKNOWN_LEAD]
            return PlainTextResponse(text, status_code=404, headers=NO_STORE)
        notice = None
        if confirmed:
            with units.open():
                stopped = sequencing.stop_sequence(lead_id)
            notice = lead_rows.sequence_stopped() if stopped else lead_rows.no_live_sequence()
        with units.open(read_only=True):  # 200, so HTMX swaps the panel in
            live = sequencing_queries.live_enrollment(lead_id) is not None
            if not confirmed and not live:  # ended before the first click: say so, never blank
                notice = lead_rows.no_live_sequence()
            context = lead_rows.sequence_context(live, notice, confirming=not confirmed)
            return page(request, lead_rows.SEQUENCE_TEMPLATE, lead_id=lead_id, **context)

    require_declared(app)
    require_route_rules(app)
    app.add_middleware(Guard, port=port, sessions=sessions, locked_page=locked_page)
    return app
