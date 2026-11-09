<#
.SYNOPSIS
  校验离线升级包并原子切换 StepEye 安装目录,失败自动回滚。
.DESCRIPTION
  升级清单格式: {"version":"1.1.0","archive":"stepeye-1.1.0.zip","sha256":"...","size":123}
  归档相对清单所在目录;也支持 http(s) URL(先下载到临时目录)。
  会停止指定进程,切换后尝试从新目录重启同名 exe。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\upgrade_windows.ps1 -Manifest .\stepeye-1.1.0.manifest.json
#>
param(
  [Parameter(Mandatory = $true)][string]$Manifest,
  [string]$Current = (Join-Path $env:LOCALAPPDATA "StepEye"),
  [string]$Python = "python",
  [string]$ProcessName = "visual-server"
)

$ErrorActionPreference = "Stop"
$manifestPath = (Resolve-Path $Manifest).Path
$spec = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if (-not $spec.version -or -not $spec.archive -or -not $spec.sha256) {
  throw "清单缺少 version/archive/sha256"
}
$archive = [string]$spec.archive
if ($archive -match '^https?://') {
  $download = Join-Path $env:TEMP ("stepeye-upgrade-" + [IO.Path]::GetFileName($archive))
  Write-Host "下载升级包 $archive"
  Invoke-WebRequest -Uri $archive -OutFile $download -UseBasicParsing
  $archive = $download
} elseif (-not [IO.Path]::IsPathRooted($archive)) {
  $archive = Join-Path (Split-Path -Parent $manifestPath) $archive
}
if (-not (Test-Path $archive -PathType Leaf)) { throw "升级包不存在: $archive" }

$stamp = Get-Date -Format yyyyMMddHHmmss
$staging = "$Current.upgrade-$stamp"
$backup = "$Current.bak-$stamp"
if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }

$stopped = @()
Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | ForEach-Object {
  $stopped += $_.Path
  Stop-Process -Id $_.Id -Force
  Write-Host "已停止进程 $($_.Id)"
}

# 从临时目录运行,避免 Windows 因当前目录被占用而无法 rename。
Push-Location $env:TEMP
try {
  $env:PYTHONPATH = $Current
  & $Python -m visual upgrade --manifest $manifestPath --archive $archive `
    --current $Current --staging $staging --backup $backup
  if ($LASTEXITCODE -ne 0) { throw "升级命令失败,退出码 $LASTEXITCODE(旧版本仍在 $Current)" }
} catch {
  Write-Warning "升级失败,尝试重启旧版本: $($_.Exception.Message)"
  $oldExe = Get-ChildItem -LiteralPath $Current -Filter "*.exe" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'visual|stepeye' } | Select-Object -First 1
  if ($oldExe) { Start-Process -FilePath $oldExe.FullName -WorkingDirectory $Current }
  throw
} finally {
  Pop-Location
  Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
}

$exe = Get-ChildItem -LiteralPath $Current -Filter "*.exe" -File |
  Where-Object { $_.Name -match 'visual|stepeye' } | Select-Object -First 1
if ($exe) {
  Start-Process -FilePath $exe.FullName -WorkingDirectory $Current
  Write-Host "已从 $($exe.FullName) 重启"
} else {
  Write-Host "未找到可执行文件,请手动运行 start_windows.ps1"
}
Write-Host "升级完成: $($spec.version);旧版本备份在 $backup"
