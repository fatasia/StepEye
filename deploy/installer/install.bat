@echo off
setlocal EnableExtensions
chcp 65001 >nul
title StepEye 安装程序

set "TARGET=%STEPEYE_TARGET%"
if "%TARGET%"=="" set "TARGET=%LOCALAPPDATA%\StepEye"
set "SOURCE=%~dp0stepeye-runtime"
if not exist "%SOURCE%\stepeye-runtime.exe" (
  echo 安装文件不完整：找不到 stepeye-runtime.exe
  if not "%STEPEYE_SILENT%"=="1" pause
  exit /b 1
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMddHHmmss"') do set "STAMP=%%i"
set "BACKUP="
if exist "%TARGET%" (
  set "BACKUP=%TARGET%.bak-%STAMP%"
  echo 备份现有版本到 "%BACKUP%"
  move "%TARGET%" "%BACKUP%" >nul
  if errorlevel 1 (
    echo 无法备份现有目录，请关闭正在运行的 StepEye 后重试。
    if not "%STEPEYE_SILENT%"=="1" pause
    exit /b 1
  )
)

echo 正在安装到 "%TARGET%" ...
robocopy "%SOURCE%" "%TARGET%" /E /MOVE /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo 安装失败，正在恢复旧版本...
  if exist "%TARGET%" rmdir /s /q "%TARGET%"
  if defined BACKUP if exist "%BACKUP%" move "%BACKUP%" "%TARGET%" >nul
  if not "%STEPEYE_SILENT%"=="1" pause
  exit /b 1
)

if defined BACKUP if exist "%BACKUP%\data" (
  echo 保留原有数据目录...
  robocopy "%BACKUP%\data" "%TARGET%\data" /E /NFL /NDL /NJH /NJS /NP >nul
)

powershell -NoProfile -Command "$v = @{ version = '%STEPEYE_VERSION%'; installed_at = (Get-Date).ToString('o') } | ConvertTo-Json; Set-Content -LiteralPath '%TARGET%\version.json' -Value $v -Encoding UTF8"

if not "%STEPEYE_NO_SHORTCUT%"=="1" (
  powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Programs'), 'StepEye.lnk')); $s.TargetPath = '%TARGET%\stepeye-runtime.exe'; $s.WorkingDirectory = '%TARGET%'; $s.Save()"
  echo 已创建开始菜单快捷方式。
)

echo.
echo 安装完成：%TARGET%
echo 启动：双击开始菜单的 StepEye，或运行 "%TARGET%\stepeye-runtime.exe" serve .\_internal\configs\demo_sim.yaml --port 8000
if not "%STEPEYE_SILENT%"=="1" pause
exit /b 0
