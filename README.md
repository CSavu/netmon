# Connection monitor dashboard

Is it your internet, your router or your Wi-Fi? This logs the connection from your computer, around the clock, and shows
it live on a page only your computer can open, so you can tell which, and show your provider the evidence.

It pings your router and two internet hosts about twice a second, tests HTTPS and DNS every second, runs a traceroute
during outages, and tells cable from Wi-Fi by itself.

**Needs:** Windows 10 or 11, Python 3.10 or newer (standard library only), and Chrome or Edge for the PDF export.

## Start

From this folder, in PowerShell or Command Prompt:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Then open http://127.0.0.1:3333/ (`-Port 9000` picks another port). It starts whatever isn't already running and is safe
to run at any time. Run it again after the computer restarts.

The first run finds your router and writes `data\settings.json`. Check that the router it printed is right.

## Other commands

```powershell
powershell -ExecutionPolicy Bypass -File .\stop.ps1            # stops the dashboard, the monitors keep logging
powershell -ExecutionPolicy Bypass -File .\stop.ps1 -Monitors  # stops the monitors too
python -m pytest test_server.py -q
```

## Settings

`data\settings.json` holds what is specific to your home. Only `router` and `internet` are needed. The dashboard picks up
a change by itself; the monitors read `router`, `internet` and `box` when they start, so stop and start them after
changing one of those.

```json
{
  "router": "192.168.1.1",
  "internet": ["1.1.1.1", "8.8.8.8"],
  "locale": "en-GB",
  "box": { "address": "192.168.1.5", "name": "Mesh", "removed": "2026-01-31T18:00:00" },
  "restarts": [["2026-01-31T17:52:00", "2026-01-31T17:57:00"]]
}
```

- `router`: the address to test, your computer's default gateway unless you change it.
- `internet`: two hosts to ping and connect to on port 443.
- `locale`: how numbers and dates read. Left out, they follow your browser.
- `box`: a router, access point or mesh node between your computer and the main router. It's pinged too, and a minute in
  which it lost a ping isn't held against the line. Add `removed` when you take it out of the path.
- `restarts`: router restarts you did on purpose, which then don't count as problems.

Cable and Wi-Fi are recognized by themselves, from `monitors\link.ps1`. Nothing on your computer can tell a restart you
did or a box in the path, so those two are yours to mark.

## Where everything is

- `data\`: your settings and every log. It's git-ignored, so none of it reaches GitHub, and it has no backup: copy it
  somewhere safe if you want to keep it. It grows by about 20-25 MB a day.
- `monitors\`: the scripts that measure, and `netmon_data.py`, the rules for what counts as a problem.
- `server.py`, `index.html`: the dashboard. It listens on 127.0.0.1 only, and never writes to the logs.

## Picking a period

The presets are live. Dragging across any chart, or the From and To fields, fixes a period; the browser's Back button
returns to the previous one and the address can be bookmarked. The charts and tables follow the period. The banner and
the tiles above them always show right now.

## The commands drawer

**Commands** lists what the monitors ran in the period, newest first, rebuilt from their logs: a monitor's log row holds
the result of each command it ran, so the list needs no second log. Commands you run yourself aren't listed.

## Export

**Export** gives a PDF of the period (charts, tables, how each command is run, every traceroute, every failed command;
optionally every command, up to 20,000) and a CSV of every command. The PDF is the page itself, printed by your Chrome or
Edge with no window.

## Limits

- It measures only while the computer is awake. `monitors\keepawake.ps1` keeps it from idle sleep on mains power, but
  closing the lid can still put it to sleep.
- A ping counts as lost when no reply arrives within 1 second, stricter than the `ping` command's default of 4.
- The thresholds for a problem minute are this project's own, not an industry standard. They're shown on the page.
- The Wi-Fi signal is read from `netsh` in English: on a Windows in another language that one chart stays empty.
