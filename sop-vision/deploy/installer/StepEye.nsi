; StepEye Windows installer (NSIS, built with Tauri's bundled toolchain)
; Compile:
;   makensis /DVERSION=0.9.0 /DRUNTIME=<dist\stepeye-runtime> /DOUTFILE=<setup.exe> StepEye.nsi

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "0.9.0"
!endif
!ifndef RUNTIME
  !error "RUNTIME define is required"
!endif
!ifndef OUTFILE
  !define OUTFILE "StepEye-Setup.exe"
!endif
!ifndef ICON
  !define ICON "..\..\web\assets\stepeye.ico"
!endif

Name "StepEye"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\StepEye"
InstallDirRegKey HKCU "Software\StepEye" "InstallDir"
RequestExecutionLevel user
BrandingText "StepEye ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "StepEye"
VIAddVersionKey "FileDescription" "StepEye 工业视觉平台安装程序"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "StepEye"

SetCompressor /SOLID lzma
SetCompressorDictSize 64
SetDatablockOptimize on

!define MUI_ABORTWARNING
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_FINISHPAGE_RUN "$INSTDIR\start.bat"
!define MUI_FINISHPAGE_RUN_TEXT "启动 StepEye 演示服务"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "StepEye 运行环境" SecMain
  SetShellVarContext current
  ; 停掉正在运行的旧版本，失败不阻断安装。
  nsExec::Exec 'taskkill /IM stepeye-runtime.exe /F'

  ; 升级只替换程序文件，data 目录原地保留。
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\stepeye-runtime.exe"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\version.json"
  Delete "$INSTDIR\uninstall.exe"

  SetOutPath "$INSTDIR"
  File /r "${RUNTIME}\*.*"
  File "start.bat"
  File "README.txt"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  FileOpen $1 "$INSTDIR\version.json" w
  FileWrite $1 '{"version":"${VERSION}"}'
  FileClose $1

  WriteRegStr HKCU "Software\StepEye" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StepEye" "DisplayName" "StepEye"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StepEye" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StepEye" "DisplayIcon" "$INSTDIR\stepeye-runtime.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StepEye" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StepEye" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StepEye" "NoRepair" 1

  IfSilent shortcuts_done
  CreateDirectory "$SMPROGRAMS\StepEye"
  CreateShortCut "$SMPROGRAMS\StepEye\StepEye.lnk" "$INSTDIR\start.bat" "" "$INSTDIR\stepeye-runtime.exe" 0
  CreateShortCut "$DESKTOP\StepEye.lnk" "$INSTDIR\start.bat" "" "$INSTDIR\stepeye-runtime.exe" 0
  shortcuts_done:
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  nsExec::Exec 'taskkill /IM stepeye-runtime.exe /F'
  ; 卸载保留 data；只删程序文件，目录非空时保留。
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\stepeye-runtime.exe"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\version.json"
  Delete "$INSTDIR\uninstall.exe"
  Delete "$INSTDIR\start.bat"
  Delete "$INSTDIR\README.txt"
  Delete "$SMPROGRAMS\StepEye\StepEye.lnk"
  RMDir "$SMPROGRAMS\StepEye"
  Delete "$DESKTOP\StepEye.lnk"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\StepEye"
  DeleteRegKey HKCU "Software\StepEye"
SectionEnd
