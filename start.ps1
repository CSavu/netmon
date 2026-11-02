# Starts whatever is not running: the connection monitors, the keep-awake helper and the dashboard, each as a hidden
# process that outlives this window. Then prints the dashboard's address. Safe to run at any time.
param([string]$NetmonDir = '<monitors folder>', [int]$Port = 8765)
$here = $PSScriptRoot
Remove-Item (Join-Path $NetmonDir 'stop') -ErrorAction SilentlyContinue
$running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | ForEach-Object { $_.CommandLine }
foreach ($s in 'netmon.ps1', 'diag.ps1', 'wifi.ps1', 'keepawake.ps1') {
    if (-not ($running | Where-Object { $_ -match [regex]::Escape($s) })) {
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$NetmonDir\$s`""
        "started $s"
    }
}
$up = $false
try { $up = (Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch {}
if (-not $up) {
    $env:NETMON_DIR = $NetmonDir
    $env:NETMON_DASHBOARD_PORT = "$Port"
    Start-Process python.exe -WindowStyle Hidden -WorkingDirectory $here -ArgumentList "`"$here\server.py`""
    "started the dashboard"
}
"http://127.0.0.1:$Port/"
