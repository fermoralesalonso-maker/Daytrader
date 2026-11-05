#!/usr/bin/env python3
"""trader-percoco paper: simulated execution of Percoco setups. Never a real order.

  python3 paper.py open SPY     # opens a simulated trade if trader/percoco.json has a CONFIRMADO setup
  python3 paper.py update       # walks 1-minute bars: stop, target, breakeven, flat-by close
  python3 paper.py show
  python3 paper.py cycle SPY ED AWK SO   # all of the above for a watchlist; prints only changes

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


SKILLS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
NEWS_BLOCK = {"earnings", "deal", "risk", "halt", "regulatory", "legal"}
INDEX_ETFS = {"SPY", "VOO", "IVV", "QQQ", "IWM", "DIA", "SMH", "SOXX", "XLF", "XLE", "XLK", "XLV", "XLI", "XLU",
              "XLP", "XLY", "XLB", "XLRE", "XLC", "TLT", "GLD"}


def _run(args, timeout=240):
    import subprocess
    r = subprocess.run([sys.executable] + args, capture_output=True, text=True, cwd=ROOT, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def gate_news(sym):
    """/trader-news: block on a fresh (24h) earnings, deal, legal, halt, regulatory or risk item, or an important 8-K."""
    try:
        code, out, err = _run([os.path.join(SKILLS, "trader-news/news.py"), "--tickers", sym, "--hours", "24",
                               "--all", "--dry-run", "--json"], 120)
        d = json.loads(out)
    except Exception as e:
        return False, "noticias no disponibles (%s): por prudencia no se abre" % str(e)[:60]
    moving = any(m.get("ticker") == sym and m.get("moving") for m in d.get("moves", []))
    mine = [it for it in d.get("items", []) if sym in (it.get("touches") or [it.get("ticker")])]
    # Block: an important 8-K, a halt, or a strong headline while the stock is making an unusual move.
    bad = [it for it in mine if (it.get("kind") == "filing" and it.get("important"))
           or "halt" in (it.get("tags") or [])
           or (moving and sym not in INDEX_ETFS and NEWS_BLOCK & set(it.get("tags") or []))]
    if bad:
        it = bad[0]
        return False, "noticia fuerte [%s]: %s" % (",".join(it.get("tags") or [it.get("kind")]), it.get("title", "")[:90])
    return True, "sin noticias fuertes en 24h (%d titulares revisados)" % len(d.get("items", []))


def gate_timemachine(sym, side, entry, stop, tp1, when_et):
    """/trader-timemachine at the 2R first target: target-first must beat 1/3 of decided replays (2R break-even)."""
    mc, last_err = None, ""
    n = et_now()
    base = n.replace(minute=n.minute - n.minute % 5, second=0, microsecond=0)
    for back in (0, 5, 10, 15):  # the last completed 5-minute bar Yahoo already has
        t = (base - datetime.timedelta(minutes=back)).strftime("%I:%M %p").lstrip("0")
        code, out, err = _run([os.path.join(SKILLS, "trader-timemachine/timemachine.py"), "run", "--symbol", sym,
                               "--side", "buy" if side == "LONG" else "sell", "--setup", "level", "--entry", "%.2f" % entry,
                               "--stop", "%.2f" % stop, "--target", "%.2f" % tp1, "--entry-time", t, "--json"])
        try:
            mc = json.loads(out)["mc"]
            break
        except Exception:
            last_err = (err or out).strip()[-100:]
    if mc is None:
        return False, "simulación no disponible (%s): por prudencia no se abre" % last_err, None
    pt, ps = mc["p_target"], mc["p_stop"]
    share = pt / (pt + ps) if pt + ps else 0
    info = {"p_target_2R": pt, "p_stop": ps, "p_neither": mc["p_neither"], "avg_R": mc["avg_R"], "target_first_share": round(share, 3)}
    ok = share >= 1 / 3.0 and mc["avg_R"] > 0
    return ok, "2R primero %.0f%% vs stop %.0f%% (necesita >=33%% de los decididos, tiene %.0f%%; R medio %+.2f)" % (
        pt * 100, ps * 100, share * 100, mc["avg_R"]), info


def gate_risk_long(sym, entry, stop, tp1):
    """/trader-exit -> /trader-budget -> /trader-risk for a LONG, on side files (never trader/plan.json)."""
    st = os.path.join(ROOT, "trader/state")
    os.makedirs(st, exist_ok=True)
    plan, orders = os.path.join(st, "percoco_plan.json"), os.path.join(st, "percoco_orders.json")
    code, out, err = _run([os.path.join(SKILLS, "trader-exit/exit.py"), "plan", "--symbol", sym, "--entry", "%.2f" % entry,
                           "--setup", "level", "--stop", "price:%.2f" % stop, "--target", "price:%.2f" % tp1, "--write", plan])
    if code:
        return False, "trader-exit rechazó: %s" % (err or out).strip().splitlines()[-1][:100]
    code, out, err = _run([os.path.join(SKILLS, "trader-budget/size.py"), "--plan", plan, "--write", orders])
    if code:
        return False, "trader-budget rechazó: %s" % (err or out).strip().splitlines()[-1][:100]
    code, out, err = _run([os.path.join(SKILLS, "trader-risk/check.py"), "--orders", orders, "--no-write", "--json"])
    txt = out or err
    if code or "BLOCK" in txt:
        line = [l for l in txt.splitlines() if "BLOCK" in l or "reason" in l]
        return False, "trader-risk BLOQUEA: %s" % (line[0].strip()[:120] if line else txt.strip()[-120:])
    return True, "trader-exit, trader-budget y trader-risk: PASS"


def cmd_open(sym, sig=None):
    cfg = load(CFG, {})
    sig = sig or load(SIG, None)
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
    frac = bool(cfg.get("fractional"))
    q = (lambda x: math.floor(x * 1000) / 1000.0) if frac else math.floor
    caps = {"riesgo": q(maxloss / risk), "orden_max": q(cfg.get("max_order_usd", 750) / fill),
            "posicion_max": q(budget * cfg.get("max_position_pct", 75) / 100.0 / fill), "efectivo": q(max(cash, 0) / fill)}
    shares = min(caps.values())
    limit = min(caps, key=caps.get)
    if shares * fill < cfg.get("min_order_usd", 5) or shares <= 0:
        return print("BLOQUEADO: no cabe una posición mínima a %.2f (límite: %s)." % (fill, limit))
    realized = sum(t.get("pnl_usd", 0) for t in tt if t["status"] == "cerrada")
    open_risk = sum(t["risk_usd"] for t in tt if t["status"] == "abierta")
    if -realized + open_risk + shares * risk > cfg.get("max_daily_loss_usd", 30):
        return print("BLOQUEADO: superaría la pérdida diaria máxima (%s USD)." % cfg.get("max_daily_loss_usd"))
    sgn = 1 if long_ else -1
    tp1 = round(fill + sgn * 2 * risk, 2)
    swing_r = (s["tp_estructura"] - fill) * sgn / risk
    final_r = swing_r if 2 < swing_r <= 4 else 4
    target = round(fill + sgn * final_r * risk, 2)
    # Pre-trade checks with the /trader skills. Any failure = no trade.
    gates = {}
    ok, why = gate_news(sym); gates["news"] = why
    if not ok:
        return print("BLOQUEADO por /trader-news: %s" % why)
    ok, why, mc = gate_timemachine(sym, side, fill, stop, tp1, now.strftime("%I:%M %p").lstrip("0"))
    gates["timemachine"] = why
    if not ok:
        return print("BLOQUEADO por /trader-timemachine: %s" % why)
    if long_:
        ok, why = gate_risk_long(sym, fill, stop, tp1); gates["risk"] = why
        if not ok:
            return print("BLOQUEADO por %s" % why)
    else:
        gates["risk"] = "SHORT: límites de config aplicados aquí (trader-risk solo revisa compras)"
    t = {"key": key, "date": day, "symbol": sym, "side": side, "model": s["modelo"], "tf": s["tf"],
         "opened_et": now.strftime("%H:%M"), "opened_ts": int(time.time()), "entry": fill, "ideal_ce": s["entrada_ce"],
         "stop": stop, "stop_initial": stop, "tp1": tp1, "tp1_done": False, "partial_shares": 0, "partial_price": None,
         "target": target, "target_r": round(final_r, 2), "swing": s["tp_estructura"], "breakeven": False,
         "shares": shares, "size_limit": limit, "checks": gates, "timemachine": mc, "risk_usd": round(shares * risk, 2), "confluences": s["confluencias"],
         "status": "abierta"}
    book["trades"].append(t)
    save(book)
    print("Revisiones: noticias OK (%s) | simulación OK (%s) | riesgo: %s" % (gates["news"], gates["timemachine"], gates["risk"]))
    print("SIMULADA ABIERTA: %s %s %g acc a %.2f (CE ideal %.2f) | stop %.2f | TP1 2R %.2f | objetivo final %.2f (%.1fR) | riesgo %.2f USD | límite de tamaño: %s" % (
        side, sym, shares, fill, s["entrada_ce"], stop, tp1, target, final_r, t["risk_usd"], limit))


def close(t, price, why, when):
    long_ = t["side"] == "LONG"
    sgn = 1 if long_ else -1
    exitp = round(price - SLIP if long_ else price + SLIP, 2)
    rest = round(t["shares"] - t.get("partial_shares", 0), 3)
    pnl = (exitp - t["entry"]) * rest * sgn
    if t.get("partial_shares"):
        pnl += (t["partial_price"] - t["entry"]) * t["partial_shares"] * sgn
    r1 = abs(t["entry"] - t["stop_initial"])
    t.update({"status": "cerrada", "exit": exitp, "exit_why": why, "closed_et": when,
              "pnl_usd": round(pnl, 2), "r_multiple": round(pnl / (r1 * t["shares"]), 2) if r1 else None})
    return "SIMULADA CERRADA: %s %s %g acc a %.2f (%s) -> %+.2f USD (%+.2fR)" % (
        t["side"], t["symbol"], rest, exitp, why, t["pnl_usd"], t["r_multiple"] or 0)


def take_tp1(t, when):
    """At 2R: close half (if 2+ shares) and move the stop to breakeven.
    With 1 share nothing can be split: lock +1R on the stop and let it run."""
    long_ = t["side"] == "LONG"
    sgn = 1 if long_ else -1
    r1 = abs(t["entry"] - t["stop_initial"])
    t["tp1_done"] = True
    half = (math.floor(t["shares"] * 500) / 1000.0) if t["shares"] != int(t["shares"]) else t["shares"] // 2
    if half > 0:
        t["partial_shares"] = half
        t["partial_price"] = round(t["tp1"] - SLIP * sgn, 2)
        t["stop"], t["breakeven"] = t["entry"], True
        return "SIMULADA: %s %s TP1 2R: cerradas %g acc a %.2f; stop del resto a breakeven %.2f" % (
            t["side"], t["symbol"], t["partial_shares"], t["partial_price"], t["entry"])
    t["stop"], t["breakeven"] = round(t["entry"] + sgn * r1, 2), True
    return "SIMULADA: %s %s llegó a 2R (%.2f): no se puede dividir, stop sube a +1R %.2f y sigue hacia %.2f" % (
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
        print("%s %s %s %s %g acc entrada %.2f stop %.2f obj %.2f | %s%s" % (
            t["date"], t["opened_et"], t["side"], t["symbol"], t["shares"], t["entry"], t["stop"], t["target"], t["status"],
            (" %s %+.2f USD (%+.2fR)" % (t["exit_why"], t["pnl_usd"], t["r_multiple"] or 0)) if t["status"] == "cerrada" else ""))
    print("Resultado realizado: %+.2f USD" % tot)


WATCH = os.path.join(ROOT, "trader/percoco_watch.json")


def cmd_cycle(symbols):
    """One pass over a watchlist: manage open trades, analyze each symbol,
    open what is CONFIRMADO, and report only what changed since the last pass."""
    import io, contextlib, subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    events = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_update()
    out = buf.getvalue().strip()
    if out and not out.startswith("Sin cambios"):
        events.append(out)
    watch = load(WATCH, {"symbols": {}, "last": {}})
    last = watch.get("last", {})
    seen = {}
    for sym in symbols:
        r = subprocess.run([sys.executable, os.path.join(here, "percoco.py"), sym, "--json"],
                           capture_output=True, text=True, cwd=ROOT)
        try:
            doc = json.loads(r.stdout)
        except ValueError:
            events.append("%s: sin datos (%s)" % (sym, (r.stderr or r.stdout).strip()[-120:]))
            continue
        watch["symbols"][sym] = {"as_of_et": doc["as_of_et"], "price": doc["price"], "setups": doc["setups"]}
        for st in doc["setups"]:
            key = "%s|%s|%.2f-%.2f" % (sym, st["lado"], st["zona"][0], st["zona"][1])
            stat = st["estado"].split(" (")[0].split(":")[0]
            seen[key] = stat
            if stat in ("EN ZONA, esperando vela de reacción", "CONFIRMADO") and last.get(key) != stat:
                events.append("%s %s %s: %s | zona %.2f-%.2f entrada %.2f stop %.2f" % (
                    sym, st["lado"], st["tf"], stat, st["zona"][0], st["zona"][1], st["entrada_ce"], st["stop"]))
        for key, stat in last.items():
            if key.startswith(sym + "|") and key not in seen and stat != "INCOMPLETO":
                events.append("%s: setup %s desapareció (invalidado o rellenado)" % (sym, key.split("|", 1)[1]))
        if any(x["estado"].startswith("CONFIRMADO") for x in doc["setups"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_open(sym, doc)
            msg = buf.getvalue().strip()
            blocked = watch.setdefault("blocked", {})
            if msg.startswith("BLOQUEADO"):
                if blocked.get(sym) != msg.split(":")[0]:
                    events.append("%s: %s" % (sym, msg))
                blocked[sym] = msg.split(":")[0]
            elif msg and not msg.startswith("Ya se operó esta zona"):
                events.append(msg)
        time.sleep(0.5)
    watch["last"] = seen
    watch["as_of_et"] = et_now().strftime("%Y-%m-%d %H:%M")
    with open(WATCH, "w") as f:
        json.dump(watch, f, indent=1, default=str)
    open_n = sum(1 for t in load(BOOK, {"trades": []})["trades"] if t["status"] == "abierta")
    print("\n".join(events) if events else "Sin cambios (%s). Operaciones abiertas: %d" % (", ".join(symbols), open_n))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] not in ("open", "update", "show", "cycle"):
        raise SystemExit(__doc__)
    {"open": lambda: cmd_open(a[1].upper()), "update": cmd_update, "show": cmd_show,
     "cycle": lambda: cmd_cycle([x.upper() for x in a[1:]] or ["SPY"])}[a[0]]()
