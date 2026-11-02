"""Builds the evidence pack for Digi (in Romanian) from netmon.csv, diag.csv and the traceroutes.

Out, in dovada_digi/: grafic_zile.png (every monitored day), grafic_ultima_seara.png (the latest evening with
the HTTPS / DNS measurements), rezumat.txt, mesaj_digi.txt, traceroute_*.txt and jurnale.zip (both logs with full
dates). The rules for what counts are in netmon_data.py.
"""
import csv
import io
import re
import shutil
import zipfile
from collections import Counter, OrderedDict
from datetime import datetime, time, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

import netmon_data as nd

HERE = nd.DATA
OUT = HERE / "dovada_digi"
C = {"router": "#2a78d6", "net": "#eb6834", "asus": "#1baf7a"}
LABEL = {"router": "Router Digi (192.168.1.1)", "net": "Internet (1.1.1.1 + 8.8.8.8)", "asus": "Router Asus din casă (192.168.1.5)"}
INK, INK2, GRID, SURFACE, IDLE, INCIDENT = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb", "#ecebe6", "#f3e3dc"
TIMEOUT_MS = 2000  # diag.ps1 gives a connection or a lookup 2 s
LUNI = ["", "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie",
        "octombrie", "noiembrie", "decembrie"]


def ro(x, dec=1):
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def zi(d):
    return f"{d.day} {LUNI[d.month]} {d.year}"


def durata(minutes):
    h, m = divmod(int(round(minutes)), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.tick_params(colors=INK2, labelsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)


# ---------------------------------------------------------------- facts

def day_stats(mins):
    days = OrderedDict()
    for m in mins.values():
        d = days.setdefault(m.start.date(), Counter())
        d["logged"] += 1
        d["kept"] += not m.excluded
        d["bad"] += m.bad
        if not m.excluded:
            for g in C:
                d["n_" + g] += m.n[g]
                d["l_" + g] += m.lost[g]
    return days


def diag_facts(diag, mins, incs):
    bad_min = {m.start for inc in incs for m in inc}
    kept = [r for r in diag if (mm := mins.get(r[0].replace(second=0, microsecond=0))) is None or not mm.excluded]
    normal = [r for r in kept if r[0].replace(second=0, microsecond=0) not in bad_min]
    during = [r for r in kept if r[0].replace(second=0, microsecond=0) in bad_min]
    ok_ms = lambda rows, i: sorted(r[i] for r in rows if r[i] >= 0)
    med = lambda v: v[len(v) // 2] if v else None
    https_ok = lambda r: r[2] >= 0 or r[3] >= 0
    return {
        "first": diag[0][0] if diag else None, "last": diag[-1][0] if diag else None, "rows": len(kept),
        "normal_https": med(ok_ms(normal, 2)), "normal_dns": med(ok_ms(normal, 4)),
        "during_rows": len(during),
        "during_https_failed": sum(not https_ok(r) for r in during),
        "during_https_max": max([max(r[2], r[3]) for r in during if https_ok(r)] or [0]),
        "during_dns_failed": sum(r[4] < 0 for r in during),
        "during_dns_max": max([r[4] for r in during if r[4] >= 0] or [0]),
        "link_down": sum(r[5] != "Up" for r in kept),
    }


def traceroutes(path):
    """[(time, router_ping_ms_at_the_time, hop1)] — hop1 = the first hop's best reply in ms, None = no reply."""
    out = []
    if not path.exists():
        return out
    text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r", "")
    for block in text.split("=====")[1:]:
        pass
    for m in re.finditer(r"===== (\S+ \S+) internet down \(router ping: (-?\d+) ms\) =====(.*?)Trace complete", text, re.S):
        hop1 = re.search(r"^\s*1\s+(.*)$", m.group(3), re.M)
        times = [int(x) for x in re.findall(r"(\d+) ms", hop1.group(1))] if hop1 else []
        out.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), int(m.group(2)), min(times) if times else None))
    return out


# ---------------------------------------------------------------- charts

def broken(points, gap=timedelta(minutes=2)):
    """xs, ys with a break wherever the monitor logged nothing, so no line is drawn across it."""
    xs, ys, prev = [], [], None
    for t, v in points:
        if prev and t - prev > gap:
            xs.append(prev + timedelta(minutes=1))
            ys.append(float("nan"))
        xs.append(t)
        ys.append(v)
        prev = t
    return xs, ys


def chart_days(mins, spans, days):
    day_list = list(days)
    fig, axes = plt.subplots(len(day_list), 1, figsize=(12, 2.5 * len(day_list) + 1.6), facecolor=SURFACE)
    axes = list(axes) if len(day_list) > 1 else [axes]
    for ax, d in zip(axes, day_list):
        style(ax)
        lo, hi = datetime.combine(d, time()), datetime.combine(d, time()) + timedelta(days=1)
        # what was NOT monitored, in grey
        cur = lo
        for a, b in spans + [(hi, hi)]:
            if b < lo or a > hi:
                continue
            if a > cur:
                ax.axvspan(cur, min(a, hi), color=IDLE, lw=0, zorder=0)
            cur = max(cur, b)
        for g in ("asus", "net", "router"):
            ax.plot(*broken([(m.start, m.loss(g)) for m in mins.values() if m.start.date() == d and not m.excluded]),
                    color=C[g], lw=1.6, label=LABEL[g])
        st = days[d]
        ax.set_title(f"{zi(d)}: {durata(st['kept'])} monitorizate, {st['bad']} minute cu întreruperi "
                     f"({ro(100 * st['bad'] / st['kept'], 0)}%)", loc="left", fontsize=10.5, color=INK)
        # only the stretch of the day that was monitored, so the spikes stay readable
        day_mins = [m.start for m in mins.values() if m.start.date() == d and not m.excluded]
        ax.set_xlim(max(lo, day_mins[0] - timedelta(minutes=15)), min(hi, day_mins[-1] + timedelta(minutes=15)))
        ax.set_ylim(0, 105)
        ax.set_ylabel("% pierdut / min", color=INK2, fontsize=9)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=14))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    handles, names = axes[0].get_legend_handles_labels()
    order = [names.index(LABEL[g]) for g in ("router", "net", "asus")]
    from matplotlib.patches import Patch
    fig.legend([handles[i] for i in order] + [Patch(color=IDLE)],
               [names[i] for i in order] + ["nemonitorizat (PC în repaus sau monitorul oprit)"],
               loc="upper left", ncol=4, bbox_to_anchor=(0.06, 0.93), frameon=False, fontsize=9, labelcolor=INK)
    first, last = mins[next(iter(mins))].start, list(mins.values())[-1].start
    fig.suptitle(f"Pachete pierdute pe conexiunea Digi, {first:%d.%m} - {last:%d.%m.%Y}", x=0.06, y=0.995,
                 ha="left", fontsize=13, color=INK, fontweight="bold")
    fig.text(0.06, 0.965, "Ping continuu de pe un PC conectat prin cablu, la ~0,55 s către fiecare adresă; fără răspuns "
             "în 1 s = pierdut. Routerul Asus (între PC și routerul Digi) nu pierde nimic.",
             fontsize=9, color=INK2, ha="left", va="top")
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(OUT / "grafic_zile.png", dpi=150, facecolor=SURFACE)


def chart_evening(mins, diag, incs, traces):
    if not diag:
        return None
    lo = diag[0][0].replace(second=0, microsecond=0) - timedelta(minutes=10)
    hi = diag[-1][0].replace(second=0, microsecond=0) + timedelta(minutes=1)
    per_min = OrderedDict()
    for t, router, h1, h2, dns, _ in diag:
        k = t.replace(second=0, microsecond=0)
        mm = mins.get(k)
        if mm is not None and mm.excluded:
            continue
        p = per_min.setdefault(k, {"https": [], "dns": []})
        p["https"].append(min([v for v in (h1, h2) if v >= 0] or [TIMEOUT_MS]))
        p["dns"].append(dns if dns >= 0 else TIMEOUT_MS)
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(12, 9), sharex=True, facecolor=SURFACE,
                                     gridspec_kw={"hspace": 0.32})
    gaps = [(a[0], b[0]) for a, b in zip(diag, diag[1:]) if b[0] - a[0] > timedelta(minutes=2)]
    for ax in (a1, a2, a3):
        style(ax)
        for a, b in gaps:
            ax.axvspan(a, b, color=IDLE, lw=0, zorder=0)
        for inc in incs:
            s, e = inc[0].start, inc[-1].start + timedelta(minutes=1)
            if e >= lo and s <= hi:
                ax.axvspan(s, e, color=INCIDENT, lw=0, zorder=0)
    lines = {}
    for g in ("asus", "net", "router"):
        lines[g], = a1.plot(*broken([(m.start, m.loss(g)) for m in mins.values() if lo <= m.start <= hi and not m.excluded]),
                            color=C[g], lw=1.8)
    a1.set_ylim(0, 110)
    a1.set_title("Pachete pierdute (% pe minut)", loc="left", fontsize=10.5, color=INK)
    fig.legend([lines[g] for g in ("router", "net", "asus")], [LABEL[g] for g in ("router", "net", "asus")],
               loc="upper left", ncol=3, bbox_to_anchor=(0.06, 0.935), frameon=False, fontsize=9, labelcolor=INK)
    for t, _, hop1 in traces:
        if lo <= t <= hi:
            a1.annotate("▼", (t, 104), ha="center", va="center", fontsize=9, color=INK)
    a2.plot(*broken([(k, max(p["https"])) for k, p in per_min.items()]), color=C["net"], lw=1.6)
    a2.set_title("Cea mai lentă conexiune HTTPS la internet în minutul respectiv (ms; 2.000 = fără răspuns în 2 s)",
                 loc="left", fontsize=10.5, color=INK)
    a2.set_ylim(0, TIMEOUT_MS * 1.08)
    a3.plot(*broken([(k, max(p["dns"])) for k, p in per_min.items()]), color=C["net"], lw=1.6)
    a3.set_title("Cea mai lentă căutare DNS în minutul respectiv (ms; 2.000 = fără răspuns în 2 s)",
                 loc="left", fontsize=10.5, color=INK)
    a3.set_ylim(0, TIMEOUT_MS * 1.08)
    a3.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    a3.set_xlim(lo, hi)
    fig.suptitle(f"Seara de {zi(lo.date())}: pachete, conexiuni și DNS", x=0.06, y=0.985, ha="left",
                 fontsize=13, color=INK, fontweight="bold")
    fig.text(0.06, 0.952, "Zonele colorate = minute cu întreruperi. ▼ = traceroute rulat în timpul întreruperii: "
             "traseul se oprește la primul hop, routerul Digi.", fontsize=9, color=INK2, ha="left", va="top")
    fig.subplots_adjust(top=0.87)
    fig.savefig(OUT / "grafic_ultima_seara.png", dpi=150, bbox_inches="tight", pad_inches=0.3, facecolor=SURFACE)
    return lo


# ---------------------------------------------------------------- text

def write_logs(rows, diag):
    buf_p, buf_d = io.StringIO(), io.StringIO()
    w = csv.writer(buf_p)
    w.writerow(["data_ora", "adresa", "raspuns", "ms"])
    for ts, target, ok, ms in rows:
        w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], target, "da" if ok else "nu", ms if ok else ""])
    w = csv.writer(buf_d)
    w.writerow(["data_ora", "ping_router_ms", "https_1.1.1.1_ms", "https_8.8.8.8_ms", "dns_ms", "cablu"])
    for t, router, h1, h2, dns, link in diag:
        w.writerow([t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]] + [v if v >= 0 else "fara raspuns" for v in (router, h1, h2, dns)] + [link])
    with zipfile.ZipFile(OUT / "jurnale.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ping_un_rand_pe_ping.csv", "﻿" + buf_p.getvalue())
        z.writestr("conexiuni_https_dns_un_rand_pe_secunda.csv", "﻿" + buf_d.getvalue())


def main():
    OUT.mkdir(exist_ok=True)
    for old in ("grafic_intreruperi.png", "log_ping_brut.csv"):
        (OUT / old).unlink(missing_ok=True)
    rows = nd.load_pings()
    mins, spans = nd.minutes(rows)
    incs = nd.incidents(mins)
    days = day_stats(mins)
    outs = nd.outages(rows, mins)
    diag = nd.load_diag()
    traces = traceroutes(HERE / "diag_traceroute.txt")
    df = diag_facts(diag, mins, incs)
    chart_days(mins, spans, days)
    evening = chart_evening(mins, diag, incs, traces)
    write_logs(rows, diag)
    for src, dst in (("diag_traceroute.txt", "traceroute_in_timpul_intreruperilor.txt"),
                     ("diag_traceroute_baseline.txt", "traceroute_cand_merge_normal.txt")):
        if (HERE / src).exists():
            shutil.copy(HERE / src, OUT / dst)

    first, last = rows[0][0], rows[-1][0]
    kept = sum(d["kept"] for d in days.values())
    bad = sum(d["bad"] for d in days.values())
    tot = lambda k: sum(d[k] for d in days.values())
    hop1_dead = sum(1 for _, _, h in traces if h is None)
    L = [f"Interval: {first:%d.%m.%Y %H:%M} - {last:%d.%m.%Y %H:%M}, monitorizat cât timp PC-ul a fost pornit: "
         f"{durata(kept)} în total.", ""]
    L.append("Pe zile (minute monitorizate, minute cu întreruperi, pachete pierdute la routerul Digi / internet / Asus):")
    for d, st in days.items():
        L.append(f"  {zi(d)}: {durata(st['kept'])}, {st['bad']} minute cu întreruperi ({ro(100 * st['bad'] / st['kept'], 0)}%), "
                 f"pierdut {ro(100 * st['l_router'] / st['n_router'], 2)}% / {ro(100 * st['l_net'] / st['n_net'], 2)}% / "
                 f"{ro(100 * st['l_asus'] / st['n_asus'], 2)}%")
    L += ["", f"Total: {len(incs)} întreruperi, {bad} minute din {kept} ({ro(100 * bad / kept, 0)}%). "
          f"Routerul Asus: {ro(tot('l_asus'), 0)} pachete pierdute din {ro(tot('n_asus'), 0)}.", ""]
    L.append("Cele mai lungi căderi complete (nici 1.1.1.1, nici 8.8.8.8 nu răspund):")
    for a, b, s in outs[:5]:
        L.append(f"  {a:%d.%m %H:%M:%S} - {b:%H:%M:%S}: {ro(s, 0)} secunde")
    L += ["", "Întreruperi (început-sfârșit | minute | router Digi: pierdut, cel mai lent răspuns | internet: pierdut | Asus: pierdut):"]
    for inc in incs:
        agg = lambda g, f: sum(getattr(m, f)[g] for m in inc)
        L.append(f"  {inc[0].start:%d.%m %H:%M}-{inc[-1].start:%H:%M} | {len(inc)} | "
                 f"{ro(100 * agg('router', 'lost') / agg('router', 'n'))}%, {max(m.worst['router'] for m in inc)} ms | "
                 f"{ro(100 * agg('net', 'lost') / agg('net', 'n'))}% | {ro(100 * agg('asus', 'lost') / max(1, agg('asus', 'n')))}%")
    if diag:
        L += ["", f"Conexiuni reale (din {df['first']:%d.%m %H:%M}, o măsurătoare pe secundă): normal o conexiune HTTPS "
              f"se deschide în {df['normal_https']} ms și o căutare DNS durează {df['normal_dns']} ms. În minutele cu "
              f"întreruperi: {df['during_https_failed']} din {df['during_rows']} încercări HTTPS fără răspuns în 2 s, "
              f"cea mai lentă reușită {ro(df['during_https_max'], 0)} ms; DNS fără răspuns de {df['during_dns_failed']} ori, "
              f"cel mai lent {ro(df['during_dns_max'], 0)} ms. Cablul PC-ului a fost conectat tot timpul "
              f"({df['link_down']} secunde deconectat)."]
    if traces:
        L += ["", f"Traceroute în timpul întreruperilor: {len(traces)}; în {hop1_dead} dintre ele routerul Digi (primul hop) "
              f"nu a răspuns deloc, în rest a răspuns în {', '.join(str(h) + ' ms' for _, _, h in traces if h is not None)}. "
              "Când merge normal, routerul răspunde sub 1 ms și rețeaua Digi în 7-24 ms."]
    (OUT / "rezumat.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    write_message(first, last, days, incs, outs, df, traces, kept, bad, tot)
    print("\n".join(L))


def write_message(first, last, days, incs, outs, df, traces, kept, bad, tot):
    per_day = "\n".join(f"- {zi(d)}: {st['bad']} minute cu întreruperi din {durata(st['kept'])} monitorizate "
                        f"({ro(100 * st['bad'] / st['kept'], 0)}%)" for d, st in days.items())
    worst = "\n".join(f"  - {a:%d.%m.%Y %H:%M:%S} - {b:%H:%M:%S}: {ro(s, 0)} secunde fără internet" for a, b, s in outs[:3])
    windows = ", ".join(f"{inc[0].start:%d.%m %H:%M}-{inc[-1].start:%H:%M}" for inc in sorted(incs, key=len, reverse=True)[:8])
    hop1_dead = sum(1 for _, _, h in traces if h is None)
    conn = ""
    if df["first"]:
        conn = (f"\nPe {zi(df['first'].date())} am măsurat și trafic real (conexiuni HTTPS și căutări DNS, una pe secundă): "
                f"normal o conexiune se deschide în {df['normal_https']} ms, dar în minutele cu probleme "
                f"în {df['during_https_failed']} din {df['during_rows']} secunde nicio conexiune nu s-a deschis în 2 secunde, iar "
                f"căutările DNS au durat până la {ro(df['during_dns_max'], 0)} ms.\n")
    trace = ""
    if traces:
        trace = (f"\nÎn timpul întreruperilor am rulat {len(traces)} traceroute: în {hop1_dead} dintre ele traseul se oprește "
                 "chiar la primul hop, routerul Digi (192.168.1.1), care nu răspunde; când merge normal, routerul răspunde "
                 "sub 1 ms și rețeaua Digi în 7-24 ms (atașez ambele).\n")
    text = f"""Bună ziua,

Am întreruperi repetate ale conexiunii la internet, deși testul de viteză arată 400-500 Mbps.
Le-am măsurat între {first:%d.%m.%Y %H:%M} și {last:%d.%m.%Y %H:%M}, cât timp calculatorul a fost pornit
({durata(kept)} în total), cu ping continuu de pe un PC conectat prin cablu (câte un ping la aproximativ
0,55 secunde; fără răspuns în 1 secundă = pierdut) către routerul Digi (192.168.1.1), către routerul meu Asus
(192.168.1.5, folosit doar ca access point între PC și routerul Digi) și către internet (1.1.1.1 și 8.8.8.8).

Rezultate:
{per_day}
- În total {len(incs)} întreruperi; cele mai lungi căderi complete:
{worst}
- Routerul Asus a pierdut {ro(tot('l_asus'), 0)} pachete din {ro(tot('n_asus'), 0)}: PC-ul, cablul și rețeaua din casă funcționează.
{conn}{trace}
Routerul Digi însuși nu mai răspunde sau răspunde cu până la o secundă întârziere, chiar și din rețeaua locală,
exact în minutele în care cade internetul. Atașez graficele, rezumatul și jurnalele complete.

Routerul este un FiberHome AN5506-02-FG, are peste 10 ani și are Wi-Fi doar pe 2,4 GHz. Vă rog:
1. să verificați nivelul semnalului optic pe linia mea și jurnalul de alarme al routerului în intervalele
   de mai sus (de exemplu {windows});
2. să trimiteți un tehnician care să măsoare semnalul la priză și să curețe conectorii fibrei;
3. să înlocuiți routerul cu un model actual.

Cod client: ____________
Adresă: ____________

Mulțumesc,
____________
"""
    (OUT / "mesaj_digi.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
