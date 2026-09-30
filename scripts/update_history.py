#!/usr/bin/env python3
"""Actualiza data/history.json con los cierres diarios de acciones y ETFs de EE.UU.

Baja el histórico del benchmark (VOO, que replica el S&P 500) y de las acciones y ETFs
de EE.UU. que haya en data/portfolio.json. Lo corre la GitHub Action "Actualizar histórico".
Cripto, activos argentinos y el MEP no pasan por acá: los baja la página directo desde el navegador.

Fuentes, en orden: Yahoo Finance, Nasdaq y Stooq (si una falla, prueba la siguiente).
Solo usa la biblioteca estándar de Python.
"""
import csv
import datetime as dt
import http.cookiejar
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO = ROOT / "data" / "portfolio.json"
OUT = ROOT / "data" / "history.json"
US_KINDS = {"accion_us", "etf_us", "otro_usd"}
BENCH = "VOO"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_crumb: str | None = None


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def fetch(url: str, headers: dict | None = None, timeout: int = 25) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    h.update(headers or {})
    with _opener.open(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read()


# ------------------------------------------------------------------ Yahoo Finance
def yahoo_crumb() -> str:
    global _crumb
    if _crumb is None:
        _crumb = ""
        try:
            fetch("https://fc.yahoo.com", timeout=10)
        except Exception:  # noqa: BLE001  (responde 404 pero deja la cookie)
            pass
        try:
            c = fetch("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10).decode().strip()
            if c and "<" not in c and len(c) < 40:
                _crumb = c
        except Exception:  # noqa: BLE001
            pass
    return _crumb


def yahoo(symbol: str, start: dt.date, kind: str) -> dict:
    p1 = int(dt.datetime.combine(start, dt.time(), dt.timezone.utc).timestamp())
    sym = symbol.replace(".", "-")
    crumb = yahoo_crumb()
    last = None
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
               f"?period1={p1}&period2={int(time.time())}&interval=1d"
               + (f"&crumb={urllib.parse.quote(crumb)}" if crumb else ""))
        try:
            j = json.loads(fetch(url, {"Accept": "application/json"}))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            last = e
            time.sleep(2)
            continue
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return {}
        res = res[0]
        ts = res.get("timestamp") or []
        off = (res.get("meta") or {}).get("gmtoffset", 0)
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        out = {}
        for s, c in zip(ts, closes):
            if c is not None and c > 0:
                out[dt.datetime.fromtimestamp(s + off, dt.timezone.utc).date().isoformat()] = round(float(c), 4)
        return out
    raise RuntimeError(f"Yahoo: {last}")


# ------------------------------------------------------------------ Nasdaq
def nasdaq(symbol: str, start: dt.date, kind: str) -> dict:
    ac = "etf" if kind == "etf_us" else "stocks"
    today = dt.date.today()
    out = {}
    for asset in (ac, "stocks" if ac == "etf" else "etf"):
        url = (f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(symbol.replace('.', '/'))}/historical"
               f"?assetclass={asset}&fromdate={start.isoformat()}&todate={today.isoformat()}&limit=9999")
        j = json.loads(fetch(url, {"Accept": "application/json, text/plain, */*",
                                   "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}, timeout=30))
        rows = (((j or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
        for r in rows:
            try:
                d = dt.datetime.strptime(r["date"], "%m/%d/%Y").date().isoformat()
                c = float(str(r["close"]).replace("$", "").replace(",", ""))
            except Exception:  # noqa: BLE001
                continue
            if c > 0:
                out[d] = round(c, 4)
        if out:
            return out
    return out


# ------------------------------------------------------------------ Stooq
def stooq(symbol: str, start: dt.date, kind: str) -> dict:
    today = dt.date.today()
    url = (f"https://stooq.com/q/d/l/?s={urllib.parse.quote(symbol.lower().replace('.', '-'))}.us&i=d"
           f"&d1={start:%Y%m%d}&d2={today:%Y%m%d}")
    text = fetch(url, timeout=30).decode("utf-8", "replace")
    if not text.lower().startswith("date,"):
        return {}
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            c = float(row["Close"])
        except Exception:  # noqa: BLE001
            continue
        if c > 0:
            out[row["Date"]] = round(c, 4)
    return out


SOURCES = [("Yahoo Finance", yahoo), ("Nasdaq", nasdaq), ("Stooq", stooq)]


def history(symbol: str, start: dt.date, kind: str) -> tuple[dict, str | None, list[str]]:
    errors = []
    for name, fn in SOURCES:
        try:
            pts = fn(symbol, start, kind)
            if pts:
                return pts, name, errors
            errors.append(f"{name}: sin datos")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
    return {}, None, errors


def main() -> int:
    txs = load(PORTFOLIO, {}).get("txs") or []
    first_all = min((t["date"] for t in txs if t.get("date")), default=None)
    need: dict[str, tuple[str, str | None]] = {BENCH: ("etf_us", first_all)}
    for t in txs:
        if t.get("kind") not in US_KINDS:
            continue
        tk = str(t.get("ticker", "")).upper().strip()
        d = t.get("date")
        if not tk or not d:
            continue
        kind, start = need.get(tk, (t["kind"], d))
        start = min(d, start) if start else d
        if tk == BENCH:
            start = first_all or start
        need[tk] = (kind, start)

    old = load(OUT, {}).get("tickers") or {}
    out, missing = {}, []
    for tk, (kind, start) in sorted(need.items()):
        s = dt.date.fromisoformat(start) if start else dt.date.today() - dt.timedelta(days=400)
        s -= dt.timedelta(days=7)
        pts, source, errors = history(tk, s, kind)
        for e in errors:
            print(f"  {tk} · {e}")
        if pts:
            out[tk] = {"kind": kind, "source": source, "points": dict(sorted(pts.items()))}
            print(f"{tk}: {len(pts)} cierres desde {min(pts)} ({source})")
        elif tk in old:
            out[tk] = old[tk]
            print(f"{tk}: no se pudo actualizar, conservo los datos anteriores")
        else:
            missing.append(tk)
            print(f"{tk}: sin datos en ninguna fuente")

    if out == old:
        print("Sin cambios")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        payload = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "tickers": out}
        OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        print(f"Guardado {OUT.relative_to(ROOT)} con {len(out)} tickers")
    # Falla solo si no hay ningún dato del benchmark, para que se note en GitHub.
    return 1 if BENCH not in out else 0


if __name__ == "__main__":
    sys.exit(main())
