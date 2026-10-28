param(
    [string]$Workspace,
    [string]$Project,
    [string]$Synthetic,
    [switch]$Help
)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & (Join-Path $PSScriptRoot 'install.ps1')
}
$arguments = @('-m', 'haadf_studio.app')
if ($Workspace) { $arguments += @('--workspace', $Workspace) }
if ($Project) { $arguments += @('--project', $Project) }
if ($Synthetic) { $arguments += @('--synthetic', $Synthetic) }
if ($Help) { $arguments += '--help' }
& $pythonPath @arguments
exit $LASTEXITCODE
