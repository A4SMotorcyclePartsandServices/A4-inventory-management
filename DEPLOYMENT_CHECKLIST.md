## Deployment Checklist

Target: Railway soft deployment tomorrow for internal testing, with planned client release next Monday.

### Current repo status

- Done: production-style entrypoints exist in [wsgi.py](/c:/Dev/a4_inventory_system/wsgi.py) and [run_waitress.py](/c:/Dev/a4_inventory_system/run_waitress.py).
- Done: duplicate protection is enforced at the database level.
- Done: SQL injection hardening was completed in the runtime app code.
- Done: access control was reviewed and documented in [ACCESS_CONTROL.md](/c:/Dev/a4_inventory_system/ACCESS_CONTROL.md).
- Done: report date validation was tightened.
- Done: login throttling is now database-backed with 5-day cleanup.
- Done: the main DOM/XSS hotspots were reduced to low residual risk in [SECURITY_AUDIT.md](/c:/Dev/a4_inventory_system/SECURITY_AUDIT.md).
- Done: Railway-compatible app startup is ready through [run_waitress.py](/c:/Dev/a4_inventory_system/run_waitress.py).

### Tomorrow Soft Deploy

Goal:
- get the app running on a Railway-generated URL
- use it for your own testing and issue-finding
- do not treat it as final public release yet

#### Before Creating The Railway Project

- [ ] Confirm the soft deploy target is Railway.
- [ ] Use the Railway domain only for tomorrow.
- [ ] Keep the client custom domain for next week.
- [ ] Generate a fresh production `FLASK_SECRET_KEY`.
- [ ] Prepare a production DB password that is different from local development.
- [ ] Keep the local `.env` values out of Railway unless they are intentionally reused.
- [ ] Decide whether tomorrow will run as one web instance only.
- [ ] Make sure the latest schema changes are committed to the branch you plan to deploy.

#### Production Environment Variables

Set these in Railway service variables, not in the repo:

```env
FLASK_SECRET_KEY=<long-random-secret>
SESSION_COOKIE_SECURE=1
SESSION_COOKIE_SAMESITE=Lax
SESSION_LIFETIME_HOURS=12
MAX_CONTENT_LENGTH_MB=16
DB_HOST=<railway-db-host>
DB_PORT=5432
DB_NAME=<railway-db-name>
DB_USER=<railway-db-user>
DB_PASSWORD=<railway-db-password>
DB_POOL_MIN=1
DB_POOL_MAX=10
APP_THREADS=8
```

Notes:

- Railway injects `PORT` automatically, and [run_waitress.py](/c:/Dev/a4_inventory_system/run_waitress.py) honors it.
- `SESSION_COOKIE_SECURE=1` should stay enabled in production.
- Start with one small instance and modest DB pool settings.

#### Railway Project Setup

- [ ] Create a new Railway project.
- [ ] Add a PostgreSQL service.
- [ ] Add a web service connected to the deployment branch/repo.
- [ ] Set the start command to `python run_waitress.py`.
- [ ] Confirm dependencies install from [requirements.txt](/c:/Dev/a4_inventory_system/requirements.txt).
- [ ] Add all production environment variables to the web service.
- [ ] Point the app at the Railway PostgreSQL credentials.
- [ ] Deploy once and confirm the app boots without startup errors.
- [ ] Confirm the latest DB schema additions create successfully, including `login_attempts`.

#### Railway Scheduled Job

The payables cheque reminders depend on a daily scheduled task.

- [ ] Add a scheduled job / cron service in Railway.
- [ ] Set the command to `python scripts/run_payables_reminders.py`.
- [ ] Schedule it for 8:00 AM Asia/Manila.
- [ ] If Railway cron uses UTC, set the schedule to the UTC equivalent.
- [ ] Confirm the job can connect to the same production database variables.

#### Soft Deploy Smoke Test

- [ ] Open the Railway-generated HTTPS URL.
- [ ] Confirm logged-out users are redirected to login for protected pages.
- [ ] Confirm login works with a production admin account.
- [ ] Confirm login works with a production staff account.
- [ ] Confirm staff cannot reach admin-only pages by direct URL.
- [ ] Confirm Bundles tab works.
- [ ] Confirm Loyalty tab and search work.
- [ ] Confirm inventory search and recent chips look correct.
- [ ] Confirm cash ledger loads without backend errors.
- [ ] Confirm sales entry works.
- [ ] Confirm inventory updates work.
- [ ] Confirm customer and debt flows work.
- [ ] Confirm reports and exports load successfully.
- [ ] Confirm PDF report pages render correctly.
- [ ] Confirm file uploads still work under the configured max size.

#### Soft Deploy Safety Checks

- [ ] Verify production uses the intended `FLASK_SECRET_KEY`.
- [ ] Verify session cookies are `Secure`, `HttpOnly`, and `SameSite=Lax`.
- [ ] Verify HTTPS is active on the Railway URL.
- [ ] Confirm the app is started with [run_waitress.py](/c:/Dev/a4_inventory_system/run_waitress.py), not `python app.py`.
- [ ] Ensure the database is not exposed beyond what Railway requires.
- [ ] Write down the Railway service URL, DB service name, and env var set used.
- [ ] Take an initial production backup after the first stable deploy.
- [ ] Document the rollback step: redeploy the previous successful Railway deployment.

### Before Monday Release

Goal:
- use the soft-deployed app to iron out issues
- only treat next Monday as the actual client-ready release checkpoint

#### Stability Pass

- [ ] Fix any issues found during live Railway testing.
- [ ] Re-test the highest-risk areas after each fix.
- [ ] Keep changes in `dev` first, then promote to `production` only after verification.

#### Operations And Monitoring

- [ ] Review Railway logs daily during the soft-deploy period.
- [ ] Turn on error monitoring or at least define a log-check routine.
- [ ] Watch Railway usage so the client has a realistic cost expectation.
- [ ] Confirm the scheduled reminders job actually runs and creates expected results.

#### Final Release Gate

- [ ] Re-run admin/staff direct-URL access tests.
- [ ] Re-run login throttling behavior once on the live environment.
- [ ] Confirm backup/restore confidence.
- [ ] Decide whether to add the client custom domain before release or shortly after.
- [ ] If adding the domain, re-check cookies, HTTPS, and login flow on the final domain.
