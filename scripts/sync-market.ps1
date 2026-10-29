param(
    [string[]]$Source,
    [switch]$Preview,
    [switch]$PrepareOnly,
    [switch]$Verify,
    [string]$Config
)
$ErrorActionPreference = 'Stop'
if ($Verify -and ($Preview -or $PrepareOnly -or $Source)) { throw '-Verify cannot be combined with -Source, -Preview or -PrepareOnly.' }
$taskRepo = Split-Path -Parent $PSScriptRoot
if (-not $Config) { $Config = Join-Path $taskRepo 'deploy/update.local.json' }
$taskPython = Join-Path $taskRepo '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $Config)) { throw 'Configure deploy/update.local.json first. See deploy/AUTOMATIC_UPDATES.md.' }
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'The project Python environment is missing; run uv sync --group compact.' }
$taskArgs = @('-X', 'utf8', '-m', 'compact_market.update', '--config', $Config)
if ($Verify) { $taskArgs += '--verify' }
elseif ($Source) {
    foreach ($taskSource in $Source) { $taskArgs += @('--source', (Resolve-Path -LiteralPath $taskSource).Path) }
} else { $taskArgs += '--discover-client' }
if ($Preview) { $taskArgs += '--dry-run' }
if (-not $PrepareOnly -and -not $Preview -and -not $Verify) { $taskArgs += '--publish' }
$taskPreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $taskRepo 'backend'
    Push-Location $taskRepo
    Write-Host $(if ($Verify) { '正在核验已发布数据，不导入或上传快照。' } else { '正在检查本地新快照。' })
    $taskOutput = & $taskPython @taskArgs | Out-String
    $taskExit = $LASTEXITCODE
    if ($taskExit -eq 0) {
        $taskResult = $taskOutput | ConvertFrom-Json
        if ($Preview) {
            Write-Host ('预检完成：发现 {0} 份新快照。' -f @($taskResult.sources).Count)
            if ($taskResult.observations) {
                $taskResult.observations | ForEach-Object {
                    Write-Host ('  {0}  {1}' -f $_.realm, ([DateTimeOffset]::Parse($_.scanned_at).ToLocalTime().ToString('MM/dd HH:mm:ss')))
                }
            } else { $taskResult.sources | ForEach-Object { Write-Host $_ } }
            if (@($taskResult.new_realms).Count -gt 0 -and $taskResult.new_realms) {
                Write-Host ('其中 {0} 个新服务器将自动接入。' -f @($taskResult.new_realms).Count)
            }
            $taskBlocked = @($taskResult.skipped | Where-Object reason -eq 'realm_not_configured')
            if ($taskBlocked.Count) { Write-Warning ('另有 {0} 份快照被服务器白名单排除。' -f $taskBlocked.Count) }
        } elseif ($taskResult.phase -eq 'unchanged') {
            Write-Host '没有发现新快照，线上数据保持不变。'
        } elseif ($taskResult.phase -eq 'published') {
            Write-Host ('同步成功，线上已更新。任务：{0}' -f $taskResult.id)
            if ($taskResult.summary) {
                Write-Host ('新增 {0} 份；当前共 {1} 份来源快照、{2} 条原始记录。' -f $taskResult.summary.added_snapshot_count,$taskResult.summary.source_snapshot_count,$taskResult.summary.source_record_count)
                $taskResult.summary.added_snapshots | ForEach-Object { Write-Host ('  {0}：{1} 条记录' -f $_.realm,$_.record_count) }
            }
            if ($taskResult.publication.verification) { Write-Host ('发布后核验通过：{0} 个服务器及全服市场。' -f $taskResult.publication.verification.realm_count) }
        } elseif ($taskResult.phase -eq 'verified') {
            Write-Host ('线上核验通过：{0} 个服务器、{1} 份来源快照、{2} 条原始记录。' -f $taskResult.verification.realm_count,$taskResult.source_snapshot_count,$taskResult.source_record_count)
        } elseif ($taskResult.phase -eq 'ready') {
            Write-Host ('候选数据已验收，尚未发布。任务：{0}' -f $taskResult.id)
        } else { Write-Host $taskOutput }
    } else { Write-Host '同步未完成，请查看上方错误；不会用未验收的数据替换线上版本。' }
} finally {
    Pop-Location
    $env:PYTHONPATH = $taskPreviousPythonPath
}
exit $taskExit
