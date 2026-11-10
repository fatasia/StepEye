param([string]$Config = "configs/demo_sim.yaml", [int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
python -m visual serve $Config --host 0.0.0.0 --port $Port --data data --auth
exit $LASTEXITCODE
