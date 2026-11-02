# Richer connection diagnostic, beside netmon.ps1: full date in every row, and REAL traffic
# Stops when a file named "stop" appears next to it.
# (TCP connects on port 443, DNS lookups through the router) besides ping, so the log shows
# more than ICMP. When the internet fails, a short traceroute shows where the path stops.
$dir = Join-Path (Split-Path $PSScriptRoot) 'data'
$log = Join-Path $dir 'diag.csv'
$traceLog = Join-Path $dir 'diag_traceroute.txt'
if (-not (Test-Path $log)) { "time,router_ping_ms,tcp_1111_ms,tcp_8888_ms,dns_ms,link" | Out-File $log -Encoding utf8 }
$ping = New-Object System.Net.NetworkInformation.Ping
$lastTrace = [datetime]::MinValue

function PingMs($ip) {
  try { $r = $ping.Send($ip, 1000); if ($r.Status -eq 'Success') { return $r.RoundtripTime } } catch {}
  return -1
}
function TcpMs($ip, $port) {
  $c = New-Object System.Net.Sockets.TcpClient
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try { if ($c.ConnectAsync($ip, $port).Wait(2000) -and $c.Connected) { return $sw.ElapsedMilliseconds } } catch {} finally { $c.Close() }
  return -1
}
function DnsMs($name) {
  # "no such name" (11001) is an ANSWER from the DNS server, so it counts as working; only silence fails
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $t = [System.Net.Dns]::GetHostAddressesAsync($name)
  try { if ($t.Wait(2000)) { return $sw.ElapsedMilliseconds } } catch {
    $inner = $_.Exception.InnerException; while ($inner -and -not ($inner -is [System.Net.Sockets.SocketException])) { $inner = $inner.InnerException }
    if ($inner -and $inner.ErrorCode -eq 11001) { return $sw.ElapsedMilliseconds }
  }
  return -1
}

while (-not (Test-Path (Join-Path $PSScriptRoot "stop"))) {
  $now = Get-Date
  $router = PingMs '192.168.1.1'
  $t1 = TcpMs '1.1.1.1' 443
  $t2 = TcpMs '8.8.8.8' 443
  # a fresh random name each time, so the answer cannot come from the PC's DNS cache
  $dns = DnsMs ("r{0}.example.com" -f (Get-Random))
  # the link the PC is on: Up while the cable or the Wi-Fi is connected (the Wi-Fi's own state is in wifi.csv)
  $link = if (Get-NetAdapter -Name Ethernet,Wi-Fi -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up') { 'Up' } else { 'Disconnected' }
  "$($now.ToString('yyyy-MM-dd HH:mm:ss.fff')),$router,$t1,$t2,$dns,$link" | Out-File $log -Append -Encoding utf8
  if ($t1 -lt 0 -and $t2 -lt 0 -and ($now - $lastTrace).TotalSeconds -ge 60) {
    $lastTrace = $now
    "===== $($now.ToString('yyyy-MM-dd HH:mm:ss')) internet down (router ping: $router ms) =====" | Out-File $traceLog -Append -Encoding utf8
    (tracert -d -h 6 -w 500 8.8.8.8) | Out-File $traceLog -Append -Encoding utf8
  }
  Start-Sleep -Milliseconds 1000
}
