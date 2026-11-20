# Create a temporary drive letter for an empty test folder; no volume or device is initialized.
[CmdletBinding()]
param([string]$TestAssembly)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This acceptance test requires Windows.' }
$folder = Join-Path ([IO.Path]::GetTempPath()) ('speakr-import-' + [Guid]::NewGuid().ToString('N'))
$letters = [IO.DriveInfo]::GetDrives() | ForEach-Object { $_.Name[0] }
$letter = 90..68 | ForEach-Object { [char]$_ } | Where-Object { $_ -notin $letters } | Select-Object -First 1
if (!$letter) { throw 'No unused drive letter for the simulation.' }
$drive = "${letter}:"
$subst = Join-Path $env:SystemRoot 'System32\subst.exe'
$previous = $env:SPEAKR_QA_MAPPED_DRIVE
[void][IO.Directory]::CreateDirectory($folder)
$mounted = $false
try {
    & $subst $drive $folder
    if ($LASTEXITCODE) { throw 'Could not map the private test folder.' }
    $mounted = $true
    $env:SPEAKR_QA_MAPPED_DRIVE = "$drive\"
    $project = Join-Path $PSScriptRoot 'Speakr.Clinic.ImportAgent.Tests\Speakr.Clinic.ImportAgent.Tests.csproj'
    $receipt = Join-Path $folder 'pickup.trx'
    if ($TestAssembly) {
        & dotnet vstest $TestAssembly '--TestCaseFilter:FullyQualifiedName~ApprovedDrivePickupTests' '--Logger:trx;LogFileName=pickup.trx' "--ResultsDirectory:$folder"
    } else {
        & dotnet test $project --filter 'FullyQualifiedName~ApprovedDrivePickupTests' --logger 'trx;LogFileName=pickup.trx' --results-directory $folder
    }
    if ($LASTEXITCODE) { throw 'Pickup or replay verification failed.' }
    $counts = ([xml](Get-Content -LiteralPath $receipt -Raw)).TestRun.ResultSummary.Counters
    if ([int]$counts.executed -lt 2 -or [int]$counts.passed -ne [int]$counts.executed) { throw 'Expected both Windows pickup and author-change tests to pass.' }
    Write-Output "Windows test receipt: executed=$($counts.executed), passed=$($counts.passed), failed=$($counts.failed)"
    Write-Output 'Windows approved-folder pickup passed: one durable ingest, identical source, no duplicate chunks.'
} finally {
    $env:SPEAKR_QA_MAPPED_DRIVE = $previous
    if ($mounted) { & $subst $drive /D }
    if (Test-Path -LiteralPath (Join-Path $folder 'pickup.trx')) { [IO.File]::Delete((Join-Path $folder 'pickup.trx')) }
    [IO.Directory]::Delete($folder, $false)
}
