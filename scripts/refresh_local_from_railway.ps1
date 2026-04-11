param(
    [string]$EnvFile = ".env",
    [string]$SecretsEnvFile = ".env.railway.local",
    [string]$RailwayEnvironment = "production",
    [string]$RailwayService,
    [string]$DumpPath,
    [string]$ProdDatabaseUrl,
    [int]$KeepDumpCount = 5,
    [switch]$Force,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-CommandPath {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Required command '$Name' was not found in PATH."
    }

    return $command.Source
}

function Read-DotEnvFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Env file '$Path' was not found."
    }

    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }

        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()

        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        $values[$key] = $value
    }

    return $values
}

function Get-RequiredValue {
    param(
        [hashtable]$Values,
        [string]$Key,
        [string]$SourceName
    )

    if (-not $Values.ContainsKey($Key) -or [string]::IsNullOrWhiteSpace($Values[$Key])) {
        throw "Missing required key '$Key' in $SourceName."
    }

    return $Values[$Key]
}

function Get-OptionalValue {
    param(
        [hashtable]$Values,
        [string[]]$Keys
    )

    foreach ($key in $Keys) {
        if ($Values.ContainsKey($key) -and -not [string]::IsNullOrWhiteSpace($Values[$key])) {
            return $Values[$key]
        }
    }

    return $null
}

function Normalize-RailwayVariables {
    param([object]$Payload)

    $normalized = @{}

    if ($Payload -is [System.Collections.IDictionary]) {
        foreach ($entry in $Payload.GetEnumerator()) {
            $normalized[[string]$entry.Key] = [string]$entry.Value
        }
        return $normalized
    }

    if ($Payload -is [System.Collections.IEnumerable]) {
        foreach ($item in $Payload) {
            if ($null -eq $item) {
                continue
            }

            if ($item.PSObject.Properties.Name -contains "name") {
                $key = [string]$item.name
                $valueProp = $item.PSObject.Properties.Name | Where-Object { $_ -in @("value", "resolvedValue") } | Select-Object -First 1
                if ($key -and $valueProp) {
                    $normalized[$key] = [string]$item.$valueProp
                }
            } elseif ($item -is [System.Collections.DictionaryEntry]) {
                $normalized[[string]$item.Key] = [string]$item.Value
            }
        }
    }

    return $normalized
}

function Add-DatabaseUrlFallbacks {
    param(
        [hashtable]$Values,
        [string]$DatabaseUrl
    )

    $databaseUrl = $DatabaseUrl
    if (-not $databaseUrl) {
        $databaseUrl = Get-OptionalValue -Values $Values -Keys @("DATABASE_URL", "DATABASE_PUBLIC_URL", "POSTGRES_URL")
    }
    if (-not $databaseUrl) {
        return $Values
    }

    try {
        $uri = [System.Uri]$databaseUrl
    }
    catch {
        return $Values
    }

    if (-not $Values.ContainsKey("DB_HOST") -and $uri.Host) {
        $Values["DB_HOST"] = $uri.Host
    }
    if (-not $Values.ContainsKey("DB_PORT") -and $uri.Port -gt 0) {
        $Values["DB_PORT"] = [string]$uri.Port
    }
    if (-not $Values.ContainsKey("DB_NAME")) {
        $dbName = $uri.AbsolutePath.TrimStart("/")
        if ($dbName) {
            $Values["DB_NAME"] = $dbName
        }
    }
    if (-not $Values.ContainsKey("DB_USER") -and $uri.UserInfo) {
        $userInfoParts = $uri.UserInfo -split ":", 2
        if ($userInfoParts.Count -ge 1 -and $userInfoParts[0]) {
            $Values["DB_USER"] = [System.Uri]::UnescapeDataString($userInfoParts[0])
        }
        if (-not $Values.ContainsKey("DB_PASSWORD") -and $userInfoParts.Count -eq 2) {
            $Values["DB_PASSWORD"] = [System.Uri]::UnescapeDataString($userInfoParts[1])
        }
    }

    return $Values
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [hashtable]$EnvironmentOverrides
    )

    $rendered = @($FilePath) + $Arguments
    Write-Host ("   " + ($rendered -join " "))

    if ($DryRun) {
        return
    }

    $previous = @{}
    if ($EnvironmentOverrides) {
        foreach ($pair in $EnvironmentOverrides.GetEnumerator()) {
            $previous[$pair.Key] = [Environment]::GetEnvironmentVariable($pair.Key, "Process")
            [Environment]::SetEnvironmentVariable($pair.Key, $pair.Value, "Process")
        }
    }

    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        if ($EnvironmentOverrides) {
            foreach ($pair in $EnvironmentOverrides.GetEnumerator()) {
                [Environment]::SetEnvironmentVariable($pair.Key, $previous[$pair.Key], "Process")
            }
        }
    }
}

function New-PostgresArgs {
    param(
        [string]$DbHost,
        [string]$DbPort,
        [string]$DbUser,
        [string]$DbName
    )

    return @(
        "--host", $DbHost,
        "--port", $DbPort,
        "--username", $DbUser,
        "--dbname", $DbName
    )
}

function Remove-OldDumpFiles {
    param(
        [string]$Directory,
        [string]$CurrentDumpPath,
        [int]$KeepCount
    )

    if ($KeepCount -lt 1) {
        return 0
    }

    if (-not (Test-Path -LiteralPath $Directory)) {
        return 0
    }

    $dumpFiles = @(
        Get-ChildItem -LiteralPath $Directory -Filter "prod_refresh_*.dump" -File |
            Sort-Object LastWriteTime -Descending
    )

    if ($dumpFiles.Count -le $KeepCount) {
        return 0
    }

    $toDelete = $dumpFiles | Select-Object -Skip $KeepCount
    $removedCount = 0

    foreach ($file in $toDelete) {
        if ($file.FullName -eq $CurrentDumpPath) {
            continue
        }

        Remove-Item -LiteralPath $file.FullName -Force
        $removedCount += 1
    }

    return $removedCount
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedEnvFile = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile
} else {
    Join-Path $repoRoot $EnvFile
}
$resolvedSecretsEnvFile = if ([System.IO.Path]::IsPathRooted($SecretsEnvFile)) {
    $SecretsEnvFile
} else {
    Join-Path $repoRoot $SecretsEnvFile
}

$localEnv = Read-DotEnvFile -Path $resolvedEnvFile
$localHost = Get-RequiredValue -Values $localEnv -Key "DB_HOST" -SourceName $resolvedEnvFile
$localPort = if ($localEnv.ContainsKey("DB_PORT") -and $localEnv["DB_PORT"]) { $localEnv["DB_PORT"] } else { "5432" }
$localDatabase = Get-RequiredValue -Values $localEnv -Key "DB_NAME" -SourceName $resolvedEnvFile
$localUser = Get-RequiredValue -Values $localEnv -Key "DB_USER" -SourceName $resolvedEnvFile
$localPassword = Get-RequiredValue -Values $localEnv -Key "DB_PASSWORD" -SourceName $resolvedEnvFile
$localMaintenanceDb = if ($localEnv.ContainsKey("LOCAL_DB_MAINTENANCE_DB") -and $localEnv["LOCAL_DB_MAINTENANCE_DB"]) {
    $localEnv["LOCAL_DB_MAINTENANCE_DB"]
} else {
    "postgres"
}

$secretsEnv = @{}
if (Test-Path -LiteralPath $resolvedSecretsEnvFile) {
    $secretsEnv = Read-DotEnvFile -Path $resolvedSecretsEnvFile
}

if (-not $ProdDatabaseUrl) {
    $ProdDatabaseUrl = Get-OptionalValue -Values $secretsEnv -Keys @("PROD_DATABASE_URL", "RAILWAY_PROD_DATABASE_URL", "DATABASE_URL")
}

if (-not $DumpPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $DumpPath = Join-Path $repoRoot ("tmp\prod_refresh_{0}.dump" -f $timestamp)
}

$dumpDirectory = Split-Path -Parent $DumpPath
if (-not (Test-Path -LiteralPath $dumpDirectory)) {
    New-Item -ItemType Directory -Path $dumpDirectory | Out-Null
}

if (-not $Force) {
    Write-Host "This will DROP and recreate your local database '$localDatabase' on ${localHost}:${localPort}." -ForegroundColor Yellow
    $confirmation = Read-Host "Type REFRESH to continue"
    if ($confirmation -ne "REFRESH") {
        throw "Refresh cancelled."
    }
}

$railwayCli = Get-CommandPath -Name "railway.cmd"
$pgDump = Get-CommandPath -Name "pg_dump.exe"
$pgRestore = Get-CommandPath -Name "pg_restore.exe"
$dropDb = Get-CommandPath -Name "dropdb.exe"
$createDb = Get-CommandPath -Name "createdb.exe"

if ($ProdDatabaseUrl) {
    Write-Step "Using production database URL provided directly"
    if ($DryRun) {
        if ($secretsEnv.Count -gt 0) {
            Write-Host "   Prod database URL was loaded from $resolvedSecretsEnvFile."
        } else {
            Write-Host "   Prod database URL was provided manually."
        }
        $prodVars = @{
            DB_HOST = "<railway-host>"
            DB_PORT = "5432"
            DB_NAME = "<railway-db>"
            DB_USER = "<railway-user>"
            DB_PASSWORD = "<railway-password>"
        }
    } else {
        $prodVars = @{}
    }
} else {
    Write-Step "Fetching production variables from Railway"
    $railwayArgs = @("variable", "list", "--environment", $RailwayEnvironment, "--json")
    if ($RailwayService) {
        $railwayArgs += @("--service", $RailwayService)
    }

    if ($DryRun) {
        Write-Host ("   " + ((@($railwayCli) + $railwayArgs) -join " "))
        $prodVars = @{
            DB_HOST = "<railway-host>"
            DB_PORT = "5432"
            DB_NAME = "<railway-db>"
            DB_USER = "<railway-user>"
            DB_PASSWORD = "<railway-password>"
            DATABASE_URL = "<railway-url>"
        }
    } else {
        $rawRailway = & $railwayCli @railwayArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to read Railway variables. Make sure this repo is linked and you're logged in with Railway CLI."
        }

        try {
            $railwayJson = $rawRailway | ConvertFrom-Json
        }
        catch {
            throw "Railway variables output was not valid JSON."
        }

        $prodVars = Normalize-RailwayVariables -Payload $railwayJson
    }
}

$prodVars = Add-DatabaseUrlFallbacks -Values $prodVars -DatabaseUrl $ProdDatabaseUrl

$prodHost = Get-OptionalValue -Values $prodVars -Keys @("DB_HOST", "PGHOST", "POSTGRES_HOST")
$prodPort = Get-OptionalValue -Values $prodVars -Keys @("DB_PORT", "PGPORT", "POSTGRES_PORT")
$prodDatabase = Get-OptionalValue -Values $prodVars -Keys @("DB_NAME", "PGDATABASE", "POSTGRES_DB")
$prodUser = Get-OptionalValue -Values $prodVars -Keys @("DB_USER", "PGUSER", "POSTGRES_USER")
$prodPassword = Get-OptionalValue -Values $prodVars -Keys @("DB_PASSWORD", "PGPASSWORD", "POSTGRES_PASSWORD")

if (-not $prodHost -or -not $prodDatabase -or -not $prodUser -or -not $prodPassword) {
    $availableKeys = ($prodVars.Keys | Sort-Object) -join ", "
    throw @"
Could not resolve production database credentials from Railway variables.

Looked for:
- host: DB_HOST, PGHOST, POSTGRES_HOST, DATABASE_URL
- port: DB_PORT, PGPORT, POSTGRES_PORT, DATABASE_URL
- database: DB_NAME, PGDATABASE, POSTGRES_DB, DATABASE_URL
- user: DB_USER, PGUSER, POSTGRES_USER, DATABASE_URL
- password: DB_PASSWORD, PGPASSWORD, POSTGRES_PASSWORD, DATABASE_URL

Available Railway keys: $availableKeys

This usually means one of these:
1. The current linked Railway service is your web app instead of the Postgres service.
2. You need to pass -RailwayService with the actual Railway database service name.
3. The Railway database variables are sealed, so the CLI cannot read them. In that case, pass -ProdDatabaseUrl with the DATABASE_URL copied from the Railway UI.
4. Store PROD_DATABASE_URL in $resolvedSecretsEnvFile so the script can load it automatically next time.
"@
}

if (-not $prodPort) {
    $prodPort = "5432"
}

Write-Step "Dumping production database"
$prodDumpArgs = New-PostgresArgs -DbHost $prodHost -DbPort $prodPort -DbUser $prodUser -DbName $prodDatabase
$prodDumpArgs += @(
    "--format=custom",
    "--file", $DumpPath,
    "--no-owner",
    "--no-privileges"
)
Invoke-Checked -FilePath $pgDump -Arguments $prodDumpArgs -EnvironmentOverrides @{
    PGPASSWORD = $prodPassword
    PGSSLMODE = "require"
}

Write-Step "Dropping local database"
$dropArgs = @(
    "--if-exists",
    "--force",
    "--host", $localHost,
    "--port", $localPort,
    "--username", $localUser,
    $localDatabase
)
Invoke-Checked -FilePath $dropDb -Arguments $dropArgs -EnvironmentOverrides @{
    PGPASSWORD = $localPassword
}

Write-Step "Recreating local database"
$createArgs = @(
    "--host", $localHost,
    "--port", $localPort,
    "--username", $localUser,
    "--maintenance-db", $localMaintenanceDb,
    $localDatabase
)
Invoke-Checked -FilePath $createDb -Arguments $createArgs -EnvironmentOverrides @{
    PGPASSWORD = $localPassword
}

Write-Step "Restoring dump into local database"
$restoreArgs = New-PostgresArgs -DbHost $localHost -DbPort $localPort -DbUser $localUser -DbName $localDatabase
$restoreArgs += @(
    "--clean",
    "--if-exists",
    "--no-owner",
    "--no-privileges",
    $DumpPath
)
Invoke-Checked -FilePath $pgRestore -Arguments $restoreArgs -EnvironmentOverrides @{
    PGPASSWORD = $localPassword
}

Write-Step "Refresh complete"
if ($DryRun) {
    Write-Host "Dry run only. No production dump was created and no local database changes were made." -ForegroundColor Yellow
    Write-Host "When you run without -DryRun, local database '$localDatabase' will be replaced from Railway '$RailwayEnvironment'."
    Write-Host "Planned dump path: $DumpPath"
} else {
    Write-Step "Cleaning up old dump files"
    $removedDumpCount = Remove-OldDumpFiles -Directory $dumpDirectory -CurrentDumpPath $DumpPath -KeepCount $KeepDumpCount
    if ($removedDumpCount -gt 0) {
        Write-Host "Removed $removedDumpCount older dump file(s). Keeping the latest $KeepDumpCount."
    } else {
        Write-Host "No old dump files needed cleanup. Keeping the latest $KeepDumpCount."
    }

    Write-Step "Refresh complete"
    Write-Host "Local database '$localDatabase' now matches the latest dump from Railway '$RailwayEnvironment'."
    Write-Host "Dump file kept at: $DumpPath"
}
