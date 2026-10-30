param([string]$GameExe = 'F:\World of Warcraft\_retail_\Wow.exe')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '找不到项目 Python 环境' }
$env:PYTHONIOENCODING = 'utf-8'
& $python -u (Join-Path $PSScriptRoot 'receive.py') --exe $GameExe --output (Join-Path $root 'data/memory-export')
