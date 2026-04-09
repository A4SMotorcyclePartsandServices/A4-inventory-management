# Refresh local PostgreSQL from Railway production

Use this when you want your local database to match the current Railway production database without doing the dump/drop/restore steps manually.

## Command

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\refresh_local_from_railway.ps1 -Force
```

## Shortcut wrapper

```bat
.\scripts\refresh-prod-db.bat
```

This runs the PowerShell script with `-Force` already included.

## What it does

1. Reads your local destination database from `.env`
2. Pulls production database credentials from Railway CLI
3. Creates a fresh production dump
4. Drops and recreates your local database
5. Restores the production dump into local
6. Keeps only the 5 newest `prod_refresh_*.dump` files in `tmp\`

## Requirements

- Railway CLI installed and logged in
- Repo linked to the correct Railway project
- PostgreSQL tools installed locally: `pg_dump`, `pg_restore`, `dropdb`, `createdb`
- Local `.env` should point to the database you want overwritten

If your Railway Postgres variables are sealed, `railway variable list` may return nothing. In that case, copy `DATABASE_URL` from the Railway Postgres service Variables tab and pass it directly with `-ProdDatabaseUrl`.

## One-time setup for sealed Railway variables

1. Copy [`.env.railway.local.example`](c:/Dev/a4_inventory_system/.env.railway.local.example) to `.env.railway.local`
2. Paste your Railway Postgres `DATABASE_URL` as `PROD_DATABASE_URL=...`
3. Run the normal refresh command

`.env.railway.local` is ignored by git, so this stays only on your machine.

## Useful options

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\refresh_local_from_railway.ps1
```

Runs with a confirmation prompt before deleting the local database.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\refresh_local_from_railway.ps1 -Force -DryRun
```

```bat
.\scripts\refresh-prod-db.bat -DryRun
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\refresh_local_from_railway.ps1 -Force -ProdDatabaseUrl "<paste-railway-database-url-here>"
```

Use this when the Railway CLI cannot read Postgres variables because they are sealed.

```bat
.\scripts\refresh-prod-db.bat
```

If `.env.railway.local` exists with `PROD_DATABASE_URL`, the wrapper will use it automatically.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\refresh_local_from_railway.ps1 -Force -KeepDumpCount 5
```

This is the default retention behavior. Older refresh dump files are deleted after a successful run.

Prints the commands it would run without touching either database.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\refresh_local_from_railway.ps1 -Force -RailwayService postgres
```

Useful if the production database variables live on a specific Railway service.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\refresh_local_from_railway.ps1 -Force -RailwayEnvironment production
```

Explicitly targets Railway's `production` environment. This is already the default.

## Notes

- The script uses `.env` for local `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`.
- The script also checks `.env.railway.local` for `PROD_DATABASE_URL`, `RAILWAY_PROD_DATABASE_URL`, or `DATABASE_URL`.
- The local database is destroyed and recreated each time.
- Production access is read-only from the script's side. Your local database is the only destructive target.
- Dump files are stored under `tmp\` so you can inspect or reuse them if needed.
- After a successful run, the script keeps only the latest 5 refresh dump files by default.
