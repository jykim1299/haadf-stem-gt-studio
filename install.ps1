param(
    [switch]$TestDependencies,
    [string]$Wheelhouse
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & py -3.12 -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 x64 is required. Install Python and retry.' }
}
$pipOptions = @()
if ($Wheelhouse) {
    $resolvedWheels = (Resolve-Path -LiteralPath $Wheelhouse).Path
    $pipOptions = @('--no-index', '--find-links', $resolvedWheels)
}
$requirements = if ($TestDependencies) { 'requirements-test.lock' } else { 'requirements-windows.lock' }
& $pythonPath -m pip install @pipOptions -r (Join-Path $projectRoot $requirements)
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network/proxy or wheelhouse.' }
& $pythonPath -m pip install @pipOptions --no-deps -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Studio installation failed.' }
Write-Output 'Installed. Run .\run.ps1 -Synthetic missing_tm for a synthetic example.'
