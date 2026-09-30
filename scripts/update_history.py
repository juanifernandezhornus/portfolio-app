#!/usr/bin/env python3
"""Actualiza data/history.json con los cierres diarios de Yahoo Finance.

Baja el histórico del benchmark (VOO, que replica el S&P 500) y de las acciones y ETFs
de EE.UU. que haya en data/portfolio.json. Lo corre la GitHub Action "Actualizar histórico".
Cripto, activos argentinos y el MEP no pasan por acá: los baja la página directo desde el navegador.
Solo usa la biblioteca estándar de Python.
"""
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO = ROOT / "data" / "portfolio.json"
OUT = ROOT / "data" / "history.json"
US_KINDS = {"accion_us", "etf_us", "otro_usd"}
BENCH = "VOO"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def yahoo(symbol: str, start: dt.date) -> dict:
    p1 = int(dt.datetime.combine(start, dt.time(), dt.timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={p1}&period2={int(time.time())}&interval=1d")
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.load(r)
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                return {}
            res = res[0]
            ts = res.get("timestamp") or []
            off = (res.get("meta") or {}).get("gmtoffset", 0)
            closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            out = {}
            for s, c in zip(ts, closes):
                if c is None or c <= 0:
                    continue
                d = dt.datetime.fromtimestamp(s + off, dt.timezone.utc).date().isoformat()
                out[d] = round(float(c), 4)
            return out
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"no pude bajar {symbol}: {last}")


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
    out, failed = {}, []
    for tk, (kind, start) in sorted(need.items()):
        s = dt.date.fromisoformat(start) if start else dt.date.today() - dt.timedelta(days=400)
        s -= dt.timedelta(days=7)
        try:
            pts = yahoo(tk.replace(".", "-"), s)
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {tk}: {e}", file=sys.stderr)
            pts = {}
            failed.append(tk)
        if pts:
            out[tk] = {"kind": kind, "source": "Yahoo Finance", "points": dict(sorted(pts.items()))}
            print(f"{tk}: {len(pts)} cierres desde {min(pts)}")
        elif tk in old:
            out[tk] = old[tk]
            print(f"{tk}: sin datos nuevos, conservo los anteriores")
        elif tk in failed:
            print(f"{tk}: no se pudo bajar ahora, se reintenta en la próxima corrida")
        else:
            print(f"{tk}: Yahoo no tiene datos para este ticker")

    if out == old:
        print("Sin cambios")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        payload = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "tickers": out}
        OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        print(f"Guardado {OUT.relative_to(ROOT)} con {len(out)} tickers")
    return 1 if failed and len(failed) == len(need) else 0


if __name__ == "__main__":
    sys.exit(main())
