# Dot-sourced by the monitors: what is specific to this machine, read from ..\data\settings.json.
$DataDir = Join-Path (Split-Path $PSScriptRoot) 'data'
$SettingsFile = Join-Path $DataDir 'settings.json'
$Settings = if (Test-Path $SettingsFile) { Get-Content $SettingsFile -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
$Router = if ($Settings.router) { $Settings.router } else { '192.168.1.1' }
$Internet = if ($Settings.internet) { @($Settings.internet) } else { @('1.1.1.1', '8.8.8.8') }
# a box between this computer and the router, pinged until it is marked as removed
$BoxInPath = @(if ($Settings.box.address -and -not $Settings.box.removed) { $Settings.box.address })
