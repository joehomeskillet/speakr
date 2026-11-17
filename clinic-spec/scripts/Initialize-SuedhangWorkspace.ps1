#Requires -Version 5.1
<#
.SYNOPSIS
Creates a NEW local working copy at the verified Speakr baseline and copies the V4-Web specification.
.DESCRIPTION
Dry-run by default. -Apply permits a public Git clone and local file writes only.
No installation, admin changes, app launch, production connection, secret creation or remote push.
Run from the extracted specification package. Existing target directories are rejected.
.EXAMPLE
.\scripts\Initialize-SuedhangWorkspace.ps1 -Destination C:\codex\suedhang-dictation
.EXAMPLE
.\scripts\Initialize-SuedhangWorkspace.ps1 -Destination C:\codex\suedhang-dictation -Apply
#>
[CmdletBinding()]
param(
    [Parameter()][string]$Destination = '',
    [Parameter()][switch]$Apply,
    [Parameter()][string]$SpecRoot = (Split-Path -Parent $PSScriptRoot)
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ExpectedRepo = 'https://github.com/murtaza-nasir/speakr.git'
$ExpectedRef = 'v0.11.1'
$ExpectedCommit = '48d3018fb7d80b77f897bedd86bcc89b5034d1f5'
$script:GitExe = $null
$script:LogPath = $null

function Write-Step {
    param([Parameter(Mandatory=$true)][string]$Message)
    $line = '{0} {1}' -f ([DateTime]::UtcNow.ToString('o')), $Message
    Write-Host $line
    if ($script:LogPath) { Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 }
}
function Invoke-CheckedGit {
    param([Parameter(Mandatory=$true)][string[]]$Arguments)
    # Do not log arbitrary credentials. This script only constructs public, fixed Git operations.
    Write-Step ('git ' + ($Arguments -join ' '))
    & $script:GitExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ('Git failed with exit code {0}. Partial target, if any, was kept for inspection.' -f $LASTEXITCODE)
    }
}
try {
    $root = [IO.Path]::GetFullPath($SpecRoot)
    if (-not (Test-Path -LiteralPath (Join-Path $root 'evidence\upstream-lock.json') -PathType Leaf)) {
        throw 'SpecRoot does not contain the V4-Web package. Extract the full ZIP or pass -SpecRoot.'
    }
    if ((Test-Path -LiteralPath (Join-Path $root '02_Originalartefakte')) -or (Test-Path -LiteralPath (Join-Path $root '01_Claude_Bauauftrag'))) { throw 'Use ONLY 01_Claude_Bauauftrag as SpecRoot, not the full archive root.' }
    foreach ($required in @('CLAUDE.md','START_CLAUDE.md','PROGRESS.md','config\decisions.json','planning\backlog.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $required) -PathType Leaf)) { throw "Missing specification file: $required" }
    }
    $lock = Get-Content -LiteralPath (Join-Path $root 'evidence\upstream-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $bases = @($lock.components | Where-Object { $_.id -eq 'speakr' })
    if ($bases.Count -ne 1) { throw 'Exactly one Speakr lock entry is required.' }
    $base = $bases[0]
    if ($base.repository -cne $ExpectedRepo -or $base.ref -cne $ExpectedRef -or $base.commit -cne $ExpectedCommit) {
        throw 'Baseline mismatch. Do not silently use another release. Review and update the bootstrap deliberately.'
    }
    if ([string]::IsNullOrWhiteSpace($Destination)) { $Destination = Join-Path (Split-Path -Parent $root) 'suedhang-dictation-work' }
    $dest = [IO.Path]::GetFullPath($Destination)
    $rootPrefix = $root.TrimEnd([char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)) + [IO.Path]::DirectorySeparatorChar
    if ($dest.Equals($root,[StringComparison]::OrdinalIgnoreCase) -or $dest.StartsWith($rootPrefix,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Destination must be outside the specification folder to prevent recursive copying.'
    }
    if (Test-Path -LiteralPath $dest) { throw 'Destination already exists. Choose a NEW directory; existing work is never overwritten.' }
    $allFiles = @(Get-ChildItem -LiteralPath $root -Force -Recurse)
    if (@($allFiles | Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 }).Count -gt 0) {
        throw 'Package contains a symlink/reparse point. Refusing recursive copy.'
    }
    if (Test-Path -LiteralPath (Join-Path $root '.git')) { throw 'Use the extracted package as SpecRoot, not an existing Git repository.' }
    Write-Step "Specification: $root"
    Write-Step "NEW workspace: $dest"
    Write-Step "Baseline: $ExpectedRef @ $ExpectedCommit"
    Write-Step 'Web-only: no native dictation client. Import-agent scope is files only.'
    Write-Step 'Plan: clone public upstream; verify commit; create clinic/main; copy clinic-spec and root instructions.'
    if (-not $Apply) {
        Write-Step 'DRY-RUN complete. Nothing changed. Add -Apply to execute the plan.'
        exit 0
    }
    $script:GitExe = (Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $parent = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { [void](New-Item -ItemType Directory -Path $parent -Force) }
    $logBase = Join-Path ([IO.Path]::GetTempPath()) 'SuedhangSpecBootstrap'
    [void](New-Item -ItemType Directory -Path $logBase -Force)
    $script:LogPath = Join-Path $logBase ('bootstrap-{0}-{1}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), ([Guid]::NewGuid().ToString('N')))
    Write-Step 'Applying local bootstrap. No production action is performed.'
    Invoke-CheckedGit -Arguments @('-c','core.hooksPath=NUL','clone','--no-checkout','--depth','1','--branch',$ExpectedRef,'--single-branch',$ExpectedRepo,$dest)
    $actual = & $script:GitExe -C $dest rev-parse --verify "${ExpectedRef}^{commit}"
    if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve the cloned release commit.' }
    $actual = ($actual | Out-String).Trim().ToLowerInvariant()
    if ($actual -cne $ExpectedCommit) { throw "Cloned commit differs from verified baseline: $actual. No checkout or copy performed." }
    Invoke-CheckedGit -Arguments @('-C',$dest,'-c','core.hooksPath=NUL','checkout','-b','clinic/main',$ExpectedCommit)
    Invoke-CheckedGit -Arguments @('-C',$dest,'remote','rename','origin','upstream')
    # Avoid accidentally pushing to upstream, even if a contributor happens to have write access.
    Invoke-CheckedGit -Arguments @('-C',$dest,'remote','set-url','--push','upstream','DISABLED_PUSH')
    $specTarget = Join-Path $dest 'clinic-spec'
    if (Test-Path -LiteralPath $specTarget) { throw 'Upstream unexpectedly contains clinic-spec; refusing overwrite.' }
    [void](New-Item -ItemType Directory -Path $specTarget)
    foreach ($item in Get-ChildItem -LiteralPath $root -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $specTarget -Recurse -Force
    }
    foreach ($name in @('CLAUDE.md','START_CLAUDE.md','PROGRESS.md')) {
        $target = Join-Path $dest $name
        if (Test-Path -LiteralPath $target) {
            $backup = Join-Path $dest ($name + '.upstream-original')
            if (Test-Path -LiteralPath $backup) { throw "Unexpected backup collision: $backup" }
            Move-Item -LiteralPath $target -Destination $backup
        }
        Copy-Item -LiteralPath (Join-Path $root $name) -Destination $target
    }
    $record = [ordered]@{
        created_utc = [DateTime]::UtcNow.ToString('o')
        repository = $ExpectedRepo
        verified_commit = $actual
        branch = 'clinic/main'
        spec_version = '4.0-web'
        app_built = $false
        app_tests_run = $false
        production_actions_performed = $false
    }
    $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $specTarget 'evidence\bootstrap-local.json') -Encoding UTF8
    Write-Step "Created: $dest"
    Write-Step 'Open Claude Code in that folder and use START_CLAUDE.md. Review git status before committing.'
    Write-Step 'No application was installed, built, tested or deployed by this script.'
    Write-Step "Bootstrap log: $script:LogPath"
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    if ($script:LogPath) { Write-Host "Log: $script:LogPath" }
    exit 1
}
