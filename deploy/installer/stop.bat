@echo off
taskkill /IM stepeye-runtime.exe /F >nul 2>&1
if errorlevel 1 (
  echo StepEye is not running.
) else (
  echo StepEye stopped.
)
>nul timeout /t 2 /nobreak
