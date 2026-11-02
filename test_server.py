"""The dashboard must report what netmon_data reports. Run: python -m pytest test_server.py -q

Feeds a fixed copy of the logs through the row-by-row Track and compares it with netmon_data's whole-file functions.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import server


@pytest.fixture(scope="module")
def nd():
    return server.load_rules()


def replay(nd, rows):
    track = server.Track(nd)
    for row in rows:
        track.feed(*row)
    track.settle()
    return track


@pytest.mark.parametrize("wifi", [False, True])
def test_real_log_matches_netmon_data(nd, wifi):
    path = nd.DATA / "netmon.csv"
    if not path.exists():
        pytest.skip("no log on this machine")
    rows = nd.load_pings(wifi=wifi)
    mins, spans = nd.minutes(rows)
    track = replay(nd, rows)
    assert list(track.mins) == list(mins)
    for key_, m in mins.items():
        t = track.mins[key_]
        assert (t.n, t.lost, t.slow, t.worst, t.excluded, t.session) == (m.n, m.lost, m.slow, m.worst, m.excluded, m.session), key_
    assert [tuple(s) for s in track.spans] == spans
    assert sorted(track.outages()) == sorted(nd.outages(rows, mins))


def rows_for(nd, start, pattern):
    """One round every second: pattern(i) -> (router_ok, net_ok)."""
    out = []
    for i in range(600):
        ts = start + timedelta(seconds=i)
        r, n = pattern(i)
        out += [(ts, nd.ROUTER, r, 1 if r else 0), (ts + timedelta(milliseconds=100), "1.1.1.1", n, 10 if n else 0),
                (ts + timedelta(milliseconds=200), "8.8.8.8", n, 20 if n else 0)]
    return out


def test_outage_found_and_router_loss_counted(nd):
    start = datetime(2030, 1, 1, 10, 0, 0)
    rows = rows_for(nd, start, lambda i: (i % 300 != 200, not 240 <= i < 270))
    track = replay(nd, rows)
    outs = track.outages()
    assert len(outs) == 1 and outs[0][2] == 30
    assert sum(m.bad for m in track.mins.values()) >= 1


def test_tail_reads_only_complete_lines_and_survives_nul_bytes(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes(b"\xef\xbb\xbftime,a\n2026-11-02 10:00:00.000,1\n2026-11-")
    tail = server.LogTail(p)
    assert [line for _, line in tail.read()] == ["2026-11-02 10:00:00.000,1"]
    with open(p, "ab") as f:
        f.write(b"02 10:00:01.000,2\n\x00\x00\x00\x002026-11-02 10:00:02.000,3\n")
    assert [line for _, line in tail.read()] == ["2026-11-02 10:00:01.000,2", "2026-11-02 10:00:02.000,3"]
    assert tail.read() == []


def test_tail_offsets_point_at_their_lines(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes(b"time,a\n2026-11-02 10:00:00.000,1\n2026-11-")
    tail = server.LogTail(p)
    first = tail.read()
    with open(p, "ab") as f:
        f.write(b"02 10:00:01.000,2\n")
    for offset, line in first + tail.read():
        with open(p, "rb") as f:
            f.seek(offset)
            assert f.readline().decode().strip() == line


def test_status_words(nd):
    store = server.Store.__new__(server.Store)
    now = datetime(2030, 1, 1, 10, 0, 30)
    def fill(router_ok, net_ok):
        from collections import deque
        store.pings = deque()
        for i in range(30):
            ts = now - timedelta(seconds=30 - i)
            store.pings.extend([(ts, nd.ROUTER, router_ok, 1), (ts, "1.1.1.1", net_ok, 10), (ts, "8.8.8.8", net_ok, 20)])
    fill(True, True)
    assert server.status_of(store, now, nd)["level"] == "good"
    fill(True, False)
    assert server.status_of(store, now, nd)["title"] == "No internet, router answering"
    fill(False, False)
    assert server.status_of(store, now, nd)["title"] == "No internet, router not answering"
    fill(True, True)
    assert server.status_of(store, now + timedelta(minutes=5), nd)["title"] == "Monitors are not logging"


T0 = datetime(2026, 3, 1, 10, 0, 0)


@pytest.fixture
def store(tmp_path, monkeypatch, nd):
    """A store over small logs of its own: 30 minutes, some failures, two traceroutes."""
    rules = Path(nd.__file__).read_text(encoding="utf-8")
    monkeypatch.setattr(server, "HERE", tmp_path)
    monkeypatch.setattr(server, "MONITORS_DIR", tmp_path)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "CHECKPOINT_EVERY", 50)
    (tmp_path / "netmon_data.py").write_text(rules, encoding="utf-8")
    pings = ["time,target,ok,ms"]
    diag = ["time,router_ping_ms,tcp_1111_ms,tcp_8888_ms,dns_ms,link"]
    wifi = ["time,state,bssid,channel,signal_pct,rssi,rx_mbps,tx_mbps"]
    ipconfig = []
    for i in range(1800):
        ts = T0 + timedelta(seconds=i)
        stamp = ts.isoformat(sep=" ", timespec="milliseconds")
        for j, target in enumerate(("192.168.1.1", "1.1.1.1", "8.8.8.8")):
            ok = i % 97 != 0
            at = (ts + timedelta(milliseconds=100 * j)).isoformat(sep=" ", timespec="milliseconds")
            pings.append(f"{at},{target},{ok},{10 if ok else 0}")
        diag.append(f"{stamp},2,{-1 if i % 89 == 0 else 15},16,{-1 if i % 61 == 0 else 20},Up")
        if i % 5 == 0:
            wifi.append(f"{stamp},connected,aa:bb,6,90,-50,433,433")
        if i % 10 == 0:
            ipconfig.append(f"{ts.isoformat(timespec='milliseconds')},Wi-Fi, 192.168.1.6")
    (tmp_path / "netmon.csv").write_text("\n".join(pings) + "\n", encoding="utf-8")
    (tmp_path / "diag.csv").write_text("\n".join(diag) + "\n", encoding="utf-8")
    (tmp_path / "wifi.csv").write_text("\n".join(wifi) + "\n", encoding="utf-8")
    (tmp_path / "commands.csv").write_text("\n".join(ipconfig) + "\n", encoding="utf-8")
    (tmp_path / "diag_traceroute.txt").write_text(
        "===== 2026-03-01 10:05:00 internet down (router ping: 3 ms) =====\n\nTracing route\n  1  192.168.1.1\n"
        "===== 2026-03-01 10:20:00 internet down (router ping: -1 ms) =====\n  1  *\n", encoding="utf-8")
    s = server.Store()
    s.poll()
    return s


def key(c):
    return (c["t"], c["kind"], c["cmd"])


@pytest.mark.parametrize("name, a, b", [("netmon", 0, 1800), ("netmon", 700, 712), ("diag", 3, 4), ("diag", 1799, 1800),
                                        ("wifi", 100, 500), ("ipconfig", 0, 95), ("netmon", 1800, 1900)])
def test_read_between_equals_reading_everything(store, name, a, b):
    start, end = T0 + timedelta(seconds=a), T0 + timedelta(seconds=b)
    everything = list(store.read_between(name, T0 - timedelta(days=1), T0 + timedelta(days=1)))
    assert len(everything) > 10
    assert list(store.read_between(name, start, end)) == [r for r in everything if start <= r[0] < end]


def test_read_between_skips_a_line_being_written(store, tmp_path):
    with open(tmp_path / "diag.csv", "ab") as f:
        f.write(b"2026-03-01 10:30:00.000,2,15,16,20,U")
    rows = list(store.read_between("diag", T0, T0 + timedelta(days=1)))
    assert len(rows) == 1800 and rows[-1][0] < T0 + timedelta(minutes=30)


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("kinds", [None, ["tcp"], ["tracert"], ["ping", "wifi"], ["dns", "tracert"]])
@pytest.mark.parametrize("limit", [7, 200])
def test_paging_returns_every_command_once(store, failed, kinds, limit):
    start, end = T0 + timedelta(seconds=30, milliseconds=500), T0 + timedelta(minutes=25, microseconds=700)
    want = server.commands_between(store, start, end, kinds, failed)
    got, before = [], None
    for _ in range(100000):
        rows, nxt = server.commands_page(store, start, end, before, limit, kinds, failed)
        got += rows
        if nxt is None:
            break
        before = server.of_ms(nxt)
    assert sorted(map(key, got)) == sorted(map(key, want))
    assert len(got) == len(want)
    assert [c["t"] for c in got] == sorted((c["t"] for c in got), reverse=True)


def test_a_page_never_splits_rows_of_one_time(store):
    rows, nxt = server.commands_page(store, T0, T0 + timedelta(minutes=5), None, 3, ["tcp", "dns"])
    assert len({c["t"] for c in rows}) == 1 and len(rows) == 3
    assert nxt == rows[-1]["t"]


def test_counts_equal_the_rows_on_whole_minutes(store):
    start, end = T0 + timedelta(minutes=2), T0 + timedelta(minutes=12)
    counts = server.command_counts(store, start, end)
    rows = server.commands_between(store, start, end)
    for kind in server.KINDS:
        assert counts[kind]["n"] == sum(c["kind"] == kind for c in rows), kind
        assert counts[kind]["failed"] == sum(c["kind"] == kind and not c["ok"] for c in rows), kind
    assert counts["tracert"] == {"n": 1, "failed": 1}


def test_failed_rows_say_so_and_traceroute_keeps_its_output(store):
    rows = server.commands_between(store, T0, T0 + timedelta(minutes=30), failed=True)
    assert rows and not any(c["ok"] for c in rows)
    traces = [c for c in rows if c["kind"] == "tracert"]
    assert len(traces) == 2 and "Tracing route" in traces[0]["output"]
    assert traces[1]["result"] == "internet down, router ping no answer in 1 s"


def test_series_is_the_same_from_memory_and_from_the_files(store):
    start, end = T0 + timedelta(minutes=5), T0 + timedelta(minutes=10)
    from_memory = server.series(store, start, end)
    for q in (store.pings, store.diag, store.radio):
        q.clear()
    assert server.series(store, start, end) == from_memory
    assert from_memory["step"] == 1000 and sum(r["routerSent"] for r in from_memory["rows"]) == 300


def test_step_stays_under_the_chart_limit():
    assert server.step_for(300) == 1
    assert server.step_for(3600) == 20
    for seconds in (10, 900, 21600, 86400, 86400 * 30):
        assert seconds / server.step_for(seconds) <= server.MAX_BUCKETS


def test_parse_range(store):
    now = T0 + timedelta(hours=1)
    assert server.parse_range({}, store, now)[2:] == ("1h", True)
    assert server.parse_range({"window": ["15m"]}, store, now)[0] == now - timedelta(minutes=15)
    assert server.parse_range({"window": ["all"]}, store, now)[0] == store.first
    a, b = server.ms_of(T0), server.ms_of(T0 + timedelta(minutes=5))
    start, end, _, live = server.parse_range({"from": [str(a)], "to": [str(b)]}, store, now)
    assert (start, end, live) == (T0, T0 + timedelta(minutes=5), False)
    # an end in the future stops at now
    far = server.ms_of(now + timedelta(days=1))
    assert server.parse_range({"from": [str(a)], "to": [str(far)]}, store, now)[1] == now
    for bad in ({"window": ["1y"]}, {"from": [str(a)]}, {"from": ["x"], "to": [str(b)]}, {"from": [str(b)], "to": [str(a)]},
                {"from": [str(a)], "to": [str(a + 9999)]}):
        with pytest.raises(server.BadRange):
            server.parse_range(bad, store, now)


def test_another_website_cannot_start_an_export():
    assert server.request_allowed_to_act({})
    assert server.request_allowed_to_act({"Sec-Fetch-Site": "same-origin"})
    assert server.request_allowed_to_act({"Sec-Fetch-Site": "none"})
    assert not server.request_allowed_to_act({"Sec-Fetch-Site": "cross-site"})
    assert not server.request_allowed_to_act({"Sec-Fetch-Site": "same-site"})


def test_report_page_carries_the_range_and_its_commands(store, tmp_path):
    (tmp_path / "index.html").write_text("<body><!--REPORT--><script>x</script></body>", encoding="utf-8")
    app = server.App()
    app.store = store
    store.ready = True
    q = {"from": [str(server.ms_of(T0))], "to": [str(server.ms_of(T0 + timedelta(minutes=10)))]}
    page = app.report_html(q)
    assert page.index("window.__REPORT__") < page.index("<script>x")
    assert '"listedAll": false' in page and "Tracing route" in page
    assert '"listedAll": true' in app.report_html({**q, "all": ["1"]})
