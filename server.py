"""Live dashboard over the connection monitors' logs. Python standard library only.

Reads netmon.csv, diag.csv and wifi.csv as they grow and serves one page on 127.0.0.1. It never writes to those logs.
The only command it runs itself is ipconfig (to see which link the laptop is on), which it records in commands.csv.
PDF reports are printed by the Chrome or Edge already installed on this laptop.

Every rule about what counts as a problem comes from the monitors' own netmon_data.py (loaded from NETMON_DIR and
loaded again when that file changes), so these numbers are the ones that go in the evidence pack.
"""
import bisect
import csv
import importlib.util
import io
import json
import logging
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

HERE = Path(__file__).parent
NETMON_DIR = Path(os.environ.get("NETMON_DIR", r"<monitors folder>"))
HOST, PORT = "127.0.0.1", int(os.environ.get("NETMON_DASHBOARD_PORT", "8765"))
PRESETS = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "all": None}
STEPS = [1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400]
MAX_BUCKETS = 300
RAW_KEEP = timedelta(hours=24, minutes=10)
STALE_SECONDS = 15
TIMEOUT_MS = 2000  # what diag.ps1 gives a connection or a DNS lookup
CHECKPOINT_EVERY = 1000
REPORT_ALL_LIMIT = 20000  # commands one PDF lists one by one; past it they go in the CSV
REPORT_FAILED_LIMIT = 5000
PDF_TIMEOUT_SECONDS = 300

KINDS = {
    "ping": {"label": "Ping", "command": "Ping.Send('{target}', 1000)", "script": "netmon.ps1",
             "every": "about every 0,55 s per address",
             "note": "Same as: ping -n 1 -w 1000 <address>. Addresses: the Digi router, 1.1.1.1, 8.8.8.8 "
                     "(and the Asus, 192.168.1.5, until 2 November)."},
    "diag_ping": {"label": "Router ping", "command": "Ping.Send('192.168.1.1', 1000)", "script": "diag.ps1",
                  "every": "about every second", "note": "A second ping to the router, beside each connection test."},
    "tcp": {"label": "HTTPS connection", "command": "TcpClient.ConnectAsync('{target}', 443).Wait(2000)",
            "script": "diag.ps1", "every": "about every second",
            "note": "Opens the TCP connection on port 443 and closes it. Downloads nothing."},
    "dns": {"label": "DNS lookup", "command": "Dns.GetHostAddressesAsync('r<random>.example.com').Wait(2000)",
            "script": "diag.ps1", "every": "about every second",
            "note": "A new random name each time, so the PC's cache can't answer. \"No such name\" counts as an answer."},
    "adapter": {"label": "Network link", "command": "Get-NetAdapter -Name Ethernet,Wi-Fi", "script": "diag.ps1",
                "every": "about every second", "note": "Until 2 November it read the Ethernet adapter only. Failed here means the link read as down."},
    "wifi": {"label": "Wi-Fi status", "command": "netsh wlan show interfaces", "script": "wifi.ps1",
             "every": "every 5 s", "note": "Since 2 November. Failed here means Wi-Fi was not connected."},
    "tracert": {"label": "Traceroute", "command": "tracert -d -h 6 -w 500 8.8.8.8", "script": "diag.ps1",
                "every": "during outages, at most once a minute", "note": "Runs when both HTTPS tests fail, so every one counts as failed. Since 19 October."},
    "ipconfig": {"label": "ipconfig", "command": "ipconfig", "script": "server.py (this dashboard)",
                 "every": "every 10 s", "note": "Finds which link the laptop is on. Recorded since 2 November."},
}

log = logging.getLogger("netmon-dashboard")


def load_rules():
    spec = importlib.util.spec_from_file_location("netmon_data", NETMON_DIR / "netmon_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ms_of(ts):
    return round(ts.timestamp() * 1000)


def of_ms(ms):
    return datetime.fromtimestamp(ms / 1000)


def minute_of(ts):
    return ts.replace(second=0, microsecond=0)


def step_for(seconds):
    """Seconds per chart point: the finest step that keeps the chart under MAX_BUCKETS points."""
    for step in STEPS:
        if seconds / step <= MAX_BUCKETS:
            return step
    return STEPS[-1]


class Track:
    """One of the two sets of rows netmon_data keeps apart (wired or Wi-Fi), built row by row."""

    def __init__(self, nd):
        self.nd = nd
        self.spans, self.mins = [], {}
        self.runs = []  # consecutive rounds with no answer from any internet host
        self._round = None
        self._run = None

    def feed(self, ts, target, ok, ms):
        self.nd.extend_sessions(self.spans, ts)
        self.nd.add_ping(self.mins, len(self.spans) - 1, ts, target, ok, ms)
        if target == self.nd.ROUTER:
            self._close_round()
            self._round = {"t": ts}
        elif self._round is not None:
            self._round[target] = ok

    def _close_round(self):
        r = self._round
        if r is None:
            return
        if all(r.get(h) is False for h in self.nd.NET):
            if self._run is None:
                self._run = {"down": [], "end": None}
                self.runs.append(self._run)
            self._run["down"].append(r["t"])
        elif self._run is not None:
            self._run["end"] = r["t"]
            self._run = None

    def settle(self):
        self.nd.mark_excluded(self.mins, [tuple(s) for s in self.spans])

    def outages(self):
        """[(start, end, seconds)], longest first: netmon_data.outages, without keeping every row."""
        out = []
        for run in self.runs:
            if run["end"] is None:  # still down, not finished yet
                continue
            start = None
            for t in run["down"]:
                m = self.mins.get(minute_of(t))
                if m is not None and not m.excluded:
                    start = start or t
                elif start:
                    out.append((start, t, (t - start).total_seconds()))
                    start = None
            if start:
                out.append((start, run["end"], (run["end"] - start).total_seconds()))
        return sorted(out, key=lambda x: -x[2])


def clean_line(raw):
    # a monitor killed mid-write leaves a run of NUL bytes
    line = raw.replace(b"\x00", b"").decode("utf-8", "replace").strip().lstrip("\ufeff")
    return "" if line.startswith("time,") else line


class LogTail:
    """Yields (byte offset, line) for the complete lines added since the last read. Holds the file only for the read."""

    def __init__(self, path):
        self.path, self.offset, self.rest = path, 0, b""

    def read(self):
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset, self.rest = 0, b""
            if size == self.offset:
                return []
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
                begin = self.offset - len(self.rest)
                self.offset = f.tell()
        except OSError:
            return []
        lines = (self.rest + data).split(b"\n")
        self.rest = lines.pop()
        out, pos = [], begin
        for raw in lines:
            line = clean_line(raw)
            if line:
                out.append((pos, line))
            pos += len(raw) + 1
        return out


def parse_ping(line, nd, day, prev):
    """(ts, target, ok, ms), day. Rows written before 19 October carry the time of day only."""
    c = line.split(",")
    if len(c) != 4 or c[2] not in ("True", "False"):
        return None, day
    try:
        ts, new_day = nd._ts(c[0], day, prev)
        return (ts, c[1], c[2] == "True", int(c[3])), new_day or ts.date()
    except ValueError:
        return None, day


def parse_diag(line):
    c = line.split(",")
    try:
        return datetime.fromisoformat(c[0]), int(c[1]), int(c[2]), int(c[3]), int(c[4]), c[5]
    except (IndexError, ValueError):
        return None


def parse_radio(line):
    c = line.split(",")
    try:
        return (datetime.fromisoformat(c[0]), c[1], c[2], c[3], int(c[4]) if c[4] else None,
                int(c[5]) if c[5] else None, c[6], c[7])
    except (IndexError, ValueError):
        return None


def parse_ipconfig(line):
    c = line.split(",", 1)
    try:
        return datetime.fromisoformat(c[0]), c[1]
    except (IndexError, ValueError):
        return None


class LogFile:
    def __init__(self, path):
        self.path, self.tail, self.count = path, LogTail(path), 0
        self.times, self.marks = [], []  # sparse index: (time, byte offset and day to start reading from)

    def mark(self, ts, offset, day=None, prev=None):
        if self.count % CHECKPOINT_EVERY == 0:
            self.times.append(ts)
            self.marks.append((offset, day, prev))
        self.count += 1


class Store:
    def __init__(self):
        self.nd = load_rules()
        self.wired, self.wifi = Track(self.nd), Track(self.nd)
        self.pings, self.diag, self.radio = deque(), deque(), deque()
        self.lock = threading.RLock()
        self.ready = False
        self.files = {
            "netmon": LogFile(NETMON_DIR / "netmon.csv"), "diag": LogFile(NETMON_DIR / "diag.csv"),
            "wifi": LogFile(NETMON_DIR / "wifi.csv"), "ipconfig": LogFile(HERE / "commands.csv"),
        }
        self.counts = {}  # minute -> Counter of commands run, "<kind>!" for the failed ones
        self.diag_min, self.signal_min = {}, {}
        self.traces, self._trace_mtime = [], None
        self.first = None
        self._day, self._prev = self.nd.FIRST_DAY, None

    def _count(self, ts, kind, failed=False):
        c = self.counts.setdefault(minute_of(ts), Counter())
        c[kind] += 1
        if failed:
            c[kind + "!"] += 1

    def poll(self):
        new = {name: f.tail.read() for name, f in self.files.items()}
        with self.lock:
            for offset, line in new["netmon"]:
                self._ping(offset, line)
            for offset, line in new["diag"]:
                self._diag(offset, line)
            for offset, line in new["wifi"]:
                self._radio(offset, line)
            for offset, line in new["ipconfig"]:
                row = parse_ipconfig(line)
                if row:
                    self.files["ipconfig"].mark(row[0], offset)
                    self._count(row[0], "ipconfig")
            self._read_traces()
            if any(new.values()):
                self._trim()

    def _ping(self, offset, line):
        day, prev = self._day, self._prev
        row, new_day = parse_ping(line, self.nd, day, prev)
        if row is None:
            return
        ts, target, ok, ms = row
        self.files["netmon"].mark(ts, offset, day, prev)
        self._day, self._prev = new_day, ts
        self.first = self.first or ts
        self._count(ts, "ping", not ok)
        if target == self.nd.ASUS and ts >= self.nd.DIRECT_SINCE:
            return
        (self.wifi if self.nd.on_wifi(ts) else self.wired).feed(ts, target, ok, ms)
        if target != self.nd.ASUS:
            self.pings.append(row)

    def _diag(self, offset, line):
        row = parse_diag(line)
        if row is None:
            return
        ts, router, h1, h2, dns, link = row
        self.files["diag"].mark(ts, offset)
        self._count(ts, "diag_ping", router < 0)
        self._count(ts, "tcp", h1 < 0)
        self._count(ts, "tcp", h2 < 0)
        self._count(ts, "dns", dns < 0)
        self._count(ts, "adapter", link != "Up")
        self.diag.append(row)
        m = self.diag_min.setdefault(minute_of(ts), [0, 0])
        m[0], m[1] = max(m[0], https_value(h1, h2)), max(m[1], dns_value(dns))

    def _radio(self, offset, line):
        row = parse_radio(line)
        if row is None:
            return
        ts, state, _b, _c, sig, *_ = row
        self.files["wifi"].mark(ts, offset)
        self._count(ts, "wifi", state != "connected")
        self.radio.append(row)
        if sig is not None and state == "connected":
            key = minute_of(ts)
            self.signal_min[key] = min(self.signal_min.get(key, sig), sig)

    def _read_traces(self):
        path = NETMON_DIR / "diag_traceroute.txt"
        try:
            mtime = path.stat().st_mtime
            if mtime == self._trace_mtime:
                return
            text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r", "").replace("\x00", "")
        except OSError:
            return
        self._trace_mtime = mtime
        traces = []
        for m in re.finditer(r"===== (\S+ \S+) internet down \(router ping: (-?\d+) ms\) =====(.*?)(?=\n=====|\Z)", text, re.S):
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            output = "\n".join(l for l in m.group(3).split("\n") if l.strip())
            traces.append({"t": ts, "router": int(m.group(2)), "output": output})
        self.traces = traces

    def _trim(self):
        newest = self.pings[-1][0] if self.pings else datetime.now()
        cut = newest - RAW_KEEP
        for q in (self.pings, self.diag, self.radio):
            while q and q[0][0] < cut:
                q.popleft()

    def read_between(self, name, a, b):
        """Yields the rows of one log with a <= time < b, read from the file at the checkpoint before a."""
        f = self.files[name]
        with self.lock:
            if not f.times:
                return
            offset, day, prev = f.marks[max(bisect.bisect_right(f.times, a) - 1, 0)]
        parse = {"diag": parse_diag, "wifi": parse_radio, "ipconfig": parse_ipconfig}.get(name)
        day = day or self.nd.FIRST_DAY
        try:
            with open(f.path, "rb") as fh:
                fh.seek(offset)
                for raw in fh:
                    if not raw.endswith(b"\n"):  # a monitor is writing this line right now
                        break
                    line = clean_line(raw)
                    if not line:
                        continue
                    if parse:
                        row = parse(line)
                        if row is None:
                            continue
                    else:
                        row, day2 = parse_ping(line, self.nd, day, prev)
                        if row is None:
                            continue
                        day, prev = day2, row[0]
                    if row[0] >= b:
                        break
                    if row[0] >= a:
                        yield row
        except OSError:
            return


def https_value(h1, h2):
    """The chart's value for one test round: the faster host, or the limit when neither answered."""
    return min(min([v for v in (h1, h2) if v >= 0] or [TIMEOUT_MS]), TIMEOUT_MS)


def dns_value(d):
    return min(d, TIMEOUT_MS) if d >= 0 else TIMEOUT_MS


def median(values):
    return statistics.median(values) if values else None


def wired_or_wifi_now():
    """(kind, address) from ipconfig: which link holds this laptop's address on the home network."""
    try:
        out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    section = None
    for line in out.splitlines():
        head = re.match(r"^\S.*adapter (.+):\s*$", line)
        if head:
            section = head.group(1)
        else:
            m = re.search(r"IPv4 Address[ .]*: *(192\.168\.1\.\d+)", line)
            if m and section:
                return ("Cable" if section == "Ethernet" else "Wi-Fi"), m.group(1)
    return None, None


def status_of(store, now, nd):
    """What the banner says: the last seconds of pings, in plain words."""
    pings = store.pings
    if not pings:
        return {"level": "unknown", "title": "No data yet", "detail": "Waiting for the monitors' first rows."}
    last = pings[-1][0]
    age = (now - last).total_seconds()
    if age > STALE_SECONDS:
        return {"level": "unknown", "title": "Monitors are not logging",
                "detail": f"Last ping logged {last:%d.%m %H:%M:%S}. Run start.ps1 to start them again."}
    if any(a <= now < b for a, b in nd.ROUTER_RESTARTS):
        return {"level": "unknown", "title": "Router restart", "detail": "Marked as a restart, so it isn't counted."}
    tail = []
    for row in reversed(pings):
        if (last - row[0]).total_seconds() > 60:
            break
        tail.append(row)
    recent = [r for r in tail if (last - r[0]).total_seconds() <= 10]
    r10 = [r for r in recent if r[1] == nd.ROUTER]
    n10 = [r for r in recent if r[1] != nd.ROUTER]
    if len(n10) >= 3 and not any(r[2] for r in n10):
        if r10 and not any(r[2] for r in r10):
            return {"level": "critical", "title": "No internet, router not answering",
                    "detail": "The Digi router and the internet have stopped answering this laptop."}
        return {"level": "critical", "title": "No internet, router answering",
                "detail": "The router answers this laptop, but nothing gets through to the internet."}
    if r10 and not any(r[2] for r in r10):
        return {"level": "serious", "title": "Router not answering",
                "detail": "The Digi router has stopped answering this laptop, the internet still does."}
    lost_r = sum(1 for r in tail if r[1] == nd.ROUTER and not r[2])
    lost_n = sum(1 for r in tail if r[1] != nd.ROUTER and not r[2])
    slow = sum(1 for r in tail if r[1] == nd.ROUTER and r[2] and r[3] >= nd.SLOW_MS)
    if lost_r or lost_n >= 2 or slow >= 3:
        return {"level": "warning", "title": "Problems in the last minute",
                "detail": f"Lost {lost_r} router and {lost_n} internet pings; {slow} slow router replies."}
    return {"level": "good", "title": "Connection OK", "detail": "No lost pings in the last minute."}


def series(store, start, end):
    nd = store.nd
    step = step_for((end - start).total_seconds())
    epoch = int(start.timestamp()) // step * step
    count = (int(end.timestamp() - 0.001) - epoch) // step + 1

    def index(ts):
        return (int(ts.timestamp()) - epoch) // step

    router = [[0, 0, 0] for _ in range(count)]  # sent, lost, slowest reply
    net = [[0, 0, 0] for _ in range(count)]
    https, dns, signal = [None] * count, [None] * count, [None] * count

    def add_https(i, v):
        https[i] = max(https[i] or 0, v)

    def add_dns(i, v):
        dns[i] = max(dns[i] or 0, v)

    def add_signal(i, v):
        signal[i] = v if signal[i] is None else min(signal[i], v)

    if step >= 60:  # the per-minute totals are kept for all of history
        for track in (store.wired, store.wifi):
            for key, m in track.mins.items():
                if start - timedelta(minutes=1) < key < end:
                    i = index(key)
                    if 0 <= i < count:
                        for b, g in ((router[i], "router"), (net[i], "net")):
                            b[0] += m.n[g]
                            b[1] += m.lost[g]
                            b[2] = max(b[2], m.worst[g])
        for key, (h, d) in store.diag_min.items():
            if start - timedelta(minutes=1) < key < end:
                i = index(key)
                if 0 <= i < count:
                    add_https(i, h)
                    add_dns(i, d)
        for key, v in store.signal_min.items():
            if start - timedelta(minutes=1) < key < end:
                i = index(key)
                if 0 <= i < count:
                    add_signal(i, v)
    else:
        covered = store.pings and store.pings[0][0] <= start
        pings = [r for r in store.pings if start <= r[0] < end] if covered else store.read_between("netmon", start, end)
        diag = [r for r in store.diag if start <= r[0] < end] if covered else store.read_between("diag", start, end)
        radio = [r for r in store.radio if start <= r[0] < end] if covered else store.read_between("wifi", start, end)
        for ts, target, ok, ms in pings:
            if target == nd.ASUS:
                continue
            i = index(ts)
            if 0 <= i < count:
                b = router[i] if target == nd.ROUTER else net[i]
                b[0] += 1
                if ok:
                    b[2] = max(b[2], ms)
                else:
                    b[1] += 1
        for ts, _, h1, h2, d, _link in diag:
            i = index(ts)
            if 0 <= i < count:
                add_https(i, https_value(h1, h2))
                add_dns(i, dns_value(d))
        for ts, state, _b, _c, sig, *_ in radio:
            i = index(ts)
            if 0 <= i < count and sig is not None and state == "connected":
                add_signal(i, sig)
    rows = []
    for i in range(count):
        r, n = router[i], net[i]
        rows.append({
            "t": (epoch + i * step) * 1000,
            "routerMs": r[2] if r[0] and r[1] < r[0] else None,
            "netMs": n[2] if n[0] and n[1] < n[0] else None,
            "routerLost": r[1], "routerSent": r[0],
            "netLost": n[1], "netSent": n[0],
            "https": https[i], "dns": dns[i], "signal": signal[i],
        })
    return {"step": step * 1000, "start": epoch * 1000, "end": ms_of(end), "rows": rows}


def incidents_of(store, start, end):
    nd = store.nd
    out = []
    for track, name in ((store.wired, "cable"), (store.wifi, "wifi")):
        for inc in nd.incidents(track.mins):
            a, b = inc[0].start, inc[-1].start + timedelta(minutes=1)
            if b <= start or a >= end:
                continue
            agg = lambda g, f: sum(getattr(m, f)[g] for m in inc)
            setup = "Wi-Fi" if name == "wifi" else ("Via Asus" if a < nd.DIRECT_SINCE else "Cable, direct")
            out.append({
                "start": ms_of(a), "end": ms_of(b), "setup": setup, "minutes": len(inc),
                "routerLost": agg("router", "lost"), "routerSent": agg("router", "n"),
                "netLost": agg("net", "lost"), "netSent": agg("net", "n"),
                "worst": max(m.worst["router"] for m in inc),
            })
    return sorted(out, key=lambda x: -x["start"])


def with_loss(m):
    return not m.excluded and (m.lost["router"] >= 1 or m.lost["net"] >= 2)


def summary(minutes, outages):
    kept = [m for m in minutes if not m.excluded]
    return {
        "counted": len(kept), "problem": sum(m.bad for m in kept), "withLoss": sum(with_loss(m) for m in kept),
        "routerLost": sum(m.lost["router"] for m in kept), "routerSent": sum(m.n["router"] for m in kept),
        "netLost": sum(m.lost["net"] for m in kept), "netSent": sum(m.n["net"] for m in kept),
        "longest": max((o[2] for o in outages), default=0),
    }


def overlaps(m, start, end):
    return m.start + timedelta(minutes=1) > start and m.start < end


def periods_of(store, start, end):
    """One row per setup inside the range, each router restart starting a new row."""
    nd = store.nd
    wired = [m for m in store.wired.mins.values() if overlaps(m, start, end)]
    wifi = [m for m in store.wifi.mins.values() if overlaps(m, start, end)]
    w_out, f_out = store.wired.outages(), store.wifi.outages()
    rows = []

    def add(label, mins, outs, a, b):
        ms = [m for m in mins if (a is None or m.start >= a) and (b is None or m.start < b)]
        os_ = [o for o in outs if (a is None or o[0] >= a) and (b is None or o[0] < b) and o[1] > start and o[0] < end]
        if ms:
            rows.append({"label": label, "from": ms_of(ms[0].start), "to": ms_of(ms[-1].start) + 60000, **summary(ms, os_)})

    add("Via Asus router, cable", wired, w_out, None, nd.DIRECT_SINCE)
    restarts = sorted(b for _, b in nd.ROUTER_RESTARTS)
    for a, b in zip([nd.DIRECT_SINCE] + restarts, restarts + [None]):
        add("Cable, straight to Digi" + ("" if a == nd.DIRECT_SINCE else f", after restart {a:%d.%m %H:%M}"),
            wired, w_out, a, b)
    first = min((a for a, _ in nd.WIFI_SPANS), default=None)
    for a, b in [(first, restarts[0] if restarts else None)] + list(zip(restarts, restarts[1:] + [None])):
        add("Wi-Fi, Digi router" + ("" if a == first else f", after restart {a:%d.%m %H:%M}"), wifi, f_out, a, b)
    return rows


def days_of(store, start, end):
    by_day = {}
    for track in (store.wired, store.wifi):
        for m in track.mins.values():
            if overlaps(m, start, end):
                by_day.setdefault(m.start.date(), []).append(m)
    out = []
    for day in sorted(by_day, reverse=True):
        s = summary(by_day[day], [])
        if s["counted"]:
            s.pop("longest")
            out.append({"day": day.isoformat(), **s})
    return out


def markers_of(nd, start, end):
    found = [(a, "Router restart") for a, _ in nd.ROUTER_RESTARTS]
    found += [(nd.DIRECT_SINCE, "Asus off")]
    found += [(a, "To Wi-Fi") for a, _ in nd.WIFI_SPANS] + [(b, "To cable") for _, b in nd.WIFI_SPANS if b]
    return [{"t": ms_of(t), "label": lbl} for t, lbl in sorted(found) if start <= t <= end]


def tiles_of(store, now, nd):
    cut30, cut5m = now - timedelta(seconds=30), now - timedelta(minutes=5)
    r_ms, n_ms, r_lost, n_lost = [], [], 0, 0
    for ts, target, ok, ms in reversed(store.pings):
        if ts < cut5m:
            break
        is_router = target == nd.ROUTER
        if not ok:
            r_lost += is_router
            n_lost += not is_router
        elif ts >= cut30:
            (r_ms if is_router else n_ms).append(ms)
    https, dns, https_fail, dns_fail = [], [], 0, 0
    for ts, _r, h1, h2, d, _l in reversed(store.diag):
        if ts < cut5m:
            break
        https_fail += h1 < 0 and h2 < 0
        dns_fail += d < 0
        if ts >= cut30:
            https += [v for v in (h1, h2) if v >= 0]
            if d >= 0:
                dns.append(d)
    return {
        "router": median(r_ms), "routerLost5m": r_lost,
        "net": median(n_ms), "netLost5m": n_lost,
        "https": median(https), "httpsFail5m": https_fail,
        "dns": median(dns), "dnsFail5m": dns_fail,
    }


# ---------------------------------------------------------------- the commands run in the background

def _reply(ms, limit="1 s"):
    return f"{ms} ms" if ms >= 0 else f"no answer in {limit}"


def _ping_rows(row):
    ts, target, ok, ms = row
    return [{"t": ms_of(ts), "kind": "ping", "cmd": KINDS["ping"]["command"].format(target=target),
             "result": f"{ms} ms" if ok else "no reply in 1 s", "ok": ok}]


def _diag_rows(row):
    ts, router, h1, h2, dns, link = row
    t = ms_of(ts)
    return [
        {"t": t, "kind": "diag_ping", "cmd": KINDS["diag_ping"]["command"], "result": _reply(router), "ok": router >= 0},
        {"t": t, "kind": "tcp", "cmd": KINDS["tcp"]["command"].format(target="1.1.1.1"), "result": _reply(h1, "2 s"), "ok": h1 >= 0},
        {"t": t, "kind": "tcp", "cmd": KINDS["tcp"]["command"].format(target="8.8.8.8"), "result": _reply(h2, "2 s"), "ok": h2 >= 0},
        {"t": t, "kind": "dns", "cmd": KINDS["dns"]["command"], "result": _reply(dns, "2 s"), "ok": dns >= 0},
        {"t": t, "kind": "adapter", "cmd": KINDS["adapter"]["command"], "result": f"Link: {link}", "ok": link == "Up"},
    ]


def _wifi_rows(row):
    ts, state, _bssid, channel, signal, _rssi, rx, tx = row
    text = f"{state}, signal {signal}%, channel {channel}, {rx}/{tx} Mbps" if state == "connected" else (state or "no reading")
    return [{"t": ms_of(ts), "kind": "wifi", "cmd": KINDS["wifi"]["command"], "result": text, "ok": state == "connected"}]


def _ipconfig_rows(row):
    return [{"t": ms_of(row[0]), "kind": "ipconfig", "cmd": "ipconfig", "result": row[1], "ok": True}]


EXPAND = {"netmon": _ping_rows, "diag": _diag_rows, "wifi": _wifi_rows, "ipconfig": _ipconfig_rows}
LOG_OF_KIND = {"ping": "netmon", "diag_ping": "diag", "tcp": "diag", "dns": "diag", "adapter": "diag",
               "wifi": "wifi", "ipconfig": "ipconfig"}


def command_counts(store, start, end):
    """{kind: {"n": run, "failed": failed}} for the range, off the per-minute totals, so whole minutes at its edges."""
    total = Counter()
    for key, c in store.counts.items():
        if start - timedelta(minutes=1) < key < end:
            total.update(c)
    out = {k: {"n": total[k], "failed": total[k + "!"]} for k in KINDS if k != "tracert"}
    traces = sum(1 for t in store.traces if start <= t["t"] < end)
    out["tracert"] = {"n": traces, "failed": traces}
    return out


def commands_between(store, a, b, kinds=None, failed=False):
    """Every command with a <= time < b, oldest first."""
    kinds = set(kinds or KINDS)
    rows = []
    for name in {LOG_OF_KIND[k] for k in kinds if k in LOG_OF_KIND}:
        for row in store.read_between(name, a, b):
            for c in EXPAND[name](row):
                if c["kind"] in kinds and (not failed or not c["ok"]):
                    rows.append(c)
    if "tracert" in kinds:
        for t in store.traces:
            if a <= t["t"] < b:
                rows.append({"t": ms_of(t["t"]), "kind": "tracert", "cmd": KINDS["tracert"]["command"],
                             "result": f"internet down, router ping {_reply(t['router'])}", "ok": False, "output": t["output"]})
    rows.sort(key=lambda c: c["t"])
    return rows


def _chunk_has(store, a, b, kinds, failed):
    key = minute_of(a)
    while key < b:
        c = store.counts.get(key)
        if c and any(c[k + "!" if failed else k] for k in kinds):
            return True
        key += timedelta(minutes=1)
    return "tracert" in kinds and any(a <= t["t"] < b for t in store.traces)


def commands_page(store, start, end, before=None, limit=200, kinds=None, failed=False):
    """Newest first. Rows sharing the last row's time stay together, so `next` never splits or repeats a row."""
    kinds = [k for k in (kinds or KINDS) if k in KINDS]
    cur, rows, chunk = min(before or end, end), [], timedelta(seconds=60)
    # rows are timed to the ms and `next` is in ms, so the upper bound goes up to a whole ms: it can't skip or repeat a row
    cur += timedelta(microseconds=-cur.microsecond % 1000)
    while cur > start and len(rows) < limit:
        a = max(start, cur - chunk)
        if _chunk_has(store, a, cur, kinds, failed):
            rows += commands_between(store, a, cur, kinds, failed)[::-1]
            chunk = timedelta(seconds=60)
        else:
            chunk = min(chunk * 2, timedelta(days=1))
        cur = a
    next_before = ms_of(cur) if cur > start else None
    if len(rows) > limit:
        last_t = rows[limit - 1]["t"]
        keep = limit
        while keep < len(rows) and rows[keep]["t"] == last_t:
            keep += 1
        if keep < len(rows):
            rows, next_before = rows[:keep], last_t
    return rows, next_before


def definitions():
    return [{"kind": k, **v} for k, v in KINDS.items()]


# ---------------------------------------------------------------- the report and the PDF

def find_browser():
    roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    for root in filter(None, roots):
        for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            path = Path(root) / rel
            if path.exists():
                return path
    return None


def request_allowed_to_act(headers):
    """Another website open in the browser must not be able to start an export on this laptop."""
    return (headers.get("Sec-Fetch-Site") or "none") in ("none", "same-origin")


def print_pdf(url, browser=None):
    browser = browser or find_browser()
    if browser is None:
        raise RuntimeError("Neither Chrome nor Edge was found to print the PDF.")
    work = Path(tempfile.mkdtemp(prefix="netmon_pdf_"))
    try:
        out = work / "report.pdf"
        # a profile of its own, or the call would hand over to the user's open browser and print nothing
        subprocess.run([str(browser), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        f"--user-data-dir={work / 'profile'}", "--window-size=1047,1400", "--no-pdf-header-footer",
                        f"--print-to-pdf={out}", url], capture_output=True, timeout=PDF_TIMEOUT_SECONDS,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("The browser produced no PDF.")
        return out.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


class BadRange(ValueError):
    pass


def parse_range(query, store, now):
    """(start, end, key, live) from window=<preset> or from=<ms>&to=<ms>."""
    window = query.get("window", [None])[0]
    if "from" in query or "to" in query:
        try:
            a, b = of_ms(int(query["from"][0])), of_ms(int(query["to"][0]))
        except (KeyError, ValueError, OSError, OverflowError) as e:
            raise BadRange("from and to must be times in milliseconds") from e
        b = min(b, now)
        if (b - a).total_seconds() < 10:
            raise BadRange("the range must be at least 10 seconds")
        return a, b, f"{query['from'][0]}-{query['to'][0]}", False
    window = window or "1h"
    if window not in PRESETS:
        raise BadRange("unknown window")
    if window == "all":
        return store.first or now - timedelta(hours=1), now, window, True
    return now - timedelta(seconds=PRESETS[window]), now, window, True


class App:
    def __init__(self):
        self.store = None
        self.link = (None, None)
        self._ranges = {}
        self._rules_mtime = None
        self.lock = threading.Lock()
        self.pdf_lock = threading.Lock()

    def build(self, store):
        started = time.time()
        store.poll()
        store.ready = True
        log.info("logs loaded in %.1f s", time.time() - started)

    def _run_ipconfig(self):
        self.link = wired_or_wifi_now()
        kind, address = self.link
        text = f"{kind}, {address}" if kind else "no address on the home network"
        with open(HERE / "commands.csv", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='milliseconds')},{text}\n")

    def run_reader(self):
        last_link = 0
        while True:
            try:
                rules = NETMON_DIR / "netmon_data.py"
                mtime = rules.stat().st_mtime
                if mtime != self._rules_mtime:  # a setup or a restart was marked: read everything again
                    self._rules_mtime = mtime
                    store = Store()
                    self.build(store)
                    self.store = store
                    self._ranges = {}
                else:
                    self.store.poll()
                if time.time() - last_link > 10:
                    self._run_ipconfig()
                    last_link = time.time()
            except Exception:
                log.exception("reading the logs failed")
            if (HERE / "stop").exists():
                log.info("stop file found, exiting")
                os._exit(0)
            time.sleep(1)

    def state(self, query):
        store = self.store
        if store is None or not store.ready:
            return {"ready": False}
        now = datetime.now()
        start, end, key, live = parse_range(query, store, now)
        with self.lock:
            hit = self._ranges.get(key)
            ttl = (15 if key == "all" else 1.5) if live else 60
            if not hit or time.time() - hit[0] >= ttl:
                part = self.range_part(store, start, end)
                part.update(key=key, live=live)
                if len(self._ranges) > 16:
                    self._ranges.clear()
                hit = self._ranges[key] = (time.time(), part)
            return {"ready": True, "now": ms_of(now), **self.live_part(store, now), **hit[1]}

    def live_part(self, store, now):
        nd = store.nd
        with store.lock:
            radio = store.radio[-1] if store.radio else None
            kind, address = self.link
            connection = {"kind": kind, "address": address, "signal": None, "rssi": None, "channel": None,
                          "rx": None, "tx": None, "state": None}
            if radio and kind != "Cable":
                connection.update(state=radio[1], channel=radio[3], signal=radio[4], rssi=radio[5], rx=radio[6], tx=radio[7])
            return {"status": status_of(store, now, nd), "connection": connection, "tiles": tiles_of(store, now, nd),
                    "firstLog": ms_of(store.first) if store.first else None, "slowMs": nd.SLOW_MS}

    def range_part(self, store, start, end):
        nd = store.nd
        with store.lock:
            store.wired.settle()
            store.wifi.settle()
            counts = command_counts(store, start, end)
            return {
                "range": {"from": ms_of(start), "to": ms_of(end)},
                "series": series(store, start, end),
                "markers": markers_of(nd, start, end),
                "restarts": [{"start": ms_of(a), "end": ms_of(b)} for a, b in nd.ROUTER_RESTARTS],
                "incidents": incidents_of(store, start, end),
                "periods": periods_of(store, start, end),
                "days": days_of(store, start, end),
                "commands": {"counts": counts, "total": sum(c["n"] for c in counts.values()),
                             "failed": sum(c["failed"] for c in counts.values()), "listLimit": REPORT_ALL_LIMIT,
                             "definitions": definitions()},
            }

    def report_html(self, query):
        store = self.store
        now = datetime.now()
        start, end, _key, _live = parse_range(query, store, now)
        list_all = query.get("all", ["0"])[0] == "1"
        data = self.state({"from": [str(ms_of(start))], "to": [str(ms_of(end))]})
        total = data["commands"]["total"]
        listed_all = list_all and total <= REPORT_ALL_LIMIT
        failed = commands_between(store, start, end, failed=True)
        rows = commands_between(store, start, end) if listed_all else failed[:REPORT_FAILED_LIMIT]
        data["report"] = {
            "generated": ms_of(now),
            "rows": [{k: v for k, v in c.items() if k != "output"} for c in rows if c["kind"] != "tracert"],
            "traces": [c for c in failed if c["kind"] == "tracert"],
            "failedTotal": sum(1 for c in failed if c["kind"] != "tracert"),
            "listedAll": listed_all,
        }
        blob = json.dumps(data).replace("</", "<\\/")
        page = (HERE / "index.html").read_text(encoding="utf-8")
        return page.replace("<!--REPORT-->", f"<script>window.__REPORT__ = {blob};</script>", 1)


APP = App()


class Handler(BaseHTTPRequestHandler):
    server_version = "netmon-dashboard"

    def log_message(self, *_):
        pass

    def _send(self, code, body, kind, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; "
                                                    "script-src 'unsafe-inline'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value, code=200):
        self._send(code, json.dumps(value).encode(), "application/json")

    def _range(self, query):
        store = APP.store
        if store is None or not store.ready:
            self._json({"error": "still reading the logs"}, 503)
            return None
        try:
            return parse_range(query, store, datetime.now())
        except BadRange as e:
            self._json({"error": str(e)}, 400)
            return None

    def do_GET(self):
        # another website open in the browser must not be able to read this through a look-alike host name
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, b"forbidden", "text/plain")
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/":
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/state":
            try:
                self._json(APP.state(query))
            except BadRange as e:
                self._json({"error": str(e)}, 400)
        elif url.path == "/api/commands":
            self._commands(query)
        elif url.path == "/api/commands.csv":
            self._commands_csv(query)
        elif url.path == "/report":
            if self._range(query):
                self._send(200, APP.report_html(query).encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/api/export.pdf":
            self._export(query)
        elif url.path == "/health":
            self._send(200, b"ok", "text/plain")
        elif url.path == "/favicon.ico":
            self._send(204, b"", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def _commands(self, query):
        parsed = self._range(query)
        if not parsed:
            return
        start, end, _k, _l = parsed
        kinds = [k for k in (query.get("kinds", [""])[0].split(",")) if k]
        failed = query.get("failed", ["0"])[0] == "1"
        try:
            limit = min(max(int(query.get("limit", ["200"])[0]), 1), 1000)
            before = of_ms(int(query["before"][0])) if "before" in query else None
        except (ValueError, OSError, OverflowError):
            self._json({"error": "bad limit or before"}, 400)
            return
        rows, next_before = commands_page(APP.store, start, end, before, limit, kinds or None, failed)
        self._json({"rows": rows, "next": next_before})

    def _commands_csv(self, query):
        if not request_allowed_to_act(self.headers):
            self._send(403, b"forbidden", "text/plain")
            return
        parsed = self._range(query)
        if not parsed:
            return
        start, end, _k, _l = parsed
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{file_name(start, end, "commands", "csv")}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write("\ufefftime;command;result;status;script\n".encode("utf-8"))
        a = start
        while a < end:
            b = min(a + timedelta(hours=1), end)
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";", lineterminator="\n")
            for c in commands_between(APP.store, a, b):
                w.writerow([of_ms(c["t"]).isoformat(sep=" ", timespec="milliseconds"), c["cmd"], c["result"],
                            "ok" if c["ok"] else "failed", KINDS[c["kind"]]["script"]])
            self.wfile.write(buf.getvalue().encode("utf-8"))
            a = b

    def _export(self, query):
        if not request_allowed_to_act(self.headers):
            self._send(403, b"forbidden", "text/plain")
            return
        parsed = self._range(query)
        if not parsed:
            return
        start, end, _k, _l = parsed
        if not APP.pdf_lock.acquire(blocking=False):
            self._json({"error": "Another PDF is being built. Try again in a moment."}, 429)
            return
        try:
            params = {"from": ms_of(start), "to": ms_of(end), "all": query.get("all", ["0"])[0]}
            body = print_pdf(f"http://{HOST}:{PORT}/report?{urlencode(params)}")
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            log.exception("PDF export failed")
            self._json({"error": str(e)}, 500)
            return
        finally:
            APP.pdf_lock.release()
        self._send(200, body, "application/pdf",
                   {"Content-Disposition": f'attachment; filename="{file_name(start, end, "connection-report", "pdf")}"'})


def file_name(start, end, stem, ext):
    return f"{stem}_{start:%Y-%m-%d_%H%M}_{end:%Y-%m-%d_%H%M}.{ext}"


def main():
    logging.basicConfig(filename=HERE / "server.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    (HERE / "stop").unlink(missing_ok=True)
    threading.Thread(target=APP.run_reader, daemon=True).start()
    log.info("serving on http://%s:%s, logs in %s", HOST, PORT, NETMON_DIR)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
