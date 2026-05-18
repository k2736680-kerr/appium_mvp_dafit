param(
    [string]$TaskName = "AppiumMvpNightlyRun",
    [string]$ServerTaskName = "AppiumMvpReportServer",
    [string]$RunTime = "23:30",
    [int]$ReportServerPort = 8876,
    [switch]$ReportServerOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ServerCmd = Join-Path $ProjectRoot "scripts\start_report_server.cmd"
$RunCmd = Join-Path $ProjectRoot "scripts\run_nightly.cmd"

if (-not (Test-Path $PythonExe)) {
    throw "未找到虚拟环境 Python: $PythonExe"
}

$ServerArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"Set-Location -LiteralPath '$ProjectRoot'; & '$PythonExe' 'scripts\report_server.py' --host 0.0.0.0 --port $ReportServerPort`""
$ServerAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ServerArgs
$ServerTrigger = New-ScheduledTaskTrigger -AtLogOn
$ServerSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 30) `
    -MultipleInstances IgnoreNew `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1)

try {
    Register-ScheduledTask `
        -TaskName $ServerTaskName `
        -Action $ServerAction `
        -Trigger $ServerTrigger `
        -Settings $ServerSettings `
        -Description "Serve Appium MVP HTML reports for DingTalk links" `
        -Force | Out-Null
} catch {
    Write-Host "Register-ScheduledTask 创建报告服务失败, 改用 schtasks 用户级任务: $($_.Exception.Message)"
    schtasks /Create /TN $ServerTaskName /SC ONLOGON /TR $ServerCmd /F | Out-Null
}

$fwName = "Appium MVP Report Server TCP $ReportServerPort"
try {
    if (-not (Get-NetFirewallRule -DisplayName $fwName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $fwName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $ReportServerPort -Profile Domain,Private | Out-Null
        Write-Host "已添加入站防火墙规则 (Domain/Private): $fwName"
    }
} catch {
    Write-Host "未添加入站防火墙 (其他电脑访问 8876 常被拦截). 请以管理员重跑本脚本, 或手动放行 TCP $ReportServerPort : $($_.Exception.Message)"
}

if (-not $ReportServerOnly) {
    $RunArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"Set-Location -LiteralPath '$ProjectRoot'; & '$PythonExe' 'scripts\nightly_run.py' --notify auto`""
    $RunAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $RunArgs
    $RunTrigger = New-ScheduledTaskTrigger -Daily -At $RunTime
    $RunSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)

    try {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $RunAction `
            -Trigger $RunTrigger `
            -Settings $RunSettings `
            -Description "Run Appium MVP recordings nightly and notify DingTalk within the allowed window" `
            -Force | Out-Null
    } catch {
        Write-Host "Register-ScheduledTask 创建夜间任务失败, 改用 schtasks 用户级任务: $($_.Exception.Message)"
        schtasks /Create /TN $TaskName /SC DAILY /ST $RunTime /TR $RunCmd /F | Out-Null
    }
}

Write-Host "已创建任务: $ServerTaskName (每次登录 Windows 时后台启动报告 HTTP 服务, 供钉钉链接访问)"
if (-not $ReportServerOnly) {
    Write-Host "已创建任务: $TaskName (每天 $RunTime 自动运行)"
}
Write-Host "报告服务地址示例: http://本机IP:$ReportServerPort/recording_report_bundle_xxx/recording_report.html"
Write-Host "打开 taskschd.msc (任务计划程序), 搜索上述任务名, 确认状态为就绪; 安装后可注销重登或右键任务选择运行以立即启动。"
