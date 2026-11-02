# Keeps the PC from idle sleep while the monitors run, only on mains power, through SetThreadExecutionState: no
# power setting changes, and the request ends with this process. Closing the lid can still put the PC to sleep.
# Stops when a file named "stop" appears next to it.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
$CONTINUOUS = [uint32]2147483648
$SYSTEM_REQUIRED = [uint32]1
while (-not (Test-Path (Join-Path $PSScriptRoot "stop"))) {
  if ([System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus -eq 'Online') {
    [void][Win32.Power]::SetThreadExecutionState($CONTINUOUS -bor $SYSTEM_REQUIRED)
  } else {
    [void][Win32.Power]::SetThreadExecutionState($CONTINUOUS)
  }
  Start-Sleep -Seconds 30
}
[void][Win32.Power]::SetThreadExecutionState($CONTINUOUS)
