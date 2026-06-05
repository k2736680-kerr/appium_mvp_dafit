param(
    [string]$HostName = "0.0.0.0",
    [int]$Port = 8876
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "reports"
$LogPath = Join-Path $LogDir "report_server.log"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location -LiteralPath $ProjectRoot

& $PythonExe "scripts\report_server.py" --host $HostName --port $Port *> $LogPath
