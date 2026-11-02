# Pings the router, any box in between and the internet hosts in turn (1 s timeout, 0.5 s pause per round), and logs
# every result to ..\data\netmon.csv. A restart appends and never wipes it. Stops when a file named "stop" appears next to it.
. (Join-Path $PSScriptRoot 'settings.ps1')
$targets = @($Router) + $BoxInPath + $Internet
$log = Join-Path $DataDir 'netmon.csv'
if (-not (Test-Path $log)) { "time,target,ok,ms" | Out-File $log -Encoding utf8 }
$p = New-Object System.Net.NetworkInformation.Ping
while (-not (Test-Path (Join-Path $PSScriptRoot "stop"))) {
  foreach ($t in $targets) {
    try { $r = $p.Send($t, 1000); $ok = ($r.Status -eq 'Success'); $ms = $r.RoundtripTime } catch { $ok = $false; $ms = -1 }
    "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')),$t,$ok,$ms" | Out-File $log -Append -Encoding utf8
  }
  Start-Sleep -Milliseconds 500
}
