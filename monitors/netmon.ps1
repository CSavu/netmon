# Pings the DIGI router and two internet hosts in turn (1 s timeout, 0,5 s pause per round), logs every result. The Asus (192.168.1.5) is off since 2026-11-02 and no longer pinged; the setup per period is in netmon_data.py.
# Stops when a file named "stop" appears next to it.
# Appends to ..\data\netmon.csv (a restart never wipes it). Rows written before 2026-10-19 22:30 carry the time only;
# later rows carry the full date.
$targets = @('192.168.1.1','1.1.1.1','8.8.8.8')
$log = Join-Path (Split-Path $PSScriptRoot) 'data\netmon.csv'
if (-not (Test-Path $log)) { "time,target,ok,ms" | Out-File $log -Encoding utf8 }
$p = New-Object System.Net.NetworkInformation.Ping
while (-not (Test-Path (Join-Path $PSScriptRoot "stop"))) {
  foreach ($t in $targets) {
    try { $r = $p.Send($t, 1000); $ok = ($r.Status -eq 'Success'); $ms = $r.RoundtripTime } catch { $ok = $false; $ms = -1 }
    "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')),$t,$ok,$ms" | Out-File $log -Append -Encoding utf8
  }
  Start-Sleep -Milliseconds 500
}
