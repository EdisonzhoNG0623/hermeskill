# Dev DB bootstrap for the Windows machine (Postgres 18 already installed).
#
# Creates the `stasis` role + `stasis` database. Re-runnable. Requires
# `psql` on PATH and a Postgres superuser to authenticate (the script uses
# Windows-auth `-U postgres` by default).
#
# Usage:
#   .\deploy\dev-db-bootstrap.ps1                  # uses password 'stasis'
#   .\deploy\dev-db-bootstrap.ps1 -Password 'xyz'  # custom

param(
    [string]$Password = "stasis",
    [string]$SuperuserName = "postgres"
)

$ErrorActionPreference = "Stop"

$sql = @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stasis') THEN
    CREATE ROLE stasis LOGIN PASSWORD '$Password';
  ELSE
    ALTER ROLE stasis WITH PASSWORD '$Password';
  END IF;
END
`$`$;
"@

Write-Output ">>> ensuring 'stasis' role"
$sql | & psql -U $SuperuserName -d postgres

Write-Output ">>> ensuring 'stasis' database"
$dbExists = & psql -U $SuperuserName -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='stasis'"
if (-not $dbExists) {
    & createdb -U $SuperuserName -O stasis stasis
}

Write-Output ">>> done. connection string:"
Write-Output "postgresql+psycopg://stasis:$Password@localhost:5432/stasis"
