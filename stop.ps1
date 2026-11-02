# Stops the dashboard only; the monitors keep logging. -Monitors stops them too.
param([string]$NetmonDir = '<monitors folder>', [switch]$Monitors)
New-Item -ItemType File -Path (Join-Path $PSScriptRoot 'stop') -Force | Out-Null
if ($Monitors) { New-Item -ItemType File -Path (Join-Path $NetmonDir 'stop') -Force | Out-Null }
