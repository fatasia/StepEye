@echo off
setlocal
cd /d "%~dp0"
if not exist "data" mkdir "data"
echo StepEye demo: http://127.0.0.1:8000
echo Close this window to stop the server.
stepeye-runtime.exe serve .\_internal\configs\demo_sim.yaml --port 8000 --data data
pause
