param(
    [string[]]$Source,
    [switch]$Preview,
    [switch]$PrepareOnly,
    [string]$Config
)
$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path -Parent $PSScriptRoot
if (-not $Config) { $Config = Join-Path $taskRepo 'deploy/update.local.json' }
$taskPython = Join-Path $taskRepo '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $Config)) { throw 'Configure deploy/update.local.json first. See deploy/AUTOMATIC_UPDATES.md.' }
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'The project Python environment is missing; run uv sync --group compact.' }
$taskArgs = @('-X', 'utf8', '-m', 'compact_market.update', '--config', $Config)
if ($Source) {
    foreach ($taskSource in $Source) { $taskArgs += @('--source', (Resolve-Path -LiteralPath $taskSource).Path) }
} else { $taskArgs += '--discover-client' }
if ($Preview) { $taskArgs += '--dry-run' }
if (-not $PrepareOnly -and -not $Preview) { $taskArgs += '--publish' }
$taskPreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $taskRepo 'backend'
    Push-Location $taskRepo
    Write-Host '正在检查本地快照；仅同步配置内服务器的新数据。'
    $taskOutput = & $taskPython @taskArgs | Out-String
    $taskExit = $LASTEXITCODE
    if ($taskExit -eq 0) {
        $taskResult = $taskOutput | ConvertFrom-Json
        if ($Preview) {
            Write-Host ('预检完成：发现 {0} 份新快照。' -f @($taskResult.sources).Count)
            $taskResult.sources | ForEach-Object { Write-Host $_ }
        } elseif ($taskResult.phase -eq 'unchanged') {
            Write-Host '没有发现新快照，线上数据保持不变。'
        } elseif ($taskResult.phase -eq 'published') {
            Write-Host ('同步成功，线上已更新。任务：{0}' -f $taskResult.id)
        } elseif ($taskResult.phase -eq 'ready') {
            Write-Host ('候选数据已验收，尚未发布。任务：{0}' -f $taskResult.id)
        } else { Write-Host $taskOutput }
    } else { Write-Host '同步未完成，请查看上方错误；不会用未验收的数据替换线上版本。' }
} finally {
    Pop-Location
    $env:PYTHONPATH = $taskPreviousPythonPath
}
exit $taskExit
