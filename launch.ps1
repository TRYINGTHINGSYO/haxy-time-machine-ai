param(
  [ValidateSet('cpu','auto','gpu','deterministic')][string]$Mode = 'cpu',
  [string]$Model = 'qwen3.5:4b',
  [switch]$Doctor,
  [switch]$Legacy
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Install Python 3.10 or newer and enable its PATH option.' }
if ($Legacy) { & python time_machine.py; exit $LASTEXITCODE }
if ($Doctor) { & python -m htmai.app --mode $Mode --model $Model doctor }
else { & python -m htmai.app --mode $Mode --model $Model dashboard }
exit $LASTEXITCODE
