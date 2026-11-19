# Lab checks for standard-user install, update and rollback of the clinic import agent (w073, T-008).
# Runs on a disposable Windows lab host only. Prints one JSON record per check.
# A check whose prerequisite is missing is reported as not_run with the blocked prerequisite; it is never reported as passed.
# Disposable synthetic data only. Never touches real recordings, secrets or production paths.
[CmdletBinding()]
param(
    [string]$LabRoot = '',
    [string]$PackageV1 = '',
    [string]$PackageV2 = '',
    [string]$PackageV2Broken = '',
    [string]$InstallScript = '',
    [string]$UpdateScript = '',
    [string]$RollbackScript = '',
    [string]$RotateScript = '',
    [string]$RevokeScript = '',
    [switch]$ConfirmDisposableLab
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$results = New-Object System.Collections.Generic.List[object]

function Add-Result([string]$Id, [string]$Status, [string]$Detail) {
    $results.Add([pscustomobject]@{ id = $Id; status = $Status; detail = $Detail; case = 'T-008' })
}

function Test-Prereq([string]$Id, [string]$Name, [string]$Value, [bool]$IsPath) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Add-Result $Id 'not_run' "blocked prerequisite: $Name not supplied"
        return $false
    }
    if ($IsPath -and -not (Test-Path -LiteralPath $Value)) {
        Add-Result $Id 'not_run' "blocked prerequisite: $Name not found"
        return $false
    }
    return $true
}

$onWindows = ($PSVersionTable.PSEdition -eq 'Desktop') -or ($PSVersionTable.Platform -eq 'Win32NT')
$ids = @(
    'standard-user-service', 'spool-acl', 'version-signature', 'update-ok',
    'update-failed-rollback', 'spool-preserved', 'stale-version-rejected',
    'unauthorized-install-denied', 'credential-rotation', 'credential-revocation'
)

if (-not $onWindows) {
    foreach ($id in $ids) { Add-Result $id 'not_run' 'blocked prerequisite: Windows lab host' }
}
elseif (-not $ConfirmDisposableLab) {
    foreach ($id in $ids) { Add-Result $id 'not_run' 'blocked prerequisite: -ConfirmDisposableLab not given' }
}
else {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)

    # Standard-user operation: the check itself must run as a standard user.
    if ($isAdmin) {
        Add-Result 'standard-user-service' 'not_run' 'blocked prerequisite: run as standard user, got elevated token'
    }
    elseif (Test-Prereq 'standard-user-service' 'LabRoot' $LabRoot $true) {
        $svc = Get-CimInstance Win32_Service -Filter "Name='SpeakrClinicImportAgent'" -ErrorAction SilentlyContinue
        if ($null -eq $svc) { Add-Result 'standard-user-service' 'not_run' 'blocked prerequisite: agent not installed in lab' }
        elseif ($svc.StartName -match 'LocalSystem') { Add-Result 'standard-user-service' 'failed' "service runs as $($svc.StartName)" }
        else { Add-Result 'standard-user-service' 'passed' "service account $($svc.StartName)" }
    }

    # Spool ACL: no read for Everyone, Users or Authenticated Users.
    if (Test-Prereq 'spool-acl' 'LabRoot' $LabRoot $true) {
        $spool = Join-Path $LabRoot 'spool'
        if (-not (Test-Path -LiteralPath $spool)) { Add-Result 'spool-acl' 'not_run' 'blocked prerequisite: spool directory missing' }
        else {
            $bad = (Get-Acl -LiteralPath $spool).Access | Where-Object {
                $_.AccessControlType -eq 'Allow' -and $_.IdentityReference.Value -match '(Everyone|\\Users|Authenticated Users)$'
            }
            if ($bad) { Add-Result 'spool-acl' 'failed' 'spool grants access to a broad group' }
            else { Add-Result 'spool-acl' 'passed' 'no broad-group allow entry on spool' }
        }
    }

    # Version and signature of the installed package.
    if (Test-Prereq 'version-signature' 'PackageV1' $PackageV1 $true) {
        $exe = Get-ChildItem -LiteralPath $PackageV1 -Filter '*.exe' -Recurse | Select-Object -First 1
        if ($null -eq $exe) { Add-Result 'version-signature' 'not_run' 'blocked prerequisite: no executable in PackageV1' }
        else {
            $sig = Get-AuthenticodeSignature -FilePath $exe.FullName
            if ($sig.Status -eq 'Valid') { Add-Result 'version-signature' 'passed' "version $($exe.VersionInfo.FileVersion), signature Valid" }
            elseif ($sig.Status -eq 'NotSigned') { Add-Result 'version-signature' 'not_run' 'blocked prerequisite: signing certificate unavailable, package unsigned' }
            else { Add-Result 'version-signature' 'failed' "signature status $($sig.Status)" }
        }
    }

    function Invoke-Step([string]$Id, [string]$Script, [string[]]$StepArgs, [string]$Name) {
        if (-not (Test-Prereq $Id $Name $Script $true)) { return $null }
        & $Script @StepArgs
        return $LASTEXITCODE
    }

    $spoolFile = if ($LabRoot) { Join-Path (Join-Path $LabRoot 'spool') 'synthetic-queued.dat' } else { '' }

    # Successful update to the newer package.
    $rc = Invoke-Step 'update-ok' $UpdateScript @('-Package', $PackageV2) 'UpdateScript'
    if ($null -ne $rc) { Add-Result 'update-ok' ($(if ($rc -eq 0) { 'passed' } else { 'failed' })) "exit $rc" }

    # Failed update: broken package must roll back and keep the queued synthetic import.
    if ((Test-Prereq 'update-failed-rollback' 'PackageV2Broken' $PackageV2Broken $true) -and $spoolFile) {
        Set-Content -LiteralPath $spoolFile -Value 'synthetic-import' -Force
        $before = (Get-FileHash -LiteralPath $spoolFile).Hash
        $rc = Invoke-Step 'update-failed-rollback' $UpdateScript @('-Package', $PackageV2Broken) 'UpdateScript'
        if ($null -ne $rc) {
            $rolled = Invoke-Step 'update-failed-rollback' $RollbackScript @() 'RollbackScript'
            Add-Result 'update-failed-rollback' ($(if ($rc -ne 0 -and $rolled -eq 0) { 'passed' } else { 'failed' })) "update exit $rc, rollback exit $rolled"
            $after = if (Test-Path -LiteralPath $spoolFile) { (Get-FileHash -LiteralPath $spoolFile).Hash } else { '' }
            Add-Result 'spool-preserved' ($(if ($before -eq $after) { 'passed' } else { 'failed' })) 'spool file hash before/after failed update'
        }
    }
    else { Add-Result 'spool-preserved' 'not_run' 'blocked prerequisite: failed-update scenario not executed' }

    # Stale version: installing the older package over the newer one must be refused.
    $rc = Invoke-Step 'stale-version-rejected' $UpdateScript @('-Package', $PackageV1) 'UpdateScript'
    if ($null -ne $rc) { Add-Result 'stale-version-rejected' ($(if ($rc -ne 0) { 'passed' } else { 'failed' })) "downgrade exit $rc" }

    # Unauthorized install: standard user without approval must be denied.
    $rc = Invoke-Step 'unauthorized-install-denied' $InstallScript @('-Package', $PackageV1) 'InstallScript'
    if ($null -ne $rc) { Add-Result 'unauthorized-install-denied' ($(if ($rc -ne 0) { 'passed' } else { 'failed' })) "install exit $rc" }

    $rc = Invoke-Step 'credential-rotation' $RotateScript @() 'RotateScript'
    if ($null -ne $rc) { Add-Result 'credential-rotation' ($(if ($rc -eq 0) { 'passed' } else { 'failed' })) "exit $rc" }

    $rc = Invoke-Step 'credential-revocation' $RevokeScript @() 'RevokeScript'
    if ($null -ne $rc) { Add-Result 'credential-revocation' ($(if ($rc -eq 0) { 'passed' } else { 'failed' })) "exit $rc" }
}

$results | ConvertTo-Json -Depth 3
if ($results | Where-Object { $_.status -eq 'failed' }) { exit 1 }
exit 0
