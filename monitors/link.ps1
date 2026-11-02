# Logs every 5 s which link this laptop reaches the Digi router over, to ..\data\link.csv, so the setup periods in
# netmon_data.py follow cable and Wi-Fi by themselves. Stops when a file named "stop" appears next to it.
# link: Cable, Wi-Fi, none (no route at all) or other (a VPN or virtual adapter, or another network), and the last two
# say nothing about the setup. By the adapter's hardware, not its name: VirtualBox's adapter here is called "Ethernet 2".
$log = Join-Path (Split-Path $PSScriptRoot) 'data\link.csv'
if (-not (Test-Path $log)) { "time,link,address" | Out-File $log -Encoding utf8 }
while (-not (Test-Path (Join-Path $PSScriptRoot "stop"))) {
  $link = 'none'; $address = ''
  try {
    $route = Find-NetRoute -RemoteIPAddress 192.168.1.1 -ErrorAction Stop | Select-Object -First 1
    $adapter = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex -ErrorAction Stop
    $address = $route.IPAddress
    $link = if (-not $adapter.HardwareInterface -or $address -notlike '192.168.1.*') { 'other' }
            elseif ($adapter.PhysicalMediaType -eq 'Native 802.11') { 'Wi-Fi' }
            elseif ($adapter.PhysicalMediaType -eq '802.3') { 'Cable' }
            else { 'other' }
  } catch {}
  "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')),$link,$address" | Out-File $log -Append -Encoding utf8
  Start-Sleep -Seconds 5
}
