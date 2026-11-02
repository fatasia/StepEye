@echo off
REM Windows 一键打包:生成独立 exe(服务+前端打包进二进制)。
REM 前置: pip install pyinstaller
REM 产物: dist\visual-server\visual.exe → visual.exe serve configs\*.yaml --auth
cd /d %~dp0..
pyinstaller --noconfirm --clean --name visual-server --onedir --console ^
  --add-data "web;web" ^
  --add-data "configs;configs" ^
  --add-data "docs;docs" ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  -m visual
echo.
echo 产物在 dist\visual-server\。运行:
echo   dist\visual-server\visual.exe serve configs\demo_sim.yaml --auth --inspection configs\inspection.yaml
