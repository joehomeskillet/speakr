# Offline distribution artifact for the clinic import-agent worker.
# Writes OutputDirectory only. No service install, no rollout, no certificate-store lookup.
# shortcut: signature step binds the authorized identity and the payload hash; Authenticode waits for a Windows signing host (w073).
[CmdletBinding()]
param(
    [ValidateSet('Development', 'Release')]
    [string]$Configuration = 'Development',
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$SigningIdentity = '',
    [string]$AuthorizedIdentitiesPath = '',
    [string]$Version = '',
    [string]$PublishLayout = '',
    [string]$PublishStub = '',
    [string]$ProjectPath = '',
    [string]$DirectoryBuildProps = '',
    [string]$RepoRoot = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$PackagePolicyJson = @'
{
  "package_id": "speakr-clinic-import-agent",
  "version": "0.11.1",
  "source_pin": "48d3018fb7d80b77f897bedd86bcc89b5034d1f5",
  "worker_project": "apps/windows-import/src/Speakr.Clinic.ImportAgent.Worker/Speakr.Clinic.ImportAgent.Worker.csproj",
  "directory_build_props": "apps/windows-import/Directory.Build.props",
  "unsigned_marker": "UNSIGNED-DEVELOPMENT",
  "unsigned_notice": "Unsignierte Entwicklungsversion. Kein signiertes Release.",
  "signature_step": "Verify-AgentPackageSignature",
  "license_notice": "{id} {version}: gepinnte PackageReference; Lizenztext wird nicht heruntergeladen.",
  "runtime_notice": "{framework}/{runtime}: gepinnter Runtime; es wird kein Runtime-Paket heruntergeladen.",
  "manifest_name": "agent-package-manifest.json",
  "failure_name": "agent-package-failure.json",
  "publish": {
    "framework": "net10.0",
    "runtime": "win-x64",
    "self_contained": false,
    "no_restore": true,
    "max_attempts": 3
  }
}
'@

$script:Policy = $PackagePolicyJson | ConvertFrom-Json

function Write-Utf8([string]$Path, [string]$Text) {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Text, $utf8)
}

function Write-Failure([string]$Status, [int]$Code, $Attempts, $Verification) {
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
    $manifest = Join-Path $OutputDirectory $script:Policy.manifest_name
    $marker = Join-Path $OutputDirectory ($script:Policy.unsigned_marker + '.txt')
    foreach ($path in @($manifest, $marker)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    $body = [ordered]@{
        package_id = $script:Policy.package_id
        status = $Status
        version = $script:Policy.version
        publish_attempts = @($Attempts)
    }
    if ($null -ne $Verification) { $body.signature_verification = $Verification }
    $failure = Join-Path $OutputDirectory $script:Policy.failure_name
    Write-Utf8 $failure (($body | ConvertTo-Json -Depth 6) + "`n")
    exit $Code
}

function Get-AuthorizedIdentities([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    $rows = @()
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trim = $line.Trim()
        if (-not $trim -or $trim.StartsWith('#')) { continue }
        $rows += $trim
    }
    return ,$rows
}

function Test-IdentityListed([string]$Identity, $Rows) {
    if (-not $Identity -or $null -eq $Rows) { return $false }
    foreach ($row in @($Rows)) {
        if ($row -ceq $Identity) { return $true }
    }
    return $false
}

function Verify-AgentPackageSignature($Channel, $Identity, $PayloadHash) {
    return [ordered]@{
        step = $script:Policy.signature_step
        executed = $true
        result = $Channel
        identity = $Identity
        payload_sha256 = $PayloadHash
    }
}

function Get-PublishArgs([string]$Project, [string]$OutDir, [string]$UseVersion) {
    return @(
        'publish', $Project,
        '-c', 'Release',
        '-f', [string]$script:Policy.publish.framework,
        '-r', [string]$script:Policy.publish.runtime,
        '--self-contained', 'false',
        '--no-restore',
        '-o', $OutDir,
        "-p:Version=$UseVersion",
        '-p:Deterministic=true'
    )
}

function Invoke-PinnedPublish([string]$Project, [string]$OutDir, [string]$UseVersion) {
    $max = [int]$script:Policy.publish.max_attempts
    $used = @()
    $publishArgs = Get-PublishArgs $Project $OutDir $UseVersion
    for ($n = 0; $n -lt $max; $n++) {
        $code = 1
        try {
            if ($PublishStub) { & $PublishStub @publishArgs | Out-Null }
            else { & dotnet @publishArgs | Out-Null }
            if ($null -ne $LASTEXITCODE) { $code = [int]$LASTEXITCODE }
        } catch {
            $code = 1
        }
        $used += $code
        if ($code -eq 0) { return @{ ok = $true; attempts = $used } }
    }
    return @{ ok = $false; attempts = $used }
}

function Copy-PublishLayout([string]$Source, [string]$Dest) {
    $root = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\', '/')
    $skip = @($script:Policy.manifest_name, $script:Policy.failure_name, ($script:Policy.unsigned_marker + '.txt'))
    foreach ($file in @(Get-ChildItem -LiteralPath $root -Recurse -File)) {
        $full = $file.FullName
        if ($full.Length -le $root.Length) { throw 'layout escape' }
        $sep = $full[$root.Length]
        if ($sep -ne '\' -and $sep -ne '/') { throw 'layout escape' }
        $rel = $full.Substring($root.Length + 1)
        $posix = $rel -replace '\\', '/'
        if ($skip -contains $posix) { continue }
        $target = Join-Path $Dest $rel
        $parent = Split-Path -Parent $target
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Copy-Item -LiteralPath $full -Destination $target -Force
    }
}

if ($Configuration -cne 'Development' -and $Configuration -cne 'Release') {
    Write-Failure 'unauthorized' 3 @() $null
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
}
if (-not $ProjectPath) {
    $ProjectPath = Join-Path $RepoRoot ($script:Policy.worker_project -replace '/', '\')
}
if (-not $DirectoryBuildProps) {
    $DirectoryBuildProps = Join-Path $RepoRoot ($script:Policy.directory_build_props -replace '/', '\')
}

if ($script:Policy.publish.self_contained -or -not $script:Policy.publish.no_restore) {
    Write-Failure 'stale-version' 2 @() $null
}
if ($Version -and $Version -cne $script:Policy.version) {
    Write-Failure 'stale-version' 2 @() $null
}
$useVersion = [string]$script:Policy.version

try {
    [xml]$projectXml = Get-Content -Raw -LiteralPath $ProjectPath
    [xml]$propsXml = Get-Content -Raw -LiteralPath $DirectoryBuildProps
} catch {
    Write-Failure 'stale-version' 2 @() $null
}

$tfms = @($projectXml.SelectNodes('//TargetFramework') | ForEach-Object { $_.InnerText.Trim() })
$packages = @()
foreach ($node in @($projectXml.SelectNodes('//PackageReference'))) {
    $id = $node.GetAttribute('Include')
    $ver = $node.GetAttribute('Version')
    if (-not $id -or -not $ver) { Write-Failure 'stale-version' 2 @() $null }
    $packages += [ordered]@{ id = $id; version = $ver }
}
$dets = @($propsXml.SelectNodes('//Deterministic') | ForEach-Object { $_.InnerText.Trim() })
$framework = [string]$script:Policy.publish.framework
if ($tfms.Count -ne 1 -or $tfms[0] -cne $framework -or $packages.Count -lt 1) {
    Write-Failure 'stale-version' 2 @() $null
}
if ($dets.Count -lt 1 -or $dets[0] -cne 'true') {
    Write-Failure 'stale-version' 2 @() $null
}

$channel = 'unsigned-development'
$verifiedIdentity = $null
if ($Configuration -ceq 'Release') {
    $listed = Get-AuthorizedIdentities $AuthorizedIdentitiesPath
    if (-not (Test-IdentityListed $SigningIdentity.Trim() $listed)) {
        $refused = Verify-AgentPackageSignature 'unauthorized' $null $null
        Write-Failure 'unauthorized' 3 @() $refused
    }
    $channel = 'signed-release'
    $verifiedIdentity = $SigningIdentity.Trim()
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$attempts = @()
if ($PublishLayout) {
    Copy-PublishLayout $PublishLayout $OutputDirectory
} else {
    $published = Invoke-PinnedPublish $ProjectPath $OutputDirectory $useVersion
    $attempts = @($published.attempts)
    if (-not $published.ok) { Write-Failure 'publish-failed' 4 $attempts $null }
}

$markerName = [string]$script:Policy.unsigned_marker + '.txt'
$markerPath = Join-Path $OutputDirectory $markerName
if ($Configuration -ceq 'Development') {
    Write-Utf8 $markerPath ([string]$script:Policy.unsigned_notice + "`n")
} elseif (Test-Path -LiteralPath $markerPath) {
    Remove-Item -LiteralPath $markerPath -Force
}

$skipHash = @([string]$script:Policy.manifest_name, [string]$script:Policy.failure_name)
$fileRows = @()
$contentCount = 0
$rootOut = (Resolve-Path -LiteralPath $OutputDirectory).Path.TrimEnd('\', '/')
foreach ($file in @(Get-ChildItem -LiteralPath $OutputDirectory -Recurse -File | Sort-Object FullName)) {
    $full = $file.FullName
    if ($full.Length -le $rootOut.Length) { throw 'layout escape' }
    $sep = $full[$rootOut.Length]
    if ($sep -ne '\' -and $sep -ne '/') { throw 'layout escape' }
    $rel = $full.Substring($rootOut.Length + 1) -replace '\\', '/'
    if ($skipHash -contains $rel) { continue }
    if ($rel -cne $markerName) { $contentCount += 1 }
    $hash = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant()
    $fileRows += [ordered]@{ path = $rel; sha256 = $hash }
}
if ($contentCount -lt 1) { Write-Failure 'publish-failed' 4 $attempts $null }

$runtimeNotice = ([string]$script:Policy.runtime_notice).Replace('{framework}', $framework).Replace('{runtime}', [string]$script:Policy.publish.runtime)
$notices = @($runtimeNotice)
foreach ($package in $packages) {
    $notices += ([string]$script:Policy.license_notice).Replace('{id}', [string]$package.id).Replace('{version}', [string]$package.version)
}

$lines = @()
foreach ($row in $fileRows) { $lines += ($row.path + "`t" + $row.sha256) }
$canonical = $lines -join "`n"
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $payloadHash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($canonical)))).Replace('-', '').ToLowerInvariant()
} finally {
    $sha.Dispose()
}
$verification = Verify-AgentPackageSignature $channel $verifiedIdentity $payloadHash

$failurePath = Join-Path $OutputDirectory $script:Policy.failure_name
if (Test-Path -LiteralPath $failurePath) { Remove-Item -LiteralPath $failurePath -Force }

$mode = 'command'
if ($PublishLayout) { $mode = 'prebuilt-layout' }
$manifest = [ordered]@{
    package_id = $script:Policy.package_id
    status = 'ok'
    version = $useVersion
    configuration = $Configuration
    channel = $channel
    distribution_marker = $(if ($Configuration -ceq 'Development') { [string]$script:Policy.unsigned_marker } else { $null })
    target_framework = $framework
    runtime_identifier = [string]$script:Policy.publish.runtime
    self_contained = $false
    restore_during_package = $false
    deterministic = $true
    source_offer = [ordered]@{
        product = 'speakr'
        version = $useVersion
        commit = [string]$script:Policy.source_pin
        project = [string]$script:Policy.worker_project
    }
    pinned_packages = @($packages)
    license_notices = @($notices)
    files = @($fileRows)
    signature_verification = $verification
    publish = [ordered]@{
        mode = $mode
        attempts = @($attempts)
        command = @(@('dotnet') + @(Get-PublishArgs $ProjectPath $OutputDirectory $useVersion))
    }
    publish_attempts = @($attempts)
    prohibited = [ordered]@{
        machine_installation = $false
        production_rollout = $false
        credential_discovery = $false
        native_dictation = $false
    }
}
$manifestPath = Join-Path $OutputDirectory $script:Policy.manifest_name
# shortcut: Windows PowerShell 5.1 ConvertTo-Json unwraps a one-element array; accept an object or a one-item array until the host is PowerShell 7
Write-Utf8 $manifestPath (($manifest | ConvertTo-Json -Depth 8) + "`n")
exit 0
