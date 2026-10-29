#!/usr/bin/env pwsh
# Report or roll back the DSH subagent -> internal GLM routing.
#
# Deliberately ASCII-only: Windows PowerShell 5.1 reads a .ps1 without a BOM as
# ANSI, so non-ASCII labels inside the file itself would corrupt the script.
#
# The routing touches two files:
#   %USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml   provider + preset override
#   %USERPROFILE%\.dsh\.credentials.yaml                   refs.ZGCI_API_KEY
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dsh_glm_routing.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dsh_glm_routing.ps1 -Restore
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dsh_glm_routing.ps1 -Restore -AlsoCredentials

[CmdletBinding()]
param(
    [switch]$Restore,
    [switch]$AlsoCredentials
)

$ErrorActionPreference = 'Stop'
$dshHome = Join-Path $env:USERPROFILE '.dsh'
$patch   = Join-Path $dshHome 'profiles\desktop\cordis.patch.yml'
$cred    = Join-Path $dshHome '.credentials.yaml'
$backups = Join-Path (Split-Path -Parent $PSScriptRoot) '.dsh-config-backup'

function Show-Status {
    Write-Host '=== DSH subagent -> GLM routing status ===' -ForegroundColor Cyan
    if (-not (Test-Path $patch)) { Write-Host "  MISSING: $patch" -ForegroundColor Red; return }
    $text = Get-Content $patch -Raw -Encoding UTF8

    $rows = @(
        @{ Name = 'zgci provider registered';  Ok = ($text -match 'zgci:\s*\r?\n\s*displayName') }
        @{ Name = 'baseURL carries /v1';       Ok = ($text -match 'baseURL:\s*https://hub\.zgci\.org/v1') }
        @{ Name = 'reasoningEfforts: false';   Ok = ($text -match 'reasoningEfforts:\s*false') }
        @{ Name = 'preset-standard override';  Ok = ($text -match '(?m)^-\s*id:\s*preset-standard') }
        @{ Name = 'spawn tools use zgci/glm';  Ok = (([regex]::Matches($text, 'provider:\s*zgci')).Count -ge 2) }
        @{ Name = 'main agent stays DeepSeek'; Ok = ($text -match 'model:\s*deepseek-flash') }
    )
    foreach ($r in $rows) {
        $mark = if ($r.Ok) { '[ OK ]' } else { '[FAIL]' }
        Write-Host ("  {0} {1}" -f $mark, $r.Name)
    }
    $credOk = (Test-Path $cred) -and ((Get-Content $cred -Raw -Encoding UTF8) -match '(?m)^\s*ZGCI_API_KEY\s*:')
    $mark = if ($credOk) { '[ OK ]' } else { '[FAIL]' }
    Write-Host ("  {0} credential refs.ZGCI_API_KEY" -f $mark)
    Write-Host ''
    Write-Host 'Applies to NEW sessions only; existing sessions keep their plugin revision.' -ForegroundColor Yellow
}

function Invoke-Restore {
    if (-not (Test-Path $backups)) { throw "No backup directory: $backups" }
    $newest = Get-ChildItem $backups -Directory | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $newest) { throw "Backup directory is empty: $backups" }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $src = Join-Path $newest.FullName 'cordis.patch.yml'
    if (-not (Test-Path $src)) { throw "Backup is missing cordis.patch.yml: $src" }

    Copy-Item $patch "$patch.before-rollback-$stamp" -Force
    Copy-Item $src $patch -Force
    Write-Host "  restored cordis.patch.yml from $($newest.Name)" -ForegroundColor Green

    if ($AlsoCredentials) {
        $csrc = Join-Path $newest.FullName '.credentials.yaml'
        if (Test-Path $csrc) {
            if (Test-Path $cred) { Copy-Item $cred "$cred.before-rollback-$stamp" -Force }
            Copy-Item $csrc $cred -Force
            Write-Host '  restored .credentials.yaml (removes ZGCI_API_KEY)' -ForegroundColor Green
        }
    } else {
        Write-Host '  credential left untouched (subagents could still be told to use zgci)' -ForegroundColor DarkGray
    }
    Write-Host ''
    Write-Host 'Restart DSH or open a new session to return to the previous behaviour.' -ForegroundColor Yellow
}

if ($Restore) { Invoke-Restore } else { Show-Status }
