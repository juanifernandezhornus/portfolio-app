"""Portafolios — FastAPI app: accounts, portfolios, operations and live market data."""
import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, prices
from .config import BENCHMARKS, INVITE_CODE, SECURE_COOKIES, SESSION_DAYS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
KINDS = {"cripto", "accion_us", "etf_us", "cedear", "accion_ar", "bono_ar", "otro_usd", "otro_ars"}
ARS_KINDS = {"cedear", "accion_ar", "bono_ar", "otro_ars"}
TICKER_RE = re.compile(r"^[A-Z0-9._\-]{1,20}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
USER_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,24}$")
COOKIE = "pf_session"
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    db.purge_sessions()
    task = asyncio.create_task(prices.loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ------------------------------------------------------------------ security helpers
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(got.hex(), h)
    except Exception:  # noqa: BLE001
        return False


_attempts: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request, key: str, limit: int = 10, window: int = 600):
    ip = (request.headers.get("x-forwarded-for") or request.client.host or "?").split(",")[0].strip()
    dq = _attempts[f"{key}:{ip}"]
    now = time.time()
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= limit:
        raise HTTPException(429, "Demasiados intentos. Esperá unos minutos.")
    dq.append(now)


@app.middleware("http")
async def json_only_writes(request: Request, call_next):
    # Mutating API calls must be JSON: browsers can't send that cross-site without CORS, which we never allow.
    if request.url.path.startswith("/api/") and request.method in ("POST", "PATCH", "DELETE"):
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "Formato inválido"}, status_code=415)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def current_user(request: Request) -> dict:
    tok = request.cookies.get(COOKIE)
    if tok:
        r = db.one("SELECT u.id, u.username FROM sessions s JOIN users u ON u.id=s.user_id "
                   "WHERE s.token=? AND s.expires>?", (tok, time.time()))
        if r:
            return dict(r)
    raise HTTPException(401, "Iniciá sesión")


def start_session(resp: Response, user_id: int):
    tok = secrets.token_urlsafe(32)
    db.run("INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)", (tok, user_id, time.time() + SESSION_DAYS * 86400))
    resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=SECURE_COOKIES, path="/")


def new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(6)


# ------------------------------------------------------------------ auth
class Creds(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=200)
    invite: str | None = Field(default=None, max_length=100)


@app.post("/api/register")
def register(body: Creds, request: Request, response: Response):
    rate_limit(request, "register", limit=5)
    if not INVITE_CODE:
        raise HTTPException(403, "El registro está cerrado.")
    if not hmac.compare_digest((body.invite or "").strip(), INVITE_CODE):
        raise HTTPException(403, "El código de invitación no es correcto.")
    u = body.username.strip()
    if not USER_RE.match(u):
        raise HTTPException(400, "El usuario debe tener entre 3 y 24 caracteres: letras, números, punto, guion o guion bajo.")
    if len(body.password) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres.")
    if db.one("SELECT 1 FROM users WHERE username=?", (u,)):
        raise HTTPException(409, "Ese usuario ya existe.")
    db.run("INSERT INTO users(username,pw_hash,created) VALUES(?,?,?)", (u, hash_pw(body.password), time.time()))
    uid = db.one("SELECT id FROM users WHERE username=?", (u,))["id"]
    start_session(response, uid)
    return {"username": u}


@app.post("/api/login")
def login(body: Creds, request: Request, response: Response):
    rate_limit(request, "login")
    r = db.one("SELECT id, username, pw_hash FROM users WHERE username=?", (body.username.strip(),))
    if not r or not check_pw(body.password, r["pw_hash"]):
        raise HTTPException(401, "Usuario o contraseña incorrectos.")
    start_session(response, r["id"])
    return {"username": r["username"]}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    tok = request.cookies.get(COOKIE)
    if tok:
        db.run("DELETE FROM sessions WHERE token=?", (tok,))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"username": user["username"], "registration_open": bool(INVITE_CODE)}


# ------------------------------------------------------------------ market data payload
def price_payload(tickers: set[str], user_id: int | None) -> dict:
    tickers = set(tickers) | {t for t, _ in BENCHMARKS}
    out: dict = {}
    if not tickers:
        return out
    ph = ",".join("?" * len(tickers))
    args = tuple(tickers)
    for r in db.q(f"SELECT ticker,date,px FROM points WHERE ticker IN ({ph}) ORDER BY date", args):
        out.setdefault(r["ticker"], {"ticker": r["ticker"], "points": {}})["points"][r["date"]] = r["px"]
    if user_id is not None:
        for r in db.q(f"SELECT ticker,date,px FROM manual_points WHERE user_id=? AND ticker IN ({ph})", (user_id, *args)):
            out.setdefault(r["ticker"], {"ticker": r["ticker"], "points": {}})["points"][r["date"]] = r["px"]
    for r in db.q(f"SELECT ticker,kind,px,chg24,source,updated,error FROM assets WHERE ticker IN ({ph})", args):
        e = out.setdefault(r["ticker"], {"ticker": r["ticker"], "points": {}})
        if r["kind"] != "mep":
            e["kind"] = r["kind"]
        e["live"] = {"px": r["px"], "chg24": r["chg24"], "source": r["source"], "updated": r["updated"], "error": r["error"]}
    for t in tickers:
        out.setdefault(t, {"ticker": t, "points": {}})
    return out


def tx_row(r) -> dict:
    return {k: r[k] for k in ("id", "pid", "date", "side", "ticker", "kind", "qty", "price", "fx", "fee", "created")}


@app.get("/api/state")
def state(user=Depends(current_user)):
    pfs = [dict(r) for r in db.q("SELECT id,name,public,created FROM portfolios WHERE user_id=? ORDER BY created", (user["id"],))]
    txs = [tx_row(r) for r in db.q("SELECT * FROM txs WHERE user_id=?", (user["id"],))]
    return {"user": user["username"], "portfolios": pfs, "txs": txs,
            "prices": price_payload({t["ticker"] for t in txs}, user["id"]), "server_time": time.time()}


@app.get("/api/ranking")
def ranking(user=Depends(current_user)):
    pfs = [dict(r) for r in db.q("SELECT p.id,p.name,p.created,u.username owner FROM portfolios p "
                                 "JOIN users u ON u.id=p.user_id WHERE p.public=1")]
    ids = [p["id"] for p in pfs]
    txs = []
    if ids:
        txs = [tx_row(r) for r in db.q(f"SELECT * FROM txs WHERE pid IN ({','.join('?' * len(ids))})", ids)]
    return {"portfolios": pfs, "txs": txs, "prices": price_payload({t["ticker"] for t in txs}, None)}


# ------------------------------------------------------------------ portfolios
class PfIn(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    public: bool | None = None


def own_pf(pid: str, user) -> dict:
    r = db.one("SELECT * FROM portfolios WHERE id=? AND user_id=?", (pid, user["id"]))
    if not r:
        raise HTTPException(404, "Portafolio no encontrado")
    return dict(r)


@app.post("/api/portfolios")
def create_pf(body: PfIn, user=Depends(current_user)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Poné un nombre.")
    if db.one("SELECT COUNT(*) n FROM portfolios WHERE user_id=?", (user["id"],))["n"] >= 30:
        raise HTTPException(400, "Llegaste al máximo de 30 portafolios.")
    pid = new_id("p")
    db.run("INSERT INTO portfolios(id,user_id,name,public,created) VALUES(?,?,?,?,?)",
           (pid, user["id"], name, 1 if body.public else 0, time.time()))
    return {"id": pid}


@app.patch("/api/portfolios/{pid}")
def update_pf(pid: str, body: PfIn, user=Depends(current_user)):
    own_pf(pid, user)
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "Poné un nombre.")
        db.run("UPDATE portfolios SET name=? WHERE id=?", (body.name.strip(), pid))
    if body.public is not None:
        db.run("UPDATE portfolios SET public=? WHERE id=?", (1 if body.public else 0, pid))
    return {"ok": True}


@app.delete("/api/portfolios/{pid}")
def delete_pf(pid: str, user=Depends(current_user)):
    own_pf(pid, user)
    db.run("DELETE FROM txs WHERE pid=?", (pid,))
    db.run("DELETE FROM portfolios WHERE id=?", (pid,))
    return {"ok": True}


# ------------------------------------------------------------------ operations
class TxIn(BaseModel):
    pid: str
    date: str
    side: str
    ticker: str
    kind: str
    qty: float = Field(gt=0)
    price: float = Field(gt=0)
    fx: float | None = None
    fee: float = Field(default=0, ge=0)


@app.post("/api/txs")
async def create_tx(body: TxIn, user=Depends(current_user)):
    own_pf(body.pid, user)
    t = body.ticker.strip().upper()
    if not TICKER_RE.match(t) or t == "MEP":
        raise HTTPException(400, "Ticker inválido.")
    if body.kind not in KINDS:
        raise HTTPException(400, "Tipo de activo inválido.")
    if body.side not in ("buy", "sell"):
        raise HTTPException(400, "Operación inválida.")
    if not DATE_RE.match(body.date) or body.date > prices.today() or body.date < "1990-01-01":
        raise HTTPException(400, "Fecha inválida.")
    if body.kind in ARS_KINDS and not (body.fx and body.fx > 0):
        raise HTTPException(400, "Falta el dólar MEP de ese día.")
    if db.one("SELECT COUNT(*) n FROM txs WHERE user_id=?", (user["id"],))["n"] >= 5000:
        raise HTTPException(400, "Llegaste al máximo de operaciones.")
    if body.side == "sell":
        held = db.one("SELECT COALESCE(SUM(CASE WHEN side='buy' THEN qty ELSE -qty END),0) q FROM txs "
                      "WHERE pid=? AND ticker=? AND date<=?", (body.pid, t, body.date))["q"]
        if body.qty > held + 1e-9:
            raise HTTPException(400, f"Solo tenés {held:g} {t} a esa fecha en este portafolio.")
    tid = new_id("t")
    db.run("INSERT INTO txs(id,pid,user_id,date,side,ticker,kind,qty,price,fx,fee,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
           (tid, body.pid, user["id"], body.date, body.side, t, body.kind, body.qty, body.price,
            body.fx if body.kind in ARS_KINDS else None, body.fee, time.time() * 1000))
    # fetch the live quote and history for this ticker in the background
    asyncio.create_task(_after_tx(t, body.kind))
    return {"id": tid}


async def _after_tx(t: str, kind: str):
    try:
        if not db.one("SELECT px FROM assets WHERE ticker=? AND px IS NOT NULL", (t,)):
            await prices.quote_now(t, kind)
        await prices.backfill_now(t, kind)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("tx").warning("after tx %s: %s", t, e)


@app.delete("/api/txs/{tid}")
def delete_tx(tid: str, user=Depends(current_user)):
    n = db.run("DELETE FROM txs WHERE id=? AND user_id=?", (tid, user["id"]))
    if not n:
        raise HTTPException(404, "Operación no encontrada")
    return {"ok": True}


# ------------------------------------------------------------------ quotes & manual prices
@app.get("/api/quote")
async def quote(ticker: str, kind: str, request: Request, user=Depends(current_user)):
    t = ticker.strip().upper()
    if not TICKER_RE.match(t) or kind not in KINDS:
        raise HTTPException(400, "Ticker inválido.")
    rate_limit(request, "quote", limit=60, window=60)
    a = db.one("SELECT px, chg24, source, updated, error FROM assets WHERE ticker=? AND kind=?", (t, kind))
    if a and a["px"] and a["updated"] and time.time() - a["updated"] < 60:
        return dict(a)
    got = await prices.quote_now(t, kind)
    if not got or not got.get("px"):
        raise HTTPException(404, "No encontré cotización para ese ticker. Revisá el ticker y el tipo de activo.")
    return got


class ManualIn(BaseModel):
    ticker: str
    date: str
    px: float = Field(gt=0)


@app.post("/api/manual_prices")
def manual_price(body: ManualIn, user=Depends(current_user)):
    t = body.ticker.strip().upper()
    if not TICKER_RE.match(t) or not DATE_RE.match(body.date) or body.date > prices.today():
        raise HTTPException(400, "Datos inválidos.")
    db.run("INSERT OR REPLACE INTO manual_points(user_id,ticker,date,px) VALUES(?,?,?,?)", (user["id"], t, body.date, body.px))
    return {"ok": True}


# ------------------------------------------------------------------ frontend
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
