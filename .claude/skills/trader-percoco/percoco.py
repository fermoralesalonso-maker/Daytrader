#!/usr/bin/env python3
"""trader-percoco: reads real bars and lists the facts the Percoco method needs.

It measures; it does not decide. For each timeframe it prints swing structure,
broken levels and their retests (overside / underside), Fair Value Gaps with
their consequential encroachment (50%), Fibonacci retracement and trend-based
extension of the last impulse, RSI(14), volume and a cleanliness score.
The trade verdict is made by applying the rules in SKILL.md to this output.

  python3 percoco.py SPY                     # 1h / 15m / 5m
  python3 percoco.py NVDA --exec 1m          # execution on 1-minute bars
  python3 percoco.py QQQ --json              # machine-readable
"""
import argparse, datetime, json, sys, time, urllib.error, urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0)"
HOSTS = ("query2", "query1")
TF = {"1m": "5d", "2m": "5d", "5m": "5d", "15m": "1mo", "30m": "1mo", "60m": "3mo", "1h": "3mo", "1d": "1y"}
FIB_R = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_X = (1.0, 1.618, 2.618, 3.618)


def et(ts):
    utc = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts)
    y = utc.year
    def nth_sun(m, n):
        d = datetime.datetime(y, m, 1)
        d += datetime.timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
        return d
    dst = nth_sun(3, 2) + datetime.timedelta(hours=7) <= utc < nth_sun(11, 1) + datetime.timedelta(hours=6)
    return utc + datetime.timedelta(hours=-4 if dst else -5)


def fetch(sym, interval):
    path = "/v8/finance/chart/%s?range=%s&interval=%s" % (sym, TF[interval], interval)
    last = "no answer"
    for host in HOSTS:
        for attempt in (0, 1):
            try:
                req = urllib.request.Request("https://%s.finance.yahoo.com%s" % (host, path),
                                             headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                res = data["chart"]["result"][0]
                q = res["indicators"]["quote"][0]
                bars = []
                for i, t in enumerate(res.get("timestamp") or []):
                    c = q["close"][i]
                    if c is None:
                        continue
                    o, h, l = q["open"][i], q["high"][i], q["low"][i]
                    bars.append({"t": t, "o": o if o is not None else c, "h": h if h is not None else c,
                                 "l": l if l is not None else c, "c": c, "v": q["volume"][i] or 0})
                return bars, res.get("meta") or {}
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                if e.code == 429 and attempt == 0:
                    time.sleep(5)
                    continue
                break
            except Exception as e:  # network or shape
                last = str(e)
                break
    raise SystemExit("Yahoo did not answer for %s %s (%s)" % (sym, interval, last))


def drop_open_bar(bars, interval, meta):
    """Drop the bar still forming: analysis uses closed candles only."""
    secs = {"1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "1h": 3600}.get(interval)
    if secs and bars and bars[-1]["t"] + secs > time.time():
        return bars[:-1], True
    return bars, False


def rsi(bars, n=14):
    if len(bars) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = bars[i]["c"] - bars[i - 1]["c"]
        gains += max(d, 0); losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(bars)):
        d = bars[i]["c"] - bars[i - 1]["c"]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def pivots(bars, k):
    """Confirmed swing highs/lows: k bars on each side."""
    out = []
    for i in range(k, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if bars[i]["h"] == max(b["h"] for b in win):
            out.append(("H", i, bars[i]["h"]))
        elif bars[i]["l"] == min(b["l"] for b in win):
            out.append(("L", i, bars[i]["l"]))
    alt = []  # keep alternating H/L, the more extreme of repeats
    for p in out:
        if alt and alt[-1][0] == p[0]:
            if (p[0] == "H" and p[2] >= alt[-1][2]) or (p[0] == "L" and p[2] <= alt[-1][2]):
                alt[-1] = p
        else:
            alt.append(p)
    if alt:  # running extreme after the last confirmed swing (not yet confirmed)
        k0 = alt[-1][1] + 1
        rest = range(k0, len(bars))
        if rest:
            if alt[-1][0] == "H":
                j = min(rest, key=lambda i: bars[i]["l"]); alt.append(("L*", j, bars[j]["l"]))
            else:
                j = max(rest, key=lambda i: bars[i]["h"]); alt.append(("H*", j, bars[j]["h"]))
    return alt


def structure(piv):
    hs = [p for p in piv if p[0] == "H"][-3:]
    ls = [p for p in piv if p[0] == "L"][-3:]
    if piv and piv[-1][0] == "L*" and ls and piv[-1][2] < ls[-1][2]:
        ls = ls + [piv[-1]]
    if piv and piv[-1][0] == "H*" and hs and piv[-1][2] > hs[-1][2]:
        hs = hs + [piv[-1]]
    if len(hs) < 2 or len(ls) < 2:
        return "indefinida (pocos swings)"
    hh, hl = hs[-1][2] > hs[-2][2], ls[-1][2] > ls[-2][2]
    if hh and hl:
        return "alcista (HH + HL)"
    if not hh and not hl:
        return "bajista (LH + LL)"
    return "lateral / mixta (%s + %s)" % ("HH" if hh else "LH", "HL" if hl else "LL")


def fvgs(bars, tick):
    """3-candle gaps. Bullish: high[i-2] < low[i]. Bearish: low[i-2] > high[i].
    State after creation: open, ce_touched (traded into the 50%), filled."""
    out = []
    for i in range(2, len(bars)):
        a, b, c = bars[i - 2], bars[i - 1], bars[i]
        for kind, lo, hi in (("bull", a["h"], c["l"]), ("bear", c["h"], a["l"])):
            if hi - lo <= tick:
                continue
            ce = (lo + hi) / 2
            state, touched = "abierto", False
            for x in bars[i + 1:]:
                if kind == "bull":
                    if x["l"] <= lo: state = "rellenado"; break
                    if x["l"] <= ce: touched = True
                else:
                    if x["h"] >= hi: state = "rellenado"; break
                    if x["h"] >= ce: touched = True
            if state != "rellenado" and touched:
                state = "mitigado al CE"
            out.append({"tipo": "alcista" if kind == "bull" else "bajista", "techo": hi, "piso": lo, "ce": ce,
                        "vela_impulso": b["t"], "vela_impulso_rango": (b["l"], b["h"]), "i": i - 1, "estado": state,
                        "gap_apertura": et(a["t"]).date() != et(c["t"]).date()})
    return out


def breaks_and_retests(bars, piv):
    """Last closes beyond a confirmed swing level, and whether price came back to it."""
    out = []
    for kind, i, lvl in [p for p in piv if "*" not in p[0]][-8:]:
        for j in range(i + 1, len(bars)):
            if kind == "H" and bars[j]["c"] > lvl:
                after = bars[j + 1:]
                rt = [x for x in after if x["l"] <= lvl * 1.0007]
                held = all(x["c"] >= lvl for x in after)
                out.append({"nivel": lvl, "tipo": "ruptura alcista de swing high", "ruptura": bars[j]["t"],
                            "retest": "overside retest" if rt else "sin retest aún",
                            "retest_hora": rt[0]["t"] if rt else None, "sostiene": held})
                break
            if kind == "L" and bars[j]["c"] < lvl:
                after = bars[j + 1:]
                rt = [x for x in after if x["h"] >= lvl * 0.9993]
                held = all(x["c"] <= lvl for x in after)
                out.append({"nivel": lvl, "tipo": "ruptura bajista de swing low", "ruptura": bars[j]["t"],
                            "retest": "underside retest" if rt else "sin retest aún",
                            "retest_hora": rt[0]["t"] if rt else None, "sostiene": held})
                break
    return out[-4:]


def fib(piv, bars):
    """Dominant recent impulse = the latest leg larger than every leg after it."""
    if len(piv) < 2:
        return None
    legs = [(piv[i], piv[i + 1]) for i in range(len(piv) - 1)]
    pick = None
    for n in range(len(legs) - 1, -1, -1):
        size = abs(legs[n][1][2] - legs[n][0][2])
        if all(size > abs(b[2] - a[2]) for a, b in legs[n + 1:]):
            pick = n
    pick = len(legs) - 1 if pick is None else pick
    # widen to the biggest leg among the last 6
    best = max(range(max(0, len(legs) - 6), len(legs)), key=lambda n: abs(legs[n][1][2] - legs[n][0][2]))
    if all(abs(legs[best][1][2] - legs[best][0][2]) > abs(b[2] - a[2]) for a, b in legs[best + 1:]):
        pick = best
    a, b = legs[pick]
    up = b[2] > a[2]
    lo, hi = (a[2], b[2]) if up else (b[2], a[2])
    rng = hi - lo
    r = {str(x): (hi - rng * x) if up else (lo + rng * x) for x in FIB_R}
    after = bars[b[1] + 1:]
    ext, retr = None, None
    if after:
        c = min(x["l"] for x in after) if up else max(x["h"] for x in after)
        retr = abs(b[2] - c) / rng if rng else None
        if retr is not None and 0.2 <= retr < 1.0:
            d = b[2] - a[2]
            ext = {"A": a[2], "B": b[2], "C": c, **{str(x): c + d * x for x in FIB_X}}
    return {"impulso": "alcista" if up else "bajista", "desde": a[2], "hasta": b[2],
            "desde_et": et(bars[a[1]]["t"]).strftime("%m-%d %H:%M"), "hasta_et": et(bars[b[1]]["t"]).strftime("%m-%d %H:%M"),
            "retroceso": r, "retroceso_actual_max": round(retr, 3) if retr is not None else None,
            "extension_tendencia": ext}


def cleanliness(bars, n=20):
    seg = bars[-n:]
    if len(seg) < 3:
        return None, None
    net = abs(seg[-1]["c"] - seg[0]["c"])
    path = sum(abs(seg[i]["c"] - seg[i - 1]["c"]) for i in range(1, len(seg)))
    wick = sum((b["h"] - b["l"] - abs(b["c"] - b["o"])) / (b["h"] - b["l"]) for b in seg if b["h"] > b["l"]) / len(seg)
    return (net / path if path else 0.0), wick


def analyze(sym, interval, k, tick):
    bars, meta = fetch(sym, interval)
    bars, dropped = drop_open_bar(bars, interval, meta)
    if len(bars) < 30:
        raise SystemExit("Muy pocas velas en %s %s" % (sym, interval))
    piv = pivots(bars, k)
    last = bars[-1]
    gaps = [g for g in fvgs(bars[-150:], tick) if g["estado"] != "rellenado"]
    for g in gaps:
        g["distancia_pct"] = round((last["c"] - g["ce"]) / last["c"] * 100, 2)
    gaps.sort(key=lambda g: abs(g["distancia_pct"]))
    eff, wick = cleanliness(bars)
    vols = [b["v"] for b in bars[-21:-1] if b["v"]]
    body = last["c"] - last["o"]
    rngb = last["h"] - last["l"]
    return {
        "tf": interval, "velas": len(bars), "vela_en_formacion_excluida": dropped,
        "ultima_vela": {"hora_et": et(last["t"]).strftime("%m-%d %H:%M"), "o": last["o"], "h": last["h"], "l": last["l"],
                        "c": last["c"], "tipo": "alcista" if body > 0 else "bajista" if body < 0 else "doji",
                        "cuerpo_pct_rango": round(abs(body) / rngb * 100) if rngb else 0},
        "estructura": structure(piv),
        "swings": [{"t": p[0], "precio": p[2], "hora_et": et(bars[p[1]]["t"]).strftime("%m-%d %H:%M")} for p in piv[-6:]],
        "rupturas": [{**x, "ruptura": et(x["ruptura"]).strftime("%m-%d %H:%M"),
                      "retest_hora": et(x["retest_hora"]).strftime("%m-%d %H:%M") if x["retest_hora"] else None}
                     for x in breaks_and_retests(bars, piv)],
        "fvg_abiertos_cercanos": [{**{kk: v for kk, v in g.items() if kk not in ("i", "vela_impulso")},
                                   "vela_impulso_et": et(g["vela_impulso"]).strftime("%m-%d %H:%M")} for g in gaps[:6]],
        "fib": fib(piv, bars),
        "rsi14": round(rsi(bars), 1) if rsi(bars) is not None else None,
        "limpieza_eficiencia_20": round(eff, 2) if eff is not None else None,
        "mecha_media_pct_20": round(wick * 100) if wick is not None else None,
        "volumen_ultima_vs_media20": round(last["v"] / (sum(vols) / len(vols)), 2) if vols else None,
        "_meta": meta,
    }


def r2(x):
    return "%.2f" % x if isinstance(x, (int, float)) else str(x)


def show(sym, res):
    m = res[0]["_meta"]
    print("ACTIVO %s | precio %s | cierre previo %s | máx/mín día %s / %s" % (
        sym, r2(m.get("regularMarketPrice")), r2(m.get("chartPreviousClose") or m.get("previousClose")),
        r2(m.get("regularMarketDayHigh")), r2(m.get("regularMarketDayLow"))))
    for a in res:
        print("\n=== %s (%d velas cerradas) ===" % (a["tf"], a["velas"]))
        u = a["ultima_vela"]
        print("Última vela %s ET: O %s H %s L %s C %s (%s, cuerpo %s%% del rango)" % (
            u["hora_et"], r2(u["o"]), r2(u["h"]), r2(u["l"]), r2(u["c"]), u["tipo"], u["cuerpo_pct_rango"]))
        print("Estructura: %s | RSI14 %s | eficiencia %s | mecha media %s%% | vol/media %s" % (
            a["estructura"], a["rsi14"], a["limpieza_eficiencia_20"], a["mecha_media_pct_20"], a["volumen_ultima_vs_media20"]))
        print("Swings: " + ", ".join("%s %s (%s)" % (s["t"], r2(s["precio"]), s["hora_et"]) for s in a["swings"]))
        for b in a["rupturas"]:
            print("Ruptura: %s %s a las %s -> %s%s, %s" % (b["tipo"], r2(b["nivel"]), b["ruptura"], b["retest"],
                  (" (%s)" % b["retest_hora"]) if b["retest_hora"] else "", "sostiene" if b["sostiene"] else "NO sostiene"))
        for g in a["fvg_abiertos_cercanos"]:
            print("FVG %s %s-%s CE %s | impulso %s | %s%s | precio vs CE %+.2f%%" % (
                g["tipo"], r2(g["piso"]), r2(g["techo"]), r2(g["ce"]), g["vela_impulso_et"], g["estado"],
                " | INCLUYE GAP DE APERTURA" if g["gap_apertura"] else "", g["distancia_pct"]))
        f = a["fib"]
        if f:
            print("Fib impulso %s %s (%s) -> %s (%s), retroceso máx. posterior %s: %s" % (f["impulso"], r2(f["desde"]), f["desde_et"],
                  r2(f["hasta"]), f["hasta_et"], f["retroceso_actual_max"],
                  ", ".join("%s=%s" % (kk, r2(v)) for kk, v in f["retroceso"].items())))
            if not f["extension_tendencia"]:
                print("Ext. tendencia: sin A-B-C válido (retroceso fuera de 20-100%)")
            else:
                x = f["extension_tendencia"]
                print("Ext. tendencia A %s B %s C %s: %s" % (r2(x["A"]), r2(x["B"]), r2(x["C"]),
                      ", ".join("%s=%s" % (kk, r2(x[kk])) for kk in ("1.0", "1.618", "2.618", "3.618"))))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("symbol")
    p.add_argument("--htf", default="1h")
    p.add_argument("--mtf", default="15m")
    p.add_argument("--exec", dest="ex", default="5m")
    p.add_argument("--swing", type=int, default=3, help="bars each side for a swing pivot")
    p.add_argument("--tick", type=float, default=0.01, help="ignore FVGs this size or smaller")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    sym = a.symbol.upper()
    res = []
    for tf in (a.htf, a.mtf, a.ex):
        res.append(analyze(sym, tf, a.swing, a.tick))
        time.sleep(0.5)
    if a.json:
        for r in res:
            r.pop("_meta", None)
        print(json.dumps({"symbol": sym, "timeframes": res}, indent=1, default=str))
    else:
        show(sym, res)


if __name__ == "__main__":
    main()
