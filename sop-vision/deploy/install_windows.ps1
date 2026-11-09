<#
.SYNOPSIS
  安装 StepEye 便携包到本机目录,创建开始菜单快捷方式与卸载入口。
.DESCRIPTION
  只做文件安装,不注册 Windows 服务、不写系统目录、不修改防火墙。
  安装失败会删除半成品并恢复旧目录。
  升级请使用 upgrade_windows.ps1(带 SHA256 校验与回滚)。
.PARAMETER Zip
  发布 ZIP 路径。
.PARAMETER Dest
  安装目录,默认 %LOCALAPPDATA%\StepEye。
.PARAMETER Version
  版本号,写入 version.json。
.PARAMETER Sha256
  期望的 ZIP SHA256;提供时强校验。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1 -Zip .\stepeye-runtime.zip -Version 1.1.0
#>
param(
  [Parameter(Mandatory = $true)][string]$Zip,
  [string]$Dest = (Join-Path $env:LOCALAPPDATA "StepEye"),
  [Parameter(Mandatory = $true)][string]$Version,
  [string]$Sha256 = "",
  [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
$zipPath = (Resolve-Path $Zip).Path
if (-not (Test-Path $zipPath -PathType Leaf)) { throw "ZIP 不存在: $zipPath" }
if ($Sha256) {
  $actual = (Get-FileHash -Algorithm SHA256 -Path $zipPath).Hash.ToLowerInvariant()
  if ($actual -ne $Sha256.ToLowerInvariant()) { throw "SHA256 不匹配: $actual" }
}

$staging = "$Dest.staging"
$backup = $null
if (Test-Path $Dest) {
  $backup = "$Dest.bak-$(Get-Date -Format yyyyMMddHHmmss)"
  Write-Host "已有安装,先备份到 $backup"
  Move-Item -LiteralPath $Dest -Destination $backup
}
try {
  if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
  New-Item -ItemType Directory -Path $staging | Out-Null
  Expand-Archive -LiteralPath $zipPath -DestinationPath $staging -Force
  # ZIP 若只有一个顶层目录,把它作为安装根,避免多一层壳。
  $entries = @(Get-ChildItem -LiteralPath $staging)
  if ($entries.Count -eq 1 -and $entries[0].PSIsContainer) {
    Move-Item -LiteralPath $entries[0].FullName -Destination $Dest
    Remove-Item -LiteralPath $staging -Recurse -Force
  } else {
    Move-Item -LiteralPath $staging -Destination $Dest
  }
  @{ version = $Version.TrimStart("v", "V"); installed_at = (Get-Date).ToString("o") } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Dest "version.json") -Encoding UTF8

  if (-not $NoShortcut) {
    $exe = Get-ChildItem -LiteralPath $Dest -Filter "*.exe" -File | Select-Object -First 1
    if ($exe) {
      $shell = New-Object -ComObject WScript.Shell
      $startMenu = [Environment]::GetFolderPath("Programs")
      $link = $shell.CreateShortcut((Join-Path $startMenu "StepEye.lnk"))
      $link.TargetPath = $exe.FullName
      $link.WorkingDirectory = $Dest
      $link.Save()
    }
  }
} catch {
  Write-Warning "安装失败,正在恢复: $($_.Exception.Message)"
  if (Test-Path $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force -ErrorAction SilentlyContinue }
  if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue }
  if ($backup -and (Test-Path $backup)) { Move-Item -LiteralPath $backup -Destination $Dest }
  throw
}
Write-Host "已安装到 $Dest"
Write-Host "启动: 进入目录运行 start_windows.ps1,或直接运行 visual-server.exe"
Write-Host "卸载: 删除 $Dest 与开始菜单快捷方式即可(不写系统目录)"
