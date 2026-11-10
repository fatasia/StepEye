@echo off
REM Windows 一键打包:生成独立 exe(服务+前端打包进二进制)。
REM 前置: pip install pyinstaller
REM 产物: dist\visual-server\visual-server.exe → visual-server.exe serve configs\*.yaml --auth
REM 注意: models\ 会一并打包(手部/人员/检测模型);mediapipe 运行库较大,首次打包慢。
cd /d %~dp0..
pyinstaller --noconfirm --clean --name visual-server --onedir --console ^
  --paths . ^
  --icon "web/assets/stepeye.ico" ^
  --add-data "web;web" ^
  --add-data "configs;configs" ^
  --add-data "docs;docs" ^
  --add-data "models;models" ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  deploy\entry.py
echo.
echo 产物在 dist\visual-server\。运行:
echo   dist\visual-server\visual-server.exe serve configs\demo_sim.yaml --auth --inspection configs\inspection.yaml
