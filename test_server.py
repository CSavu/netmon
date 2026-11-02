"""The dashboard must report what netmon_data reports. Run: python -m pytest test_server.py -q

Feeds a fixed copy of the logs through the row-by-row Track and compares it with netmon_data's whole-file functions.
"""
import json
import re
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
        out += [(ts, nd.ROUTER, r, 1 if r else 0), (ts + timedelta(milliseconds=100), nd.NET[0], n, 10 if n else 0),
                (ts + timedelta(milliseconds=200), nd.NET[1], n, 20 if n else 0)]
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
            store.pings.extend([(ts, nd.ROUTER, router_ok, 1), (ts, nd.NET[0], net_ok, 10), (ts, nd.NET[1], net_ok, 20)])
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
    links = ["time,link,address"]
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
            links.append(f"{stamp},Cable,192.168.1.6")
    (tmp_path / "netmon.csv").write_text("\n".join(pings) + "\n", encoding="utf-8")
    (tmp_path / "diag.csv").write_text("\n".join(diag) + "\n", encoding="utf-8")
    (tmp_path / "wifi.csv").write_text("\n".join(wifi) + "\n", encoding="utf-8")
    (tmp_path / "link.csv").write_text("\n".join(links) + "\n", encoding="utf-8")
    (tmp_path / "diag_traceroute.txt").write_text(
        "===== 2026-03-01 10:05:00 internet down (router ping: 3 ms) =====\n\nTracing route\n  1  192.168.1.1\n"
        "===== 2026-03-01 10:20:00 internet down (router ping: -1 ms) =====\n  1  *\n", encoding="utf-8")
    s = server.Store()
    s.poll()
    return s


def key(c):
    return (c["t"], c["kind"], c["cmd"])


@pytest.mark.parametrize("name, a, b", [("netmon", 0, 1800), ("netmon", 700, 712), ("diag", 3, 4), ("diag", 1799, 1800),
                                        ("wifi", 100, 500), ("link", 0, 95), ("netmon", 1800, 1900)])
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


def at(minute, second=0):
    return datetime(2026, 11, 3, 10, minute, second)


def test_wifi_spans_follow_the_link_log(nd):
    start = datetime(2026, 11, 2, 13, 0)
    marked = [(start, None)]
    assert nd.wifi_spans([], marked) == marked
    # still on Wi-Fi when the log begins: one span, carried on from the hand-marked one
    assert nd.wifi_spans([(at(0), "Wi-Fi"), (at(1), "Wi-Fi")], marked) == [(start, None)]
    assert nd.wifi_spans([(at(0), "Wi-Fi"), (at(5), "Cable")], marked) == [(start, at(5))]
    assert nd.wifi_spans([(at(0), "Cable"), (at(5), "Wi-Fi"), (at(9), "Cable"), (at(12), "Wi-Fi")], marked) == [
        (start, at(0)), (at(5), at(9)), (at(12), None)]
    # no reading of the other link: a dropped Wi-Fi or a VPN is still the same setup
    assert nd.wifi_spans([(at(0), "Wi-Fi"), (at(1), "none"), (at(2), "other"), (at(3), "Wi-Fi")], marked) == [(start, None)]
    assert nd.wifi_spans([(at(0), "Cable"), (at(1), "none"), (at(2), "Cable")], marked) == [(start, at(0))]
    # hand-marked spans over before the log began stay as they are
    assert nd.wifi_spans([(at(0), "Cable")], [(start, at(0) - timedelta(hours=1))]) == [(start, at(0) - timedelta(hours=1))]


def test_read_link_skips_what_it_cannot_read(tmp_path, nd):
    p = tmp_path / "link.csv"
    p.write_bytes(b"\xef\xbb\xbftime,link,address\r\n2026-11-03 10:00:00.000,Wi-Fi,192.168.1.6\r\n"
                  b"\x00\x00garbage\r\n2026-11-03 10:00:05.000,Cable,192.168.1.10\r\n2026-11-03 10:00:1")
    assert nd.read_link(p) == [(at(0), "Wi-Fi"), (at(0, 5), "Cable")]
    assert nd.read_link(tmp_path / "missing.csv") == []


@pytest.fixture
def switching(tmp_path, monkeypatch, nd):
    """A laptop on Wi-Fi that plugs the cable in at 10:10, with pings lost around the switch."""
    (tmp_path / "monitors").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "monitors" / "netmon_data.py").write_text(Path(nd.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(server, "HERE", tmp_path)
    monkeypatch.setattr(server, "MONITORS_DIR", tmp_path / "monitors")
    monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
    pings = ["time,target,ok,ms"]
    for i in range(1200):
        ts = at(0) + timedelta(seconds=i)
        ok = not (at(9, 50) <= ts < at(10, 10))
        pings.append(f"{ts.isoformat(sep=' ', timespec='milliseconds')},192.168.1.1,{ok},{1 if ok else 0}")
    (tmp_path / "data" / "netmon.csv").write_text("\n".join(pings) + "\n", encoding="utf-8")
    (tmp_path / "data" / "settings.json").write_text(json.dumps({
        "wifi_before_link_log": [["2026-11-02T13:35:34", None]],
        "restarts": [["2026-11-02T17:52:00", "2026-11-02T17:57:00"]]}), encoding="utf-8")
    return tmp_path / "data" / "link.csv"


def link_rows(minutes):
    return "time,link,address\n" + "".join(
        f"{at(m).isoformat(sep=' ', timespec='milliseconds')},{link},192.168.1.{10 if link == 'Cable' else 6}\n" for m, link in minutes)


def test_plugging_in_the_cable_moves_the_rows_by_itself(switching):
    switching.write_text(link_rows([(0, "Wi-Fi"), (5, "Wi-Fi"), (10, "Cable"), (15, "Cable")]), encoding="utf-8")
    store = server.Store()
    store.poll()
    assert store.nd.WIFI_SPANS == [(datetime(2026, 11, 2, 13, 35, 34), at(10))]
    assert not store.rules_outdated
    assert max(store.wifi.mins) == at(9) and min(store.wired.mins) == at(10)
    # the minutes either side of the switch end one stretch and start the other, so its lost pings count against neither
    store.wifi.settle(); store.wired.settle()
    assert store.wifi.mins[at(9)].lost["router"] == 10 and store.wifi.mins[at(9)].excluded
    assert store.wired.mins[at(10)].lost["router"] == 10 and store.wired.mins[at(10)].excluded
    labels = [m["label"] for m in server.markers_of(store.nd, at(0), at(20))]
    assert labels == ["To cable"]
    assert [p["label"] for p in server.periods_of(store, at(0), at(20))] == [
        "Cable, after restart 2026-11-02 17:57", "Wi-Fi, after restart 2026-11-02 17:57"]


def test_a_switch_while_running_asks_for_the_logs_to_be_read_again(switching):
    switching.write_text(link_rows([(0, "Wi-Fi")]), encoding="utf-8")
    store = server.Store()
    store.poll()
    assert not store.rules_outdated and store.nd.on_wifi(at(15))
    with open(switching, "a", encoding="utf-8") as f:
        f.write(link_rows([(1, "none"), (2, "other"), (3, "Wi-Fi")]).split("\n", 1)[1])
    store.poll()
    assert not store.rules_outdated  # a Wi-Fi drop or a VPN is no change of setup
    with open(switching, "a", encoding="utf-8") as f:
        f.write(link_rows([(10, "Cable")]).split("\n", 1)[1])
    store.poll()
    assert store.rules_outdated
    fresh = server.Store()
    fresh.poll()
    assert not fresh.rules_outdated and not fresh.nd.on_wifi(at(15))


def test_the_banner_reads_the_newest_link(switching):
    store = server.Store()
    assert server.link_of(store, at(0)) == ("Link unknown", None)
    store.link_now = (at(0), "Cable", "192.168.1.10")
    assert server.link_of(store, at(0, 5)) == ("Cable", "192.168.1.10")
    assert server.link_of(store, at(1)) == ("Link unknown", None)  # link.ps1 stopped
    store.link_now = (at(0), "none", "")
    assert server.link_of(store, at(0, 5)) == (None, None)
    store.link_now = (at(0), "other", "10.0.0.5")
    assert server.link_of(store, at(0, 5)) == (None, None)


def test_link_checks_are_listed_as_commands(switching):
    switching.write_text(link_rows([(0, "Wi-Fi"), (1, "none"), (2, "Cable")]), encoding="utf-8")
    store = server.Store()
    store.poll()
    rows = server.commands_between(store, at(0), at(3), ["link"])
    assert [(c["result"], c["ok"]) for c in rows] == [
        ("Wi-Fi, 192.168.1.6", True), ("none, 192.168.1.6", False), ("Cable, 192.168.1.10", True)]
    assert server.command_counts(store, at(0), at(3))["link"] == {"n": 3, "failed": 1}


def test_a_new_install_marks_nothing_and_still_sorts_cable_from_wifi(switching):
    (switching.parent / "settings.json").unlink()
    switching.write_text(link_rows([(0, "Wi-Fi"), (10, "Cable")]), encoding="utf-8")
    store = server.Store()
    store.poll()
    nd = store.nd
    assert (nd.BOX, nd.BOX_REMOVED, nd.HAND_MARKED_WIFI, nd.ROUTER_RESTARTS) == (None, None, [], [])
    assert nd.WIFI_SPANS == [(at(0), at(10))]
    assert [p["label"] for p in server.periods_of(store, at(0), at(20))] == ["Cable", "Wi-Fi"]
    assert [m["label"] for m in server.markers_of(nd, at(0), at(20))] == ["To Wi-Fi", "To cable"]


def box_settings(switching, **box):
    (switching.parent / "settings.json").write_text(json.dumps({
        "router": "10.0.0.1", "internet": ["9.9.9.9", "8.8.4.4"], "box": {"address": "10.0.0.2", "name": "Mesh", **box}}), encoding="utf-8")
    pings = ["time,target,ok,ms"]
    for i in range(600):
        ts = at(0) + timedelta(seconds=i)
        stamp = ts.isoformat(sep=" ", timespec="milliseconds")
        pings += [f"{stamp},10.0.0.1,True,1", f"{stamp},10.0.0.2,{i != 300},1", f"{stamp},9.9.9.9,True,9", f"{stamp},8.8.4.4,True,9"]
    (switching.parent / "netmon.csv").write_text("\n".join(pings) + "\n", encoding="utf-8")
    store = server.Store()
    store.poll()
    store.wired.settle()
    return store


def test_the_settings_name_the_router_the_hosts_and_a_box(switching):
    store = box_settings(switching)
    assert (store.nd.ROUTER, store.nd.NET, store.nd.BOX) == ("10.0.0.1", ("9.9.9.9", "8.8.4.4"), "10.0.0.2")
    # the minute the box lost a ping is its doing, not the line's
    assert store.wired.mins[at(5)].excluded and store.wired.mins[at(5)].lost["box"] == 1
    assert [p["label"] for p in server.periods_of(store, at(0), at(10))] == ["Via Mesh, cable"]
    commands = {d["kind"]: d["command"] for d in server.definitions(store.nd)}
    assert commands["diag_ping"] == "Ping.Send('10.0.0.1', 1000)"
    assert commands["tracert"] == "tracert -d -h 6 -w 500 8.8.4.4"
    assert commands["link"] == "Find-NetRoute -RemoteIPAddress 10.0.0.1"


def test_a_removed_box_splits_the_periods_and_its_rows_stop_counting(switching):
    store = box_settings(switching, removed=at(5).isoformat())
    assert not store.wired.mins[at(6)].lost["box"] and not store.wired.mins[at(6)].n["box"]
    assert [p["label"] for p in server.periods_of(store, at(0), at(10))] == ["Via Mesh, cable", "Cable"]
    assert [m["label"] for m in server.markers_of(store.nd, at(0), at(10))] == ["Mesh off"]


def test_editing_the_settings_counts_as_a_change_of_rules(switching):
    before = server.rules_mtime()
    settings = switching.parent / "settings.json"
    import os
    os.utime(settings, (1, 1))
    assert server.rules_mtime() != before
    settings.unlink()
    assert server.rules_mtime()[1] is None


def test_no_monitor_overwrites_a_setting():
    """PowerShell variable names ignore case: `$router = ...` in a monitor overwrites `$Router` from settings.ps1."""
    monitors = Path(server.__file__).parent / "monitors"
    assigned = re.compile(r"\$(\w+)\s*=[^=]")
    settings = {name.lower() for name in assigned.findall((monitors / "settings.ps1").read_text(encoding="utf-8"))}
    assert "router" in settings
    for script in monitors.glob("*.ps1"):
        if script.name != "settings.ps1":
            clash = settings & {name.lower() for name in assigned.findall(script.read_text(encoding="utf-8"))}
            assert not clash, (script.name, clash)
