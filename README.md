# Connection monitor dashboard

A live view of the home connection, on this laptop only: http://127.0.0.1:8765/

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1           # starts anything not running: the monitors, the keep-awake helper, the dashboard
powershell -ExecutionPolicy Bypass -File .\stop.ps1            # stops the dashboard, the monitors keep logging
powershell -ExecutionPolicy Bypass -File .\stop.ps1 -Monitors  # stops the monitors too
python -m pytest test_server.py -q
```

`server.py` (Python standard library only) reads the logs the monitors write in
`<monitors folder>` (`NETMON_DIR`) as they grow, and `index.html` asks it for
the current picture every 2 seconds. It listens on 127.0.0.1 only and never writes to those logs. It writes `server.log`
and `commands.csv`, the record of the one command it runs itself (`ipconfig`, every 10 seconds, to see which link the
laptop is on).

What counts as a problem comes from that folder's `netmon_data.py`, the file the evidence pack for Digi is built from,
so both show the same numbers. When the setup changes (Wi-Fi or cable, a router restart), mark it there; the dashboard
sees the file change and reads everything again.

## Picking a period

The presets are live. Dragging across any chart, or the From and To fields, fixes a period; the browser's Back button
returns to the previous one and the address can be bookmarked. The charts and tables follow the period. The banner and
the tiles above them always show right now.

## The commands drawer

**Commands** lists what the monitors ran in the period, newest first, rebuilt from their logs: a monitor's log row holds
the result of each command it ran, so the list needs no second log and goes back to the first day. `ipconfig` is
recorded only since this feature was added. Commands run by hand or in a chat aren't listed.

## Export

**Export** gives a PDF of the period (charts, tables, how each command is run, every traceroute, every failed command;
optionally every command, up to 20.000) and a CSV of every command. The PDF is the page itself, printed by the Chrome or
Edge on this laptop with no window. It needs one of them installed.
