"""Loads the two monitor logs into per-minute facts the Digi evidence pack is built from.

netmon.csv: one row per ping (router 192.168.1.1, Asus 192.168.1.5, 1.1.1.1, 8.8.8.8). Rows written before
2026-10-19 22:24 carry the time of day only and start on 2026-10-17; a step BACK of more than an hour is the next
day (a small step back is the clock being corrected). diag.csv: one row a second from 2026-10-19 21:08 with the
router ping, an HTTPS connect to 1.1.1.1 and 8.8.8.8, and a DNS lookup (-1 = no answer).

The monitors log only while the PC is awake. A session is a run of rows without a gap over 60 s. Excluded as the
PC's own doing: sessions under 3 minutes, the first and last minute of every session (waking up, going to sleep),
and any minute in which the Asus, the box between the PC and the Digi router, lost a ping.

From DIRECT_SINCE the PC is plugged straight into the Digi router and the Asus is off: its rows are dropped, so a
lost router ping can no longer be blamed on anything between the two.

In WIFI_SPANS the PC reached the router over Wi-Fi, whose own losses say nothing about the router or the line: those
rows are left out by default and read only with wifi=True. An open span (None) runs until the setup changes again.
link.ps1 logs the link every 5 s, so the spans follow data/link.csv by themselves; HAND_MARKED_WIFI is the time before
that log began. It can tell cable from Wi-Fi, not what the cable plugs into: DIRECT_SINCE stays marked by hand.

ROUTER_RESTARTS are reboots done on purpose: those minutes count as the household's doing, never as a drop.
"""
import csv
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"
ROUTER, ASUS, NET = "192.168.1.1", "192.168.1.5", ("1.1.1.1", "8.8.8.8")
FIRST_DAY = date(2026, 10, 17)
SESSION_GAP = timedelta(seconds=60)
SLOW_MS = 150
DIRECT_SINCE = datetime(2026, 11, 2)
HAND_MARKED_WIFI = [(datetime(2026, 11, 2, 13, 35, 34), None)]
ROUTER_RESTARTS = [(datetime(2026, 11, 2, 17, 52), datetime(2026, 11, 2, 17, 57))]


def _ts(text, day, prev):
    if len(text) > 13 and text[4] == "-":
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f"), None
    t = datetime.combine(day, datetime.strptime(text, "%H:%M:%S.%f").time())
    if prev and t < prev - timedelta(hours=1):
        day += timedelta(days=1)
        t += timedelta(days=1)
    return t, day


def read_link(path=DATA / "link.csv"):
    """[(time, link)] from link.ps1's log. A line cut short, or NUL bytes from a killed monitor, are skipped."""
    try:
        text = path.read_bytes().replace(b"\x00", b"").decode("utf-8", "replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        c = line.lstrip("\ufeff").split(",")
        try:
            if len(c) == 3:
                rows.append((datetime.fromisoformat(c[0]), c[1]))
        except ValueError:
            pass  # the header
    return rows


def wifi_spans(rows, marked=HAND_MARKED_WIFI):
    """The hand-marked spans up to the log's first row, then the log's. Only a reading of Cable or Wi-Fi changes the
    setup: Wi-Fi dropping for a moment ("none") or a VPN ("other") is still the setup it was."""
    if not rows:
        return list(marked)
    first = rows[0][0]
    spans = [(a, b) for a, b in marked if b is not None and b <= first]
    start = next((a for a, b in marked if a <= first and (b is None or first < b)), None)
    for ts, link in rows:
        if link == "Wi-Fi" and start is None:
            start = ts
        elif link == "Cable" and start is not None:
            spans.append((start, ts))
            start = None
    if start is not None:
        spans.append((start, None))
    return spans


WIFI_SPANS = wifi_spans(read_link())


def on_wifi(ts):
    return any(a <= ts and (b is None or ts < b) for a, b in WIFI_SPANS)


def load_pings(path=DATA / "netmon.csv", wifi=False):
    """[(ts, target, ok, ms)] with real dates: the wired periods, or with wifi=True the Wi-Fi ones."""
    rows, day, prev = [], FIRST_DAY, None
    # a monitor killed mid-write leaves a run of NUL bytes; drop them and any line they cut
    lines = (line.replace("\x00", "") for line in open(path, encoding="utf-8-sig", errors="replace"))
    for r in csv.DictReader(lines):
        if not r.get("time") or not r.get("target") or r.get("ok") not in ("True", "False"):
            continue
        try:
            ts, new_day = _ts(r["time"], day, prev)
            int(r["ms"])
        except ValueError:
            continue
        day = new_day or ts.date()
        prev = ts
        if (r["target"] == ASUS and ts >= DIRECT_SINCE) or on_wifi(ts) != wifi:
            continue
        rows.append((ts, r["target"], r["ok"] == "True", int(r["ms"])))
    return rows


def load_diag(path=DATA / "diag.csv", wifi=False):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        try:
            ts = datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S.%f")
            if on_wifi(ts) == wifi:
                out.append((ts, int(r["router_ping_ms"]), int(r["tcp_1111_ms"]), int(r["tcp_8888_ms"]),
                            int(r["dns_ms"]), r["link"]))
        except (KeyError, ValueError):
            continue
    return out


@dataclass
class Minute:
    start: datetime
    session: int
    n: dict = field(default_factory=lambda: {"router": 0, "asus": 0, "net": 0})
    lost: dict = field(default_factory=lambda: {"router": 0, "asus": 0, "net": 0})
    slow: dict = field(default_factory=lambda: {"router": 0, "asus": 0, "net": 0})
    worst: dict = field(default_factory=lambda: {"router": 0, "asus": 0, "net": 0})
    excluded: bool = False

    def loss(self, g):
        return 100 * self.lost[g] / self.n[g] if self.n[g] else 0.0

    @property
    def bad(self):
        return not self.excluded and (self.lost["router"] >= 1 or self.lost["net"] >= 2 or self.slow["router"] >= 3)


def group(target):
    return "router" if target == ROUTER else "asus" if target == ASUS else "net"


def extend_sessions(spans, ts):
    """Grows the list of [start, end] runs of uninterrupted logging by one row."""
    if spans and ts - spans[-1][1] <= SESSION_GAP:
        spans[-1][1] = ts
    else:
        spans.append([ts, ts])


def sessions(rows):
    """[(start, end)] of uninterrupted logging."""
    out = []
    for ts, *_ in rows:
        extend_sessions(out, ts)
    return [tuple(s) for s in out]


def add_ping(mins, session, ts, target, ok, ms):
    key = ts.replace(second=0, microsecond=0)
    m = mins.setdefault(key, Minute(key, session))
    g = group(target)
    m.n[g] += 1
    if not ok:
        m.lost[g] += 1
    else:
        m.worst[g] = max(m.worst[g], ms)
        m.slow[g] += ms >= SLOW_MS


def mark_excluded(mins, spans):
    """Sets Minute.excluded from scratch; safe to call again as a live log grows."""
    short = {si for si, (a, b) in enumerate(spans) if b - a < timedelta(minutes=3)}
    for m in mins.values():
        m.excluded = (m.session in short or m.lost["asus"] > 0
                      or any(a <= m.start < b for a, b in ROUTER_RESTARTS))
    for a, b in spans:
        for key in (a.replace(second=0, microsecond=0), b.replace(second=0, microsecond=0)):
            if key in mins:
                mins[key].excluded = True


def minutes(rows):
    spans, mins = [], OrderedDict()
    for ts, target, ok, ms in rows:
        extend_sessions(spans, ts)
        add_ping(mins, len(spans) - 1, ts, target, ok, ms)
    spans = [tuple(s) for s in spans]
    mark_excluded(mins, spans)
    return mins, spans


def incidents(mins):
    """Bad minutes at most 2 minutes apart, in one session, are one incident."""
    out = []
    for m in mins.values():
        if not m.bad:
            continue
        if out and out[-1][-1].session == m.session and m.start - out[-1][-1].start <= timedelta(minutes=2):
            out[-1].append(m)
        else:
            out.append([m])
    return out


def outages(rows, mins):
    """Runs of rounds in which BOTH internet hosts failed: [(start, end, seconds)], longest first."""
    rounds, cur = [], None
    for ts, target, ok, _ in rows:
        if target == ROUTER:
            cur = {"t": ts}
            rounds.append(cur)
        elif cur is not None:
            cur[target] = ok
    runs, start, last_fail = [], None, None
    for r in rounds:
        m = mins.get(r["t"].replace(second=0, microsecond=0))
        down = all(r.get(h) is False for h in NET) and m is not None and not m.excluded
        if down:
            start = start or r["t"]
            last_fail = r["t"]
        elif start:
            runs.append((start, r["t"], (r["t"] - start).total_seconds()))
            start = None
    return sorted(runs, key=lambda x: -x[2])
