$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
$taskRequirements = Join-Path $PSScriptRoot 'requirements.txt'
$taskDependencyCheck = Join-Path $PSScriptRoot 'scripts\check_dependencies.py'
& $taskPython $taskDependencyCheck $taskRequirements
$taskDependencyStatus = $LASTEXITCODE
if ($taskDependencyStatus -eq 1) {
    & $taskPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'pip setup failed.' }
    # Reinstall also repairs missing module files when package metadata survives.
    & $taskPython -m pip install --force-reinstall -r $taskRequirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & $taskPython $taskDependencyCheck $taskRequirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed after installation.' }
} elseif ($taskDependencyStatus -ne 0) {
    throw 'Dependency requirements check failed.'
}
Write-Host 'Open http://127.0.0.1:8788 in your browser.'
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
& $taskPython -m uvicorn server:app --host 127.0.0.1 --port 8788 --no-access-log
exit $LASTEXITCODE
