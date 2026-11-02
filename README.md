# Connection monitor dashboard

A live view of the home connection, on this laptop only: http://127.0.0.1:3333/

## Start

From this folder, in PowerShell or Command Prompt:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

It starts whatever isn't already running (the monitors, the keep-awake helper, the dashboard) and prints the address.
It's safe to run at any time, and it's needed again after the laptop restarts.

## Other commands

```powershell
powershell -ExecutionPolicy Bypass -File .\stop.ps1            # stops the dashboard, the monitors keep logging
powershell -ExecutionPolicy Bypass -File .\stop.ps1 -Monitors  # stops the monitors too
python -m pytest test_server.py -q
```

## Where everything is

- `data\`: every log and the evidence pack for Digi (`dovada_digi\`). It's git-ignored, so none of it reaches GitHub,
  and it has no backup: copy it somewhere safe if you want to keep it. It grows by about 20-25 MB a day.
- `monitors\`: the scripts that measure, the rules in `netmon_data.py`, and `make_digi_report.py`.
- `server.py`, `index.html`: the dashboard.

## How it works

`server.py` (Python standard library only) reads the logs the monitors write in `data\` as they grow, and
`index.html` asks it for
the current picture every 2 seconds. It listens on 127.0.0.1 only and never writes to those logs. It writes only
`server.log`.

What counts as a problem comes from `monitors\netmon_data.py`, the file the evidence pack for Digi is built from,
so both show the same numbers.

**Cable and Wi-Fi are recognized by themselves:** `monitors\link.ps1` logs every 5 seconds which link reaches the router,
and the dashboard reads everything again when it changes. Only a router restart, or putting a box like the Asus back
between the laptop and the router, still needs marking in `netmon_data.py`: nothing on the laptop can tell either one.

## Picking a period

The presets are live. Dragging across any chart, or the From and To fields, fixes a period; the browser's Back button
returns to the previous one and the address can be bookmarked. The charts and tables follow the period. The banner and
the tiles above them always show right now.

## The commands drawer

**Commands** lists what the monitors ran in the period, newest first, rebuilt from their logs: a monitor's log row holds
the result of each command it ran, so the list needs no second log and goes back to the first day. Commands run by hand or in a chat aren't listed.

## Export

**Export** gives a PDF of the period (charts, tables, how each command is run, every traceroute, every failed command;
optionally every command, up to 20.000) and a CSV of every command. The PDF is the page itself, printed by the Chrome or
Edge on this laptop with no window. It needs one of them installed.
