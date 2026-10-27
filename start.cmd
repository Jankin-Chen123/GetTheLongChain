@echo off
setlocal
cd /d "%~dp0" || goto failed

rem Windows PowerShell may be installed but absent from PATH.
set "taskPowerShell=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%taskPowerShell%" goto run
set "taskPowerShell=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
if exist "%taskPowerShell%" goto run

rem Also support PowerShell 7 and custom installations available on PATH.
for %%P in (pwsh.exe powershell.exe) do (
    set "taskPowerShell=%%~$PATH:P"
    if defined taskPowerShell goto run
)
echo PowerShell was not found. Install PowerShell or use the manual Python startup in README.md.
goto failed

:run
"%taskPowerShell%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "taskExitCode=%errorlevel%"
if "%taskExitCode%"=="0" exit /b 0
echo Startup failed. See the error above.
pause
exit /b %taskExitCode%

:failed
pause
exit /b 1
