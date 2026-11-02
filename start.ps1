# Starts whatever is not running: the connection monitors, the keep-awake helper and the dashboard, each as a hidden
# process that outlives this window. Then prints the dashboard's address. Safe to run at any time.
param([int]$Port = 3333)
$here = $PSScriptRoot
$monitors = Join-Path $here 'monitors'
New-Item -ItemType Directory -Force -Path (Join-Path $here 'data') | Out-Null
Remove-Item (Join-Path $monitors 'stop') -ErrorAction SilentlyContinue
$running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | ForEach-Object { $_.CommandLine }
foreach ($s in 'netmon.ps1', 'diag.ps1', 'wifi.ps1', 'link.ps1', 'keepawake.ps1') {
    if (-not ($running | Where-Object { $_ -match [regex]::Escape("monitors\$s") })) {
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$monitors\$s`""
        "started $s"
    }
}
$up = $false
try { $up = (Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch {}
if (-not $up) {
    $env:NETMON_DASHBOARD_PORT = "$Port"
    Start-Process python.exe -WindowStyle Hidden -WorkingDirectory $here -ArgumentList "`"$here\server.py`""
    "started the dashboard"
}
"http://127.0.0.1:$Port/"
