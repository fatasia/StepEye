@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "URL=http://127.0.0.1:8000"
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "data" mkdir "data"
if not exist "logs" mkdir "logs"

call :healthy
if not errorlevel 1 goto open

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%~dp0stepeye-runtime.exe' -ArgumentList @('serve','.\_internal\configs\demo_sim.yaml','--port','8000','--data','data') -WindowStyle Hidden -RedirectStandardOutput '%~dp0logs\server.log' -RedirectStandardError '%~dp0logs\server.err.log'"

for /l %%i in (1,1,60) do (
  call :healthy
  if not errorlevel 1 goto open
  >nul timeout /t 1 /nobreak
)

echo StepEye did not start. See logs\server.err.log
if exist "logs\server.err.log" start "" notepad "%~dp0logs\server.err.log"
exit /b 1

:open
if exist "%EDGE%" (
  start "" "%EDGE%" --app=%URL% --window-size=1440,900
) else (
  start "" %URL%
)
exit /b 0

:healthy
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 '%URL%/api/health'; if($r.StatusCode -eq 200){exit 0}}catch{}; exit 1" >nul 2>&1
exit /b %errorlevel%
