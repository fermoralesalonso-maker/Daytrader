#!/usr/bin/env python3
"""trader-percoco paper: simulated execution of Percoco setups. Never a real order.

  python3 paper.py open SPY     # opens a simulated trade if trader/percoco.json has a CONFIRMADO setup
  python3 paper.py update       # walks 1-minute bars: stop, target, breakeven, flat-by close
  python3 paper.py show

Rules (from SKILL.md + trader/config.json):
- Only a CONFIRMADO setup (FVG + retest + Fib/RSI + reaction candle) opens a trade.
- Fill = current price (not the ideal CE). Risk is re-measured from the fill.
- Size = floor(max_loss_per_trade_usd / risk a share), capped by max_order_usd,
  budget * max_position_pct and cash after the buffer. Under 1 share -> refused.
- SHORT only when "allow_short": true.
- Entry window no_entries_before..no_entries_after, max_trades_per_day,
  max_daily_loss_usd (realized losses + open risk), one trade per zone.
- Exits: TP1 at 2R closes half and moves the stop to breakeven (with 1 share:
  stop to +1R instead). The rest runs to the structural swing when it sits
  between 2R and 4R, otherwise to 4R. Before TP1, a close beyond the swing
  moves the stop to breakeven (Modelo B rule). Out at flat_by.
- A bar that touches both stop and target counts as the stop (conservative).
"""
import datetime, json, math, os, sys, time, urllib.request

ROOT = os.getcwd()
CFG = os.path.join(ROOT, "trader/config.json")
SIG = os.path.join(ROOT, "trader/percoco.json")
BOOK = os.path.join(ROOT, "trader/percoco_paper.json")
UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0)"
SLIP = 0.01  # simulated slippage per share, each side


def et_now():
    u = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return to_et(u)


def to_et(u):
    y = u.year
    def nth_sun(m, n):
        d = datetime.datetime(y, m, 1)
        return d + datetime.timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
    dst = nth_sun(3, 2) + datetime.timedelta(hours=7) <= u < nth_sun(11, 1) + datetime.timedelta(hours=6)
    return u + datetime.timedelta(hours=-4 if dst else -5)


def et_ts(ts):
    return to_et(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts))


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(book):
    with open(BOOK, "w") as f:
        json.dump(book, f, indent=1)


def bars_1m(sym):
    for host in ("query2", "query1"):
        try:
            req = urllib.request.Request("https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=1m" % (host, sym),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read().decode())["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            out = []
            for i, t in enumerate(res.get("timestamp") or []):
                if q["close"][i] is None:
                    continue
                out.append({"t": t, "o": q["open"][i], "h": q["high"][i], "l": q["low"][i], "c": q["close"][i]})
            if out and out[-1]["t"] + 60 > time.time():
                out = out[:-1]  # still forming
            return out, res["meta"].get("regularMarketPrice")
        except Exception:
            time.sleep(1)
    raise SystemExit("Yahoo no respondió para %s" % sym)


def hhmm(s):
    h, m = s.split(":")
    return datetime.time(int(h), int(m))


def today_trades(book, day):
    return [t for t in book["trades"] if t["date"] == day]


def cmd_open(sym):
    cfg = load(CFG, {})
    sig = load(SIG, None)
    book = load(BOOK, {"note": "Simulación en papel de setups Percoco. Ninguna orden real.", "trades": []})
    now = et_now()
    day = now.strftime("%Y-%m-%d")
    if not sig or sig.get("symbol") != sym:
        return print("Sin señal: corre primero percoco.py %s --write" % sym)
    age = (now - datetime.datetime.strptime(sig["as_of_et"], "%Y-%m-%d %H:%M")).total_seconds() / 60
    if age > 10:
        return print("Señal vieja (%.0f min): corre percoco.py %s --write" % (age, sym))
    conf = [s for s in sig["setups"] if s["estado"].startswith("CONFIRMADO")]
    if not conf:
        return print("Sin setup CONFIRMADO en %s: no se abre nada." % sym)
    s = conf[0]
    side = s["lado"]
    if side == "SHORT" and not cfg.get("allow_short"):
        return print("BLOQUEADO: SHORT con allow_short=false.")
    if not (hhmm(cfg.get("no_entries_before", "09:45")) <= now.time() <= hhmm(cfg.get("no_entries_after", "15:30"))):
        return print("BLOQUEADO: fuera de la ventana de entradas (%s-%s ET)." % (cfg.get("no_entries_before"), cfg.get("no_entries_after")))
    tt = today_trades(book, day)
    if len(tt) >= cfg.get("max_trades_per_day", 3):
        return print("BLOQUEADO: ya hay %d operaciones hoy (máximo %s)." % (len(tt), cfg.get("max_trades_per_day")))
    key = "%s|%s|%.2f-%.2f" % (sym, side, s["zona"][0], s["zona"][1])
    if any(t["key"] == key for t in tt):
        return print("Ya se operó esta zona hoy: no se repite.")
    _, px = bars_1m(sym)
    long_ = side == "LONG"
    fill = round(px + SLIP if long_ else px - SLIP, 2)
    stop = s["stop"]
    risk = (fill - stop) if long_ else (stop - fill)
    if risk <= 0:
        return print("BLOQUEADO: el precio ya está más allá del stop.")
    maxloss = cfg.get("max_loss_per_trade_usd", 10)
    budget = cfg.get("budget", 1000)
    cash = budget * (1 - cfg.get("cash_buffer_pct", 10) / 100.0) - sum(t["shares"] * t["entry"] for t in tt if t["status"] == "abierta")
    caps = {"riesgo": math.floor(maxloss / risk), "orden_max": math.floor(cfg.get("max_order_usd", 750) / fill),
            "posicion_max": math.floor(budget * cfg.get("max_position_pct", 75) / 100.0 / fill), "efectivo": math.floor(cash / fill)}
    shares = min(caps.values())
    limit = min(caps, key=caps.get)
    if shares < 1:
        return print("BLOQUEADO: 1 acción a %.2f no cabe (límite: %s)." % (fill, limit))
    realized = sum(t.get("pnl_usd", 0) for t in tt if t["status"] == "cerrada")
    open_risk = sum(t["risk_usd"] for t in tt if t["status"] == "abierta")
    if -realized + open_risk + shares * risk > cfg.get("max_daily_loss_usd", 30):
        return print("BLOQUEADO: superaría la pérdida diaria máxima (%s USD)." % cfg.get("max_daily_loss_usd"))
    sgn = 1 if long_ else -1
    tp1 = round(fill + sgn * 2 * risk, 2)
    swing_r = (s["tp_estructura"] - fill) * sgn / risk
    final_r = swing_r if 2 < swing_r <= 4 else 4
    target = round(fill + sgn * final_r * risk, 2)
    t = {"key": key, "date": day, "symbol": sym, "side": side, "model": s["modelo"], "tf": s["tf"],
         "opened_et": now.strftime("%H:%M"), "opened_ts": int(time.time()), "entry": fill, "ideal_ce": s["entrada_ce"],
         "stop": stop, "stop_initial": stop, "tp1": tp1, "tp1_done": False, "partial_shares": 0, "partial_price": None,
         "target": target, "target_r": round(final_r, 2), "swing": s["tp_estructura"], "breakeven": False,
         "shares": shares, "size_limit": limit, "risk_usd": round(shares * risk, 2), "confluences": s["confluencias"],
         "status": "abierta"}
    book["trades"].append(t)
    save(book)
    print("SIMULADA ABIERTA: %s %s %d acc a %.2f (CE ideal %.2f) | stop %.2f | TP1 2R %.2f | objetivo final %.2f (%.1fR) | riesgo %.2f USD | límite de tamaño: %s" % (
        side, sym, shares, fill, s["entrada_ce"], stop, tp1, target, final_r, t["risk_usd"], limit))


def close(t, price, why, when):
    long_ = t["side"] == "LONG"
    sgn = 1 if long_ else -1
    exitp = round(price - SLIP if long_ else price + SLIP, 2)
    rest = t["shares"] - t.get("partial_shares", 0)
    pnl = (exitp - t["entry"]) * rest * sgn
    if t.get("partial_shares"):
        pnl += (t["partial_price"] - t["entry"]) * t["partial_shares"] * sgn
    r1 = abs(t["entry"] - t["stop_initial"])
    t.update({"status": "cerrada", "exit": exitp, "exit_why": why, "closed_et": when,
              "pnl_usd": round(pnl, 2), "r_multiple": round(pnl / (r1 * t["shares"]), 2) if r1 else None})
    return "SIMULADA CERRADA: %s %s %s acc a %.2f (%s) -> %+.2f USD (%+.2fR)" % (
        t["side"], t["symbol"], rest, exitp, why, t["pnl_usd"], t["r_multiple"] or 0)


def take_tp1(t, when):
    """At 2R: close half (if 2+ shares) and move the stop to breakeven.
    With 1 share nothing can be split: lock +1R on the stop and let it run."""
    long_ = t["side"] == "LONG"
    sgn = 1 if long_ else -1
    r1 = abs(t["entry"] - t["stop_initial"])
    t["tp1_done"] = True
    if t["shares"] >= 2:
        t["partial_shares"] = t["shares"] // 2
        t["partial_price"] = round(t["tp1"] - SLIP * sgn, 2)
        t["stop"], t["breakeven"] = t["entry"], True
        return "SIMULADA: %s %s TP1 2R: cerradas %d acc a %.2f; stop del resto a breakeven %.2f" % (
            t["side"], t["symbol"], t["partial_shares"], t["partial_price"], t["entry"])
    t["stop"], t["breakeven"] = round(t["entry"] + sgn * r1, 2), True
    return "SIMULADA: %s %s llegó a 2R (%.2f): 1 sola acción, stop sube a +1R %.2f y sigue hacia %.2f" % (
        t["side"], t["symbol"], t["tp1"], t["stop"], t["target"])


def cmd_update():
    cfg = load(CFG, {})
    book = load(BOOK, {"trades": []})
    flat = hhmm(cfg.get("flat_by", "15:55"))
    msgs = []
    for t in [x for x in book["trades"] if x["status"] == "abierta"]:
        bars, _ = bars_1m(t["symbol"])
        long_ = t["side"] == "LONG"
        for b in [b for b in bars if b["t"] >= t["opened_ts"]]:
            when = et_ts(b["t"] + 60)
            hit_stop = b["l"] <= t["stop"] if long_ else b["h"] >= t["stop"]
            hit_tgt = b["h"] >= t["target"] if long_ else b["l"] <= t["target"]
            if hit_stop:
                why = "stop inicial" if not t["breakeven"] else ("stop protegido" if t.get("tp1_done") else "breakeven")
                msgs.append(close(t, t["stop"], why, when.strftime("%H:%M")))
                break
            if not t.get("tp1_done") and (b["h"] >= t["tp1"] if long_ else b["l"] <= t["tp1"]):
                msgs.append(take_tp1(t, when))
            if hit_tgt:
                msgs.append(close(t, t["target"], "objetivo final %.1fR" % t.get("target_r", 4), when.strftime("%H:%M")))
                break
            if when.time() >= flat:
                msgs.append(close(t, b["c"], "cierre por horario %s" % cfg.get("flat_by", "15:55"), when.strftime("%H:%M")))
                break
            beyond = b["c"] > t["swing"] if long_ else b["c"] < t["swing"]
            if not t["breakeven"] and beyond and not t.get("tp1_done"):
                t["breakeven"], t["stop"] = True, t["entry"]
                msgs.append("SIMULADA: %s %s stop movido a breakeven %.2f (cerró más allá del swing %.2f)" % (
                    t["side"], t["symbol"], t["entry"], t["swing"]))
    save(book)
    print("\n".join(msgs) if msgs else "Sin cambios en operaciones simuladas.")


def cmd_show():
    book = load(BOOK, {"trades": []})
    if not book["trades"]:
        return print("Sin operaciones simuladas.")
    tot = 0.0
    for t in book["trades"]:
        tot += t.get("pnl_usd", 0)
        print("%s %s %s %s %d acc entrada %.2f stop %.2f obj %.2f | %s%s" % (
            t["date"], t["opened_et"], t["side"], t["symbol"], t["shares"], t["entry"], t["stop"], t["target"], t["status"],
            (" %s %+.2f USD (%+.2fR)" % (t["exit_why"], t["pnl_usd"], t["r_multiple"] or 0)) if t["status"] == "cerrada" else ""))
    print("Resultado realizado: %+.2f USD" % tot)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] not in ("open", "update", "show"):
        raise SystemExit(__doc__)
    {"open": lambda: cmd_open(a[1].upper()), "update": cmd_update, "show": cmd_show}[a[0]]()
