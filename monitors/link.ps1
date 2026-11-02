# Logs every 5 s which link this computer reaches the router over, to ..\data\link.csv, so the setup periods in
# netmon_data.py follow cable and Wi-Fi by themselves. Stops when a file named "stop" appears next to it.
# link: Cable, Wi-Fi, none (no route at all) or other (a VPN or virtual adapter, or another network), and the last two
# say nothing about the setup. By the adapter's hardware, not its name, which differs per machine and language.
. (Join-Path $PSScriptRoot 'settings.ps1')
$log = Join-Path $DataDir 'link.csv'
if (-not (Test-Path $log)) { "time,link,address" | Out-File $log -Encoding utf8 }
while (-not (Test-Path (Join-Path $PSScriptRoot "stop"))) {
  $link = 'none'; $address = ''
  try {
    $found = Find-NetRoute -RemoteIPAddress $Router -ErrorAction Stop
    $address = ($found | Select-Object -First 1).IPAddress
    $route = $found | Select-Object -Last 1
    $adapter = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex -ErrorAction Stop
    # a next hop means the router isn't on this network: this computer is somewhere else
    $link = if (-not $adapter.HardwareInterface -or $route.NextHop -ne '0.0.0.0') { 'other' }
            elseif ($adapter.PhysicalMediaType -eq 'Native 802.11') { 'Wi-Fi' }
            elseif ($adapter.PhysicalMediaType -eq '802.3') { 'Cable' }
            else { 'other' }
  } catch {}
  "$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss.fff')),$link,$address" | Out-File $log -Append -Encoding utf8
  Start-Sleep -Seconds 5
}
