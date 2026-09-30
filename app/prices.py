"""Market data: live quotes and daily history from public APIs.

Sources (no API keys needed):
  crypto            Binance (fallback: Yahoo Finance "<T>-USD")
  US stocks / ETFs  Yahoo Finance chart API
  CEDEARs, AR stocks, AR bonds (ARS)   data912.com
  Dólar MEP         dolarapi.com (live), argentinadatos.com (history)
"""
import asyncio
import datetime as dt
import logging
import math
import random
import time
from zoneinfo import ZoneInfo

import httpx

from . import db
from .config import BENCHMARKS, FAST_INTERVAL, MOCK_PRICES, SLOW_INTERVAL

log = logging.getLogger("prices")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}
AR_LIVE = {
    "cedear": ["arg_cedears"],
    "accion_ar": ["arg_stocks"],
    "bono_ar": ["arg_bonds", "arg_notes", "arg_corp"],
    "otro_ars": ["arg_bonds", "arg_notes", "arg_corp", "arg_stocks", "arg_cedears"],
}
AR_HIST = {"cedear": "cedears", "accion_ar": "stocks", "bono_ar": "bonds"}
YAHOO_KINDS = {"accion_us", "etf_us", "otro_usd"}
STABLE = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD"}


def today() -> str:
    return dt.datetime.now(TZ).date().isoformat()


class Quote:
    def __init__(self, px: float, chg24: float | None, source: str):
        self.px, self.chg24, self.source = px, chg24, source


def _ok(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def yahoo_symbol(t: str, kind: str) -> str:
    return f"{t}-USD" if kind == "cripto" else t.replace(".", "-")


# ---------------------------------------------------------------- live quotes
async def binance_quote(c: httpx.AsyncClient, t: str) -> Quote | None:
    r = await c.get("https://api.binance.com/api/v3/ticker/24hr", params={"symbol": f"{t}USDT"})
    if r.status_code != 200:
        return None
    j = r.json()
    px = float(j["lastPrice"])
    return Quote(px, float(j["priceChangePercent"]) / 100, "Binance") if _ok(px) else None


async def yahoo_quote(c: httpx.AsyncClient, sym: str) -> Quote | None:
    r = await c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                    params={"range": "1d", "interval": "5m"})
    if r.status_code != 200:
        return None
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return None
    m = res[0]["meta"]
    px = m.get("regularMarketPrice")
    prev = m.get("previousClose") or m.get("chartPreviousClose")
    if not _ok(px):
        return None
    return Quote(float(px), (px / prev - 1) if _ok(prev) else None, "Yahoo Finance")


async def data912_list(c: httpx.AsyncClient, name: str, cache: dict) -> dict:
    if name not in cache:
        cache[name] = {}
        try:
            r = await c.get(f"https://data912.com/live/{name}")
            if r.status_code == 200:
                cache[name] = {row["symbol"].upper(): row for row in r.json() if row.get("symbol")}
        except Exception as e:  # noqa: BLE001
            log.warning("data912 %s: %s", name, e)
    return cache[name]


async def ar_quote(c, t: str, kind: str, cache: dict) -> Quote | None:
    for name in AR_LIVE.get(kind, []):
        row = (await data912_list(c, name, cache)).get(t)
        if not row:
            continue
        px = row.get("c") or 0
        if not _ok(px):
            bid, ask = row.get("px_bid") or 0, row.get("px_ask") or 0
            px = (bid + ask) / 2 if _ok(bid) and _ok(ask) else 0
        if _ok(px):
            pc = row.get("pct_change")
            return Quote(float(px), pc / 100 if isinstance(pc, (int, float)) else None, "data912 (BYMA)")
    return None


async def mep_quote(c) -> Quote | None:
    r = await c.get("https://dolarapi.com/v1/dolares/bolsa")
    if r.status_code != 200:
        return None
    px = r.json().get("venta")
    return Quote(float(px), None, "dolarapi") if _ok(px) else None


async def get_quote(c, t: str, kind: str, cache: dict) -> Quote | None:
    if MOCK_PRICES:
        return mock_quote(t, kind)
    if kind == "mep":
        return await mep_quote(c)
    if kind == "cripto":
        if t in STABLE:
            return Quote(1.0, 0.0, "Stablecoin")
        return await binance_quote(c, t) or await yahoo_quote(c, yahoo_symbol(t, kind))
    if kind in YAHOO_KINDS:
        return await yahoo_quote(c, yahoo_symbol(t, kind))
    if kind in AR_LIVE:
        return await ar_quote(c, t, kind, cache)
    return None


# ---------------------------------------------------------------- history
def _ms(d: str) -> int:
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


async def binance_history(c, t: str, start: str) -> list[tuple[str, float]]:
    out, cursor, end = [], _ms(start), int(time.time() * 1000)
    while cursor < end:
        r = await c.get("https://api.binance.com/api/v3/klines",
                        params={"symbol": f"{t}USDT", "interval": "1d", "startTime": cursor, "limit": 1000})
        if r.status_code != 200:
            return out
        rows = r.json()
        if not rows:
            break
        for k in rows:
            out.append((dt.datetime.fromtimestamp(k[0] / 1000, dt.timezone.utc).date().isoformat(), float(k[4])))
        cursor = rows[-1][0] + 86_400_000
        if len(rows) < 1000:
            break
    return out


async def yahoo_history(c, sym: str, start: str) -> list[tuple[str, float]]:
    p1 = _ms(start) // 1000
    r = await c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                    params={"period1": p1, "period2": int(time.time()), "interval": "1d"})
    if r.status_code != 200:
        return []
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return []
    ts = res[0].get("timestamp") or []
    closes = ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    off = res[0]["meta"].get("gmtoffset", 0)
    return [(dt.datetime.fromtimestamp(s + off, dt.timezone.utc).date().isoformat(), float(v))
            for s, v in zip(ts, closes) if _ok(v)]


async def data912_history(c, t: str, kind: str) -> list[tuple[str, float]]:
    cat = AR_HIST.get(kind)
    if not cat:
        return []
    r = await c.get(f"https://data912.com/historical/{cat}/{t}")
    if r.status_code != 200:
        return []
    return [(str(row["date"])[:10], float(row["c"])) for row in r.json() if _ok(row.get("c"))]


async def mep_history(c) -> list[tuple[str, float]]:
    r = await c.get("https://api.argentinadatos.com/v1/cotizaciones/dolares/bolsa")
    if r.status_code != 200:
        return []
    return [(row["fecha"][:10], float(row["venta"])) for row in r.json() if _ok(row.get("venta"))]


async def get_history(c, t: str, kind: str, start: str) -> list[tuple[str, float]]:
    if MOCK_PRICES:
        return mock_history(t, kind, start)
    if kind == "mep":
        return [p for p in await mep_history(c) if p[0] >= start]
    if kind == "cripto":
        if t in STABLE:
            return []
        return await binance_history(c, t, start) or await yahoo_history(c, yahoo_symbol(t, kind), start)
    if kind in YAHOO_KINDS:
        return await yahoo_history(c, yahoo_symbol(t, kind), start)
    if kind in AR_HIST:
        return [p for p in await data912_history(c, t, kind) if p[0] >= start]
    return []


# ---------------------------------------------------------------- mock (offline tests)
def _base(t: str) -> float:
    return {"BTC": 83000, "SPY": 764, "MEP": 1548, "ETH": 3100, "SOL": 180, "AL30": 85000}.get(t, 50 + sum(map(ord, t)) % 400)


def mock_quote(t, kind) -> Quote:
    return Quote(_base(t) * (1 + random.uniform(-0.01, 0.01)), random.uniform(-0.04, 0.04), "Simulado")


def mock_history(t, kind, start) -> list[tuple[str, float]]:
    d, end, out, px = dt.date.fromisoformat(start), dt.date.fromisoformat(today()), [], _base(t) * 0.8
    random.seed(t)
    while d < end:
        px *= 1 + random.gauss(0.0008, 0.015)
        out.append((d.isoformat(), round(px, 4)))
        d += dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------- bookkeeping
def tracked() -> list[tuple[str, str]]:
    rows = db.q("SELECT ticker, kind FROM txs GROUP BY ticker")
    seen = {r["ticker"]: r["kind"] for r in rows}
    # MEP always tracked so the form can prefill it for the first ARS operation
    for t, k in BENCHMARKS:
        seen.setdefault(t, k)
    return list(seen.items())


def save_quote(t: str, kind: str, qt: Quote | None, err: str | None = None):
    now = time.time()
    if qt:
        db.run("""INSERT INTO assets(ticker,kind,px,chg24,source,updated,error) VALUES(?,?,?,?,?,?,NULL)
                  ON CONFLICT(ticker) DO UPDATE SET kind=excluded.kind, px=excluded.px, chg24=excluded.chg24,
                  source=excluded.source, updated=excluded.updated, error=NULL""",
               (t, kind, qt.px, qt.chg24, qt.source, now))
        db.run("INSERT OR REPLACE INTO points(ticker,date,px) VALUES(?,?,?)", (t, today(), qt.px))
    else:
        db.run("""INSERT INTO assets(ticker,kind,error) VALUES(?,?,?)
                  ON CONFLICT(ticker) DO UPDATE SET kind=excluded.kind, error=excluded.error""",
               (t, kind, err or "Sin cotización"))


def earliest_needed(t: str) -> str | None:
    if t in ("SPY", "BTC", "MEP"):
        r = db.one("SELECT MIN(date) d FROM txs")
    else:
        r = db.one("SELECT MIN(date) d FROM txs WHERE ticker=?", (t,))
    return r["d"] if r and r["d"] else None


async def backfill(c, t: str, kind: str):
    start = earliest_needed(t)
    if not start:
        return
    a = db.one("SELECT hist_from, hist_try FROM assets WHERE ticker=?", (t,))
    if a and a["hist_from"] and a["hist_from"] <= start:
        return
    if a and a["hist_try"] and time.time() - a["hist_try"] < 6 * 3600:
        return  # tried recently without covering `start`; retry in a few hours
    from_date = (dt.date.fromisoformat(start) - dt.timedelta(days=7)).isoformat()
    try:
        pts = await get_history(c, t, kind, from_date)
    except Exception as e:  # noqa: BLE001
        log.warning("history %s: %s", t, e)
        pts = []
    td = today()
    pts = [(t, d, px) for d, px in pts if d < td]
    if pts:
        db.many("INSERT OR REPLACE INTO points(ticker,date,px) VALUES(?,?,?)", pts)
        db.run("INSERT INTO assets(ticker,kind,hist_from,hist_try) VALUES(?,?,?,?) "
               "ON CONFLICT(ticker) DO UPDATE SET hist_from=excluded.hist_from, hist_try=excluded.hist_try",
               (t, kind, start, time.time()))
        log.info("history %s: %d days from %s", t, len(pts), from_date)
    else:
        db.run("INSERT INTO assets(ticker,kind,hist_try) VALUES(?,?,?) "
               "ON CONFLICT(ticker) DO UPDATE SET hist_try=excluded.hist_try", (t, kind, time.time()))


async def refresh(tickers: list[tuple[str, str]], with_history: bool):
    cache: dict = {}
    async with httpx.AsyncClient(timeout=12, headers=HEADERS, follow_redirects=True) as c:
        for t, kind in tickers:
            try:
                qt = await get_quote(c, t, kind, cache)
                save_quote(t, kind, qt)
            except Exception as e:  # noqa: BLE001
                log.warning("quote %s: %s", t, e)
                save_quote(t, kind, None, "Error al consultar")
            if with_history:
                try:
                    await backfill(c, t, kind)
                except Exception as e:  # noqa: BLE001
                    log.warning("backfill %s: %s", t, e)


async def quote_now(t: str, kind: str) -> dict | None:
    """On-demand quote (used when someone types a ticker in the form)."""
    await refresh([(t, kind)], with_history=False)
    a = db.one("SELECT px, chg24, source, updated, error FROM assets WHERE ticker=?", (t,))
    return dict(a) if a else None


async def backfill_now(t: str, kind: str):
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as c:
        # benchmarks may need an earlier start after a new old-dated operation
        for tt, kk in {(t, kind), *[(b, k) for b, k in BENCHMARKS]}:
            if tt == "MEP" and not db.one("SELECT 1 FROM txs WHERE kind IN ('cedear','accion_ar','bono_ar','otro_ars') LIMIT 1"):
                continue
            db.run("UPDATE assets SET hist_try=NULL WHERE ticker=?", (tt,))
            await backfill(c, tt, kk)


async def loop():
    last_slow = 0.0
    while True:
        started = time.time()
        slow = started - last_slow >= SLOW_INTERVAL
        try:
            all_t = tracked()
            todo = all_t if slow else [x for x in all_t if x[1] == "cripto"]
            await refresh(todo, with_history=slow)
            if slow:
                last_slow = started
        except Exception as e:  # noqa: BLE001
            log.exception("price loop: %s", e)
        await asyncio.sleep(max(2, FAST_INTERVAL - (time.time() - started)))
