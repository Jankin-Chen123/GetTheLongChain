$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& $taskPython -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ['fastapi','uvicorn','curl_cffi']) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m ensurepip --upgrade
    & $taskPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
Write-Host 'Open http://127.0.0.1:8788 in your browser.'
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
& $taskPython -m uvicorn server:app --host 127.0.0.1 --port 8788 --no-access-log
exit $LASTEXITCODE
