# Logs the Wi-Fi link every 5 s to ..\data\wifi.csv: state, access point, channel, signal and rates, so a drop can be told
# from a weak or dropped radio link. Stops when a file named "stop" appears next to it.
# Only "show interfaces": a repeated "show networks" can trigger scans, which themselves cause latency spikes.
$log = Join-Path (Split-Path $PSScriptRoot) 'data\wifi.csv'
if (-not (Test-Path $log)) { "time,state,bssid,channel,signal_pct,rssi,rx_mbps,tx_mbps" | Out-File $log -Encoding utf8 }
function Field($lines, $name) {
  foreach ($l in $lines) { if ($l -match "^\s*$name\s*:\s*(.+?)\s*$") { return $Matches[1] } }
  return ''
}
while (-not (Test-Path (Join-Path $PSScriptRoot "stop"))) {
  $o = netsh wlan show interfaces
  $row = @((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff'), (Field $o 'State'), (Field $o 'AP BSSID'), (Field $o 'Channel'),
           ((Field $o 'Signal') -replace '%',''), (Field $o 'Rssi'), (Field $o 'Receive rate \(Mbps\)'), (Field $o 'Transmit rate \(Mbps\)'))
  ($row -join ',') | Out-File $log -Append -Encoding utf8
  Start-Sleep -Seconds 5
}
