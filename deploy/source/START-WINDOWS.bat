@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [StepEye] 创建虚拟环境...
  python -m venv .venv || goto :error
)
call ".venv\Scripts\activate.bat"
echo [StepEye] 安装依赖（首次较慢）...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :error
echo.
echo [StepEye] 启动演示工位: http://127.0.0.1:8000
python -m visual serve configs/demo_sim.yaml --port 8000
goto :eof
:error
echo.
echo 启动失败，请确认已安装 Python 3.11+ 并加入 PATH。
pause
