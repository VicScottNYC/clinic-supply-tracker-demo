# P1 web interface (Vite + React + TypeScript)

Screens: Inventory, Receive supplies, Issue supplies, Counts, History. Transfers, holds, approvals and user
administration are deferred to P2.

## Run locally

Terminal 1, from the project folder (this uses the demo database created by the loader, see below):

    cd demo_data/p1db
    P1_DB_PATH=~/clinic-demo/northbridge-demo.db P1_CLOCK=fixed ../../.venv/bin/uvicorn api_p1:make_app --factory --port 8000

Terminal 2:

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173. The browser calls `/api/...`, which Vite forwards to the API on port 8000.

## Launch modes

- `P1_CLOCK=fixed`: the approved evaluation timestamp (2026-10-18 09:00 New York). The header shows a DEMO DATE notice.
- `P1_CLOCK=current`: real time, labelled Live clock.

## Create the Northbridge demo database (new file only)

    mkdir -p ~/clinic-demo
    cd demo_data/p1db
    ../../.venv/bin/python loader_p1.py ~/clinic-demo/northbridge-demo.db

To start over, choose a new file name. The loader never overwrites an existing database. Keep the smoke-test
database separate.

## Checks

    cd frontend
    npm run typecheck
    npm test
    npm run build

## Browser checks (need the API running; each script states the database it expects)

Install with `npm ci` so the committed `package-lock.json` is used exactly.

    cd frontend
    npm ci
    CHROME=/path/to/chromium node e2e/browser-walk.mjs      # fresh fixed-date database, every screen
    CHROME=/path/to/chromium node e2e/recovery-cases.mjs    # after the walk, same database
    # then restart the API with P1_CLOCK=current on a new database:
    CHROME=/path/to/chromium node e2e/current-clock.mjs     # live-clock walkthrough

`browser-walk.mjs` walks every screen, including a dropped connection whose retry reuses its key. Two console
errors are expected in that run: the deliberate aborted request and the refused issue into the locked area.

`recovery-cases.mjs` uses fixed lot and packing-slip names. Run it on a fresh database for a clean record. It checks
the request the browser actually sent:

- a structured 503 response (JSON error body) is treated as an unknown outcome, and retry resends the same key,
  actor, and body;
- after a dropped response, changing screen keeps the pending write, clinic and actor are locked, and retry
  sends the original request after the form has been rebuilt;
- the server commits a receipt and the response is lost; retry returns the committed receipt and stock rises once.

Screenshots go to ./e2e-shots (or $SHOTS). `verification/` holds screenshots from the last passing run.

## Notes

- "Demo actor" is a selector for demonstration, not a login.
- Submission keys: each intended action gets one key. Retrying the unchanged request reuses it. Any 5xx response
  (structured or not), and any lost connection, is an unknown outcome.
- Known limitation: the pending-write store is in memory only. Changing screen or clinic is handled; a browser
  reload or closing the tab is not, and the outcome then has to be checked by hand (the app shows a warning while
  a write is unresolved). Durable recovery is a later improvement.
- One pending-write store sits above the screens (`src/pending.tsx`). It keeps the original actor, endpoint,
  payload and key. Retry resends that stored request, not the form. While a write is pending or unknown, clinic
  and actor cannot change and other submissions are not offered.
- Expiration: required for products that track expiry. For an existing lot it must equal the recorded date.
- Unit conversions come from the opening file (`unit_conversions`). P-002 "case" = 12 boxes is labelled as a
  demo packaging assumption, not a supplier specification.

## Accessibility and presentation

See `ACCESSIBILITY.md` for method, exact coverage, findings and the VoiceOver checklist. `axe-core` is a pinned
devDependency, so `npm ci` installs it with everything else.

    CHROME=/path/to/chromium node e2e/a11y-audit.mjs      # axe, 5 states x 5 screens; exits 1 on findings
    CHROME=/path/to/chromium node e2e/keyboard-check.mjs  # keyboard-only workflows; run on a FRESH database
