## Deployment Checklist

Target: internet-facing production deployment for this Flask app.

### Required before go-live

- Set a real `FLASK_SECRET_KEY` in the production environment.
- Set `SESSION_COOKIE_SECURE=1` in production.
- Keep `SESSION_COOKIE_SAMESITE=Lax` unless a cross-site flow requires otherwise.
- Do not expose the Flask development server directly to the internet.
- Run the app behind HTTPS only.
- Put a reverse proxy in front of the app.
- Disable any development-only browser-opening behavior in the production start command.
- Ensure the database is not publicly reachable except from the app host/network.
- Keep `.env` out of git and manage production secrets outside the repository.

### Python and app runtime

- Use a production WSGI server such as `waitress` on Windows or `gunicorn` on Linux.
- Run the Flask app as an imported application object, not via `python app.py` in production.
- This repo now includes [wsgi.py](/c:/Dev/a4_inventory_system/wsgi.py) and [run_waitress.py](/c:/Dev/a4_inventory_system/run_waitress.py) for that purpose.
- Set up service supervision so the process restarts on failure.
- Capture structured application logs.

### Reverse proxy / network

- Terminate TLS at the proxy.
- Redirect HTTP to HTTPS.
- Preserve forwarded headers correctly.
- Restrict request body size at the proxy as well as in Flask.
- Add IP-based rate limiting on login and sensitive endpoints.

### Session and auth

- Verify production uses the intended `FLASK_SECRET_KEY`.
- Verify session cookies are marked `Secure`, `HttpOnly`, and appropriate `SameSite`.
- Verify CSRF failures return a safe response and are logged.
- Consider moving login throttling to shared storage if you will run multiple app instances.

### Database and secrets

- Use a dedicated production database user with least privilege.
- Rotate the current database password before production if it has been used in development/shared environments.
- Back up the database before go-live and test restore procedures.
- Store DB credentials in environment variables or a secrets manager, not in tracked files.

### App-specific checks

- Review every route listed in [ACCESS_CONTROL.md](/c:/Dev/a4_inventory_system/ACCESS_CONTROL.md) and confirm the staff/admin split is intentional.
- Finish the remaining `innerHTML`-based DOM XSS cleanup in [SECURITY_AUDIT.md](/c:/Dev/a4_inventory_system/SECURITY_AUDIT.md).
- Validate all date query params on reporting routes.
- Confirm export endpoints are intended to be staff-accessible before internet exposure.
- Decide whether debt and cash pages should be staff-visible or admin-only.

### Operational checks

- Important: hosting platform is still undecided, and the Payables cheque reminder system now depends on a production scheduler.
- Important: before go-live, choose a hosting platform that supports a daily scheduled task / cron-style job.
- Important: schedule `python scripts/run_payables_reminders.py` to run once daily at 8:00 AM Asia/Manila so 7-day and due-today cheque notifications are created without requiring a page visit.
- Turn on monitoring for:
  - application errors
  - repeated login failures
  - CSRF failures
  - unusual export volume
- Document deployment and rollback steps.
- Test with separate admin and staff accounts after deployment.

### Recommended production env baseline

```env
FLASK_SECRET_KEY=<long-random-secret>
SESSION_COOKIE_SECURE=1
SESSION_COOKIE_SAMESITE=Lax
SESSION_LIFETIME_HOURS=12
MAX_CONTENT_LENGTH_MB=16
DB_HOST=<prod-db-host>
DB_PORT=5432
DB_NAME=<prod-db-name>
DB_USER=<prod-db-user>
DB_PASSWORD=<prod-db-password>
```

### Final verification before launch

1. Staff cannot access admin routes by direct URL.
2. Login, logout, and all POST/DELETE actions still work with CSRF enabled.
3. HTTPS is enforced.
4. Production server is using [wsgi.py](/c:/Dev/a4_inventory_system/wsgi.py) or [run_waitress.py](/c:/Dev/a4_inventory_system/run_waitress.py), not Flask’s built-in dev server.
5. Backup and restore have been tested.
