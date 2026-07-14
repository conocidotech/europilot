import json
import sqlite3
import time
import base64
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Depends, Request, Form
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from nacl.signing import SigningKey
from itsdangerous import URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware
import hashlib
import secrets
import httpx

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "flags.db"
PRIVATE_KEY_PATH = APP_DIR / "keys" / "private.key"
PUBLIC_KEY_PATH = APP_DIR / "keys" / "public.key"
ADMIN_TOKEN_PATH = APP_DIR / "keys" / "admin.token"
AZURE_SSO_PATH = APP_DIR / "keys" / "azure_sso.json"

app = FastAPI(title="Europilot", version="2.3.0")


def _load_azure_sso() -> dict:
    if AZURE_SSO_PATH.exists():
        cfg = json.loads(AZURE_SSO_PATH.read_text())
        if cfg.get("tenant_id") and cfg.get("client_id"):
            return cfg
    return {}


AZURE_SSO = _load_azure_sso()
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

_signing_key: SigningKey | None = None


def get_signing_key() -> SigningKey:
    global _signing_key
    if _signing_key is None:
        _signing_key = SigningKey(PRIVATE_KEY_PATH.read_bytes())
    return _signing_key


def get_public_key_b64() -> str:
    return base64.b64encode(PUBLIC_KEY_PATH.read_bytes()).decode()


def get_admin_token() -> str:
    return ADMIN_TOKEN_PATH.read_text().strip()


SECRET_KEY = get_admin_token()
serializer = URLSafeTimedSerializer(SECRET_KEY)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS flags (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kill_switch (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active INTEGER DEFAULT 0,
                reason TEXT DEFAULT '',
                activated_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL DEFAULT '',
                salt TEXT NOT NULL DEFAULT '',
                email TEXT DEFAULT '',
                is_admin INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                dongle_id TEXT PRIMARY KEY,
                owner_id INTEGER REFERENCES users(id),
                device_name TEXT DEFAULT 'comma device',
                car_model TEXT DEFAULT '',
                software_version TEXT DEFAULT '',
                ip_address TEXT DEFAULT '',
                last_seen REAL,
                registered_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dongle_id TEXT NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL,
                distance_km REAL DEFAULT 0,
                duration_sec INTEGER DEFAULT 0,
                engaged_pct REAL DEFAULT 0,
                start_lat REAL,
                start_lng REAL,
                end_lat REAL,
                end_lng REAL,
                route_json TEXT DEFAULT '[]',
                max_speed_kmh REAL DEFAULT 0,
                avg_speed_kmh REAL DEFAULT 0,
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                dongle_id TEXT PRIMARY KEY,
                frame_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO kill_switch (id, active) VALUES (1, 0)"
        )
        conn.commit()
        _seed_admin(conn)


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def _seed_admin(conn):
    existing = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if existing:
        return
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(get_admin_token(), salt)
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
        ("admin", pw_hash, salt, time.time()),
    )
    conn.commit()


def require_admin(authorization: str = Header(None)):
    if not authorization or authorization != f"Bearer {get_admin_token()}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_session(request: Request) -> Optional[dict]:
    return request.session.get("user")


def flash(request: Request, category: str, message: str):
    if "flashes" not in request.session:
        request.session["flashes"] = []
    request.session["flashes"].append([category, message])


def get_flashed_messages(request: Request) -> list:
    msgs = request.session.pop("flashes", [])
    return msgs


def web_context(request: Request, page: str = "", **kwargs):
    return {
        "session": get_session(request),
        "page": page,
        "get_flashed_messages": lambda: get_flashed_messages(request),
        **kwargs,
    }


def render(request: Request, template: str, page: str = "", **kwargs):
    return templates.TemplateResponse(request, template, context=web_context(request, page, **kwargs))


# --- Public API endpoints (device-facing) ---


@app.get("/flags")
def api_get_flags():
    with get_db() as conn:
        kill = conn.execute("SELECT active, reason FROM kill_switch WHERE id=1").fetchone()
        rows = conn.execute(
            "SELECT key, value FROM flags WHERE enabled=1 ORDER BY key"
        ).fetchall()

    flags = {row["key"]: row["value"] for row in rows}
    payload = {
        "flags": flags,
        "kill_switch": {"active": bool(kill["active"]), "reason": kill["reason"] or ""},
        "timestamp": int(time.time()),
    }

    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = get_signing_key().sign(payload_bytes).signature

    return JSONResponse(
        content={
            **payload,
            "signature": base64.b64encode(signature).decode(),
            "public_key": get_public_key_b64(),
        }
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "europilot-api", "version": app.version}


# --- Admin API endpoints (Bearer token) ---


class FlagIn(BaseModel):
    key: str
    value: str
    description: str = ""
    enabled: bool = True


class KillSwitchIn(BaseModel):
    active: bool
    reason: str = ""


class DeviceHeartbeat(BaseModel):
    dongle_id: str
    device_name: str = "comma device"
    car_model: str = ""
    software_version: str = ""


class TelemetryObject(BaseModel):
    x: float          # metres, device frame: forward
    y: float          # metres, device frame: left
    v: float | None = None
    prob: float | None = None


class TelemetryIn(BaseModel):
    dongle_id: str
    lat: float
    lon: float
    bearing: float | None = None
    speed_mps: float | None = None
    leads: list[TelemetryObject] = []
    tracks: list[TelemetryObject] = []


class RideIn(BaseModel):
    dongle_id: str
    started_at: float
    ended_at: float | None = None
    distance_km: float = 0
    duration_sec: int = 0
    engaged_pct: float = 0
    start_lat: float | None = None
    start_lng: float | None = None
    end_lat: float | None = None
    end_lng: float | None = None
    route: list[list[float]] = []
    max_speed_kmh: float = 0
    avg_speed_kmh: float = 0


@app.get("/api/admin/flags", dependencies=[Depends(require_admin)])
def admin_api_list_flags():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM flags ORDER BY key").fetchall()
    return [dict(r) for r in rows]


@app.put("/api/admin/flags", dependencies=[Depends(require_admin)])
def admin_api_set_flag(flag: FlagIn):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO flags (key, value, description, enabled, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,
                 description=excluded.description,
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (flag.key, flag.value, flag.description, int(flag.enabled), time.time()),
        )
        conn.commit()
    return {"ok": True, "key": flag.key}


@app.delete("/api/admin/flags/{key}", dependencies=[Depends(require_admin)])
def admin_api_delete_flag(key: str):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM flags WHERE key=?", (key,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Flag not found")
    return {"ok": True, "deleted": key}


@app.get("/api/admin/kill-switch", dependencies=[Depends(require_admin)])
def admin_api_get_kill_switch():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM kill_switch WHERE id=1").fetchone()
    return dict(row)


@app.put("/api/admin/kill-switch", dependencies=[Depends(require_admin)])
def admin_api_set_kill_switch(ks: KillSwitchIn):
    with get_db() as conn:
        conn.execute(
            "UPDATE kill_switch SET active=?, reason=?, activated_at=? WHERE id=1",
            (int(ks.active), ks.reason, time.time() if ks.active else None),
        )
        conn.commit()
    return {"ok": True, "active": ks.active, "reason": ks.reason}


# --- Device API endpoints ---


@app.post("/api/devices/heartbeat")
def device_heartbeat(hb: DeviceHeartbeat, request: Request):
    ip = request.client.host if request.client else ""
    now = time.time()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO devices (dongle_id, device_name, car_model, software_version, ip_address, last_seen, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(dongle_id) DO UPDATE SET
                 device_name=excluded.device_name,
                 car_model=excluded.car_model,
                 software_version=excluded.software_version,
                 ip_address=excluded.ip_address,
                 last_seen=excluded.last_seen""",
            (hb.dongle_id, hb.device_name, hb.car_model, hb.software_version, ip, now, now),
        )
        conn.commit()
    return {"ok": True, "dongle_id": hb.dongle_id}


@app.get("/api/devices", dependencies=[Depends(require_admin)])
def api_list_devices():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    now = time.time()
    return [
        {**dict(r), "online": (now - (r["last_seen"] or 0)) < 300}
        for r in rows
    ]


@app.delete("/api/devices/{dongle_id}", dependencies=[Depends(require_admin)])
def api_delete_device(dongle_id: str):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM devices WHERE dongle_id=?", (dongle_id,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Device not found")
    with get_db() as conn:
        conn.execute("DELETE FROM telemetry WHERE dongle_id=?", (dongle_id,))
        conn.commit()
    return {"ok": True, "deleted": dongle_id}


# --- Live telemetry (bird's-eye view) ---
#
# Latest frame per device, stored in SQLite (not in-process memory) because
# uvicorn runs multiple workers -- an in-memory dict would let a POST land on one
# worker and a GET miss it on another. It is a live view, not history: exactly
# one row per device, overwritten each frame, and treated as absent once stale.

_TELEMETRY_TTL_S = 10.0          # a frame older than this is "no live data"
_TELEMETRY_MAX_OBJECTS = 64      # defensive cap regardless of what the device sent


def _user_owns_device(request: Request, dongle_id: str) -> bool:
    user = get_session(request)
    if not user:
        return False
    with get_db() as conn:
        row = conn.execute("SELECT owner_id FROM devices WHERE dongle_id=?", (dongle_id,)).fetchone()
    return bool(row) and row["owner_id"] == user["user_id"]


@app.post("/api/devices/telemetry")
def device_telemetry(frame: TelemetryIn, request: Request):
    now = time.time()
    payload = {
        "lat": frame.lat, "lon": frame.lon,
        "bearing": frame.bearing, "speed_mps": frame.speed_mps,
        "leads": [o.model_dump(exclude_none=True) for o in frame.leads[:_TELEMETRY_MAX_OBJECTS]],
        "tracks": [o.model_dump(exclude_none=True) for o in frame.tracks[:_TELEMETRY_MAX_OBJECTS]],
    }
    with get_db() as conn:
        # Only accept telemetry for a device we already know (one that has sent a
        # heartbeat / been registered). This keeps the telemetry table a strict
        # subset of devices and stops an unknown dongle_id from creating orphan
        # rows. Device-level auth (QR pairing, EUROPILOT-42) is the fuller fix.
        known = conn.execute("SELECT 1 FROM devices WHERE dongle_id=?", (frame.dongle_id,)).fetchone()
        if not known:
            raise HTTPException(status_code=404, detail="Unknown device; send a heartbeat first")
        conn.execute(
            """INSERT INTO telemetry (dongle_id, frame_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(dongle_id) DO UPDATE SET
                 frame_json=excluded.frame_json, updated_at=excluded.updated_at""",
            (frame.dongle_id, json.dumps(payload), now),
        )
        # a telemetry frame also proves the device is alive -> keep last_seen fresh
        conn.execute("UPDATE devices SET last_seen=? WHERE dongle_id=?", (now, frame.dongle_id))
        conn.commit()
    return {"ok": True}


@app.get("/api/devices/{dongle_id}/telemetry")
def get_device_telemetry(dongle_id: str, request: Request):
    user = get_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not (user.get("is_admin") or _user_owns_device(request, dongle_id)):
        raise HTTPException(status_code=403, detail="Not your device")
    with get_db() as conn:
        row = conn.execute(
            "SELECT frame_json, updated_at FROM telemetry WHERE dongle_id=?", (dongle_id,)
        ).fetchone()
    if not row or (time.time() - row["updated_at"]) > _TELEMETRY_TTL_S:
        return {"live": False}
    return {"live": True, "age_s": round(time.time() - row["updated_at"], 2),
            **json.loads(row["frame_json"])}


# --- Rides API endpoints ---


@app.post("/api/rides")
def api_create_ride(ride: RideIn):
    now = time.time()
    route_json = json.dumps(ride.route)
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO rides (dongle_id, started_at, ended_at, distance_km, duration_sec,
                engaged_pct, start_lat, start_lng, end_lat, end_lng, route_json,
                max_speed_kmh, avg_speed_kmh, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ride.dongle_id, ride.started_at, ride.ended_at, ride.distance_km,
             ride.duration_sec, ride.engaged_pct, ride.start_lat, ride.start_lng,
             ride.end_lat, ride.end_lng, route_json, ride.max_speed_kmh,
             ride.avg_speed_kmh, now),
        )
        conn.commit()
        ride_id = cur.lastrowid
    return {"ok": True, "id": ride_id}


@app.get("/api/rides")
def api_list_rides(dongle_id: str | None = None, limit: int = 50):
    with get_db() as conn:
        if dongle_id:
            rows = conn.execute(
                "SELECT * FROM rides WHERE dongle_id=? ORDER BY started_at DESC LIMIT ?",
                (dongle_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rides ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/rides/{ride_id}")
def api_get_ride(ride_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ride not found")
    return dict(row)


# --- Web routes ---


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    if get_session(request):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_session(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "login.html", error=None, sso_enabled=bool(AZURE_SSO))


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip().lower()
    if not username or not password:
        return render(request, "login.html", error="Vul gebruikersnaam en wachtwoord in.", sso_enabled=bool(AZURE_SSO))

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    if not user or not user["salt"] or _hash_password(password, user["salt"]) != user["password_hash"]:
        return render(request, "login.html", error="Ongeldige gebruikersnaam of wachtwoord.", sso_enabled=bool(AZURE_SSO))

    request.session["user"] = {
        "user_id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "logged_in_at": int(time.time()),
    }

    return RedirectResponse("/dashboard", status_code=303)


@app.post("/logout")
@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --- Microsoft 365 SSO ---


@app.get("/auth/microsoft/redirect")
def microsoft_sso_redirect(request: Request):
    if not AZURE_SSO:
        flash(request, "error", "Microsoft SSO is niet geconfigureerd.")
        return RedirectResponse("/login", status_code=303)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    request.session["azure_sso_state"] = state
    request.session["azure_sso_nonce"] = nonce

    params = {
        "client_id": AZURE_SSO["client_id"],
        "response_type": "code",
        "redirect_uri": AZURE_SSO["redirect_uri"],
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    authorize_url = (
        f"https://login.microsoftonline.com/{AZURE_SSO['tenant_id']}/oauth2/v2.0/authorize?"
        + "&".join(f"{k}={v}" for k, v in params.items())
    )
    return RedirectResponse(authorize_url)


@app.get("/auth/microsoft/callback")
async def microsoft_sso_callback(request: Request):
    if not AZURE_SSO:
        flash(request, "error", "Microsoft SSO is niet geconfigureerd.")
        return RedirectResponse("/login", status_code=303)

    expected_state = request.session.pop("azure_sso_state", None)
    expected_nonce = request.session.pop("azure_sso_nonce", None)

    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", error)
        flash(request, "error", f"Microsoft heeft de aanvraag afgewezen: {desc}")
        return RedirectResponse("/login", status_code=303)

    state = request.query_params.get("state", "")
    if not expected_state or not secrets.compare_digest(expected_state, state):
        flash(request, "error", "Ongeldige state — herstart de inlog.")
        return RedirectResponse("/login", status_code=303)

    code = request.query_params.get("code", "")
    if not code:
        flash(request, "error", "Geen authorization code ontvangen van Microsoft.")
        return RedirectResponse("/login", status_code=303)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"https://login.microsoftonline.com/{AZURE_SSO['tenant_id']}/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": AZURE_SSO["client_id"],
                "client_secret": AZURE_SSO["client_secret"],
                "code": code,
                "redirect_uri": AZURE_SSO["redirect_uri"],
                "scope": "openid profile email",
            },
        )

    if token_resp.status_code != 200:
        flash(request, "error", "Token-uitwisseling met Microsoft mislukt.")
        return RedirectResponse("/login", status_code=303)

    id_token = token_resp.json().get("id_token", "")
    if not id_token:
        flash(request, "error", "Geen ID token in respons.")
        return RedirectResponse("/login", status_code=303)

    parts = id_token.split(".")
    if len(parts) < 2:
        flash(request, "error", "ID token ongeldig formaat.")
        return RedirectResponse("/login", status_code=303)

    padding = 4 - len(parts[1]) % 4
    payload = base64.urlsafe_b64decode(parts[1] + "=" * padding)
    claims = json.loads(payload)

    if claims.get("aud") != AZURE_SSO["client_id"]:
        flash(request, "error", "ID token audience klopt niet.")
        return RedirectResponse("/login", status_code=303)

    if claims.get("tid") != AZURE_SSO["tenant_id"]:
        flash(request, "error", "ID token tenant klopt niet.")
        return RedirectResponse("/login", status_code=303)

    if expected_nonce and claims.get("nonce") != expected_nonce:
        flash(request, "error", "ID token nonce klopt niet.")
        return RedirectResponse("/login", status_code=303)

    if claims.get("exp", 0) < time.time():
        flash(request, "error", "ID token is verlopen.")
        return RedirectResponse("/login", status_code=303)

    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").lower()
    name = claims.get("name", email)

    if not email:
        flash(request, "error", "Geen e-mailadres in Microsoft account gevonden.")
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            user = conn.execute("SELECT * FROM users WHERE username=?", (email.split("@")[0],)).fetchone()

        if not user:
            username = email.split("@")[0]
            base_username = username
            counter = 1
            while conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                username = f"{base_username}{counter}"
                counter += 1

            conn.execute(
                "INSERT INTO users (username, email, password_hash, salt, is_admin, created_at) VALUES (?, ?, '', '', 0, ?)",
                (username, email, time.time()),
            )
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        elif not user["email"]:
            conn.execute("UPDATE users SET email=? WHERE id=?", (email, user["id"]))
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()

    request.session["user"] = {
        "user_id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "logged_in_at": int(time.time()),
        "email": email,
        "sso": True,
    }

    flash(request, "success", f"Welkom {name}! Ingelogd via Microsoft 365.")
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    user = get_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        flags = [dict(r) for r in conn.execute("SELECT * FROM flags ORDER BY key").fetchall()]
        kill = dict(conn.execute("SELECT * FROM kill_switch WHERE id=1").fetchone())

    flags_enabled = sum(1 for f in flags if f["enabled"])

    return render(request, "dashboard.html", page="dashboard",
        flags=flags, kill_switch=kill,
        flags_enabled=flags_enabled, flags_total=len(flags),
    )


# --- Admin web pages and actions ---


@app.get("/admin/flags", response_class=HTMLResponse)
def admin_flags_page(request: Request):
    user = get_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not user.get("is_admin"):
        flash(request, "error", "Alleen admins hebben toegang tot deze pagina.")
        return RedirectResponse("/dashboard", status_code=303)

    with get_db() as conn:
        flags = [dict(r) for r in conn.execute("SELECT * FROM flags ORDER BY key").fetchall()]
        kill = dict(conn.execute("SELECT * FROM kill_switch WHERE id=1").fetchone())

    return render(request, "admin.html", page="admin", flags=flags, kill_switch=kill)


@app.post("/admin/flags/create")
def admin_create_flag(request: Request, key: str = Form(...), value: str = Form(...), description: str = Form("")):
    user = get_session(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        conn.execute(
            """INSERT INTO flags (key, value, description, enabled, updated_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value, description=excluded.description, updated_at=excluded.updated_at""",
            (key.strip(), value.strip(), description.strip(), time.time()),
        )
        conn.commit()

    flash(request, "success", f"Flag aangemaakt: {key}")
    return RedirectResponse("/admin/flags", status_code=303)


@app.post("/admin/flags/{key}/toggle")
def admin_toggle_flag(request: Request, key: str):
    user = get_session(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        row = conn.execute("SELECT enabled FROM flags WHERE key=?", (key,)).fetchone()
        if row:
            new_val = 0 if row["enabled"] else 1
            conn.execute("UPDATE flags SET enabled=?, updated_at=? WHERE key=?", (new_val, time.time(), key))
            conn.commit()

    return RedirectResponse("/admin/flags", status_code=303)


@app.post("/admin/flags/{key}/update")
def admin_update_flag_value(request: Request, key: str, value: str = Form(...)):
    user = get_session(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        conn.execute("UPDATE flags SET value=?, updated_at=? WHERE key=?", (value.strip(), time.time(), key))
        conn.commit()

    flash(request, "success", f"Flag bijgewerkt: {key}")
    return RedirectResponse("/admin/flags", status_code=303)


@app.post("/admin/flags/{key}/delete")
def admin_delete_flag_web(request: Request, key: str):
    user = get_session(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        conn.execute("DELETE FROM flags WHERE key=?", (key,))
        conn.commit()

    flash(request, "success", f"Flag verwijderd: {key}")
    return RedirectResponse("/admin/flags", status_code=303)


@app.post("/admin/kill-switch/toggle")
def admin_toggle_kill_switch(request: Request, reason: str = Form("")):
    user = get_session(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        current = conn.execute("SELECT active FROM kill_switch WHERE id=1").fetchone()
        new_active = 0 if current["active"] else 1
        conn.execute(
            "UPDATE kill_switch SET active=?, reason=?, activated_at=? WHERE id=1",
            (new_active, reason.strip(), time.time() if new_active else None),
        )
        conn.commit()

    status = "geactiveerd" if new_active else "gedeactiveerd"
    cat = "error" if new_active else "success"
    flash(request, cat, f"Kill switch {status}.")
    return RedirectResponse("/admin/flags", status_code=303)


# --- Device web pages ---


@app.get("/devices", response_class=HTMLResponse)
def devices_page(request: Request):
    user = get_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    now = time.time()
    with get_db() as conn:
        if user.get("is_admin"):
            rows = conn.execute("SELECT d.*, u.username as owner FROM devices d LEFT JOIN users u ON d.owner_id=u.id ORDER BY d.last_seen DESC").fetchall()
        else:
            rows = conn.execute("SELECT d.*, u.username as owner FROM devices d LEFT JOIN users u ON d.owner_id=u.id WHERE d.owner_id=? ORDER BY d.last_seen DESC", (user["user_id"],)).fetchall()

    devices = []
    for r in rows:
        d = dict(r)
        last = d.get("last_seen") or 0
        d["online"] = (now - last) < 300
        if last > 0:
            ago = int(now - last)
            if ago < 60:
                d["last_seen_fmt"] = f"{ago}s geleden"
            elif ago < 3600:
                d["last_seen_fmt"] = f"{ago // 60}m geleden"
            elif ago < 86400:
                d["last_seen_fmt"] = f"{ago // 3600}u geleden"
            else:
                d["last_seen_fmt"] = f"{ago // 86400}d geleden"
        else:
            d["last_seen_fmt"] = "Nooit"
        devices.append(d)

    return render(request, "devices.html", page="devices", devices=devices)


@app.get("/devices/{dongle_id}/live", response_class=HTMLResponse)
def device_live_page(request: Request, dongle_id: str):
    user = get_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not (user.get("is_admin") or _user_owns_device(request, dongle_id)):
        flash(request, "error", "Dit device is niet van jou.")
        return RedirectResponse("/devices", status_code=303)
    with get_db() as conn:
        row = conn.execute("SELECT device_name, car_model FROM devices WHERE dongle_id=?", (dongle_id,)).fetchone()
    name = (row["device_name"] if row else "") or dongle_id
    return render(request, "device_live.html", page="devices",
                  dongle_id=dongle_id, device_name=name,
                  car_model=(row["car_model"] if row else "") or "")


@app.post("/devices/register")
def register_device(request: Request, dongle_id: str = Form(...), device_name: str = Form("comma device"), car_model: str = Form("")):
    user = get_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    dongle_id = dongle_id.strip()
    if len(dongle_id) < 3:
        flash(request, "error", "Dongle ID moet minimaal 3 tekens zijn.")
        return RedirectResponse("/devices", status_code=303)

    with get_db() as conn:
        existing = conn.execute("SELECT owner_id FROM devices WHERE dongle_id=?", (dongle_id,)).fetchone()
        if existing and existing["owner_id"] and existing["owner_id"] != user["user_id"]:
            flash(request, "error", "Dit device is al gekoppeld aan een andere gebruiker.")
            return RedirectResponse("/devices", status_code=303)

        conn.execute(
            """INSERT INTO devices (dongle_id, owner_id, device_name, car_model, registered_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(dongle_id) DO UPDATE SET
                 owner_id=excluded.owner_id, device_name=excluded.device_name, car_model=excluded.car_model""",
            (dongle_id, user["user_id"], device_name.strip(), car_model.strip(), time.time()),
        )
        conn.commit()

    flash(request, "success", f"Device {dongle_id} geregistreerd.")
    return RedirectResponse("/devices", status_code=303)


@app.post("/devices/{dongle_id}/delete")
def delete_device_web(request: Request, dongle_id: str):
    user = get_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        if user.get("is_admin"):
            conn.execute("DELETE FROM devices WHERE dongle_id=?", (dongle_id,))
        else:
            conn.execute("DELETE FROM devices WHERE dongle_id=? AND owner_id=?", (dongle_id, user["user_id"]))
        conn.commit()

    flash(request, "success", f"Device {dongle_id} verwijderd.")
    return RedirectResponse("/devices", status_code=303)


# --- Rides web pages ---


@app.get("/rides", response_class=HTMLResponse)
def rides_page(request: Request):
    user = get_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    now = time.time()
    with get_db() as conn:
        if user.get("is_admin"):
            rows = conn.execute(
                "SELECT r.*, d.device_name, d.car_model FROM rides r LEFT JOIN devices d ON r.dongle_id=d.dongle_id ORDER BY r.started_at DESC LIMIT 100"
            ).fetchall()
        else:
            device_ids = [
                r["dongle_id"] for r in
                conn.execute("SELECT dongle_id FROM devices WHERE owner_id=?", (user["user_id"],)).fetchall()
            ]
            if device_ids:
                placeholders = ",".join("?" * len(device_ids))
                rows = conn.execute(
                    f"SELECT r.*, d.device_name, d.car_model FROM rides r LEFT JOIN devices d ON r.dongle_id=d.dongle_id WHERE r.dongle_id IN ({placeholders}) ORDER BY r.started_at DESC LIMIT 100",
                    device_ids,
                ).fetchall()
            else:
                rows = []

    rides = []
    for r in rows:
        ride = dict(r)
        started = ride["started_at"]
        import datetime as dt
        ride["date_fmt"] = dt.datetime.fromtimestamp(started).strftime("%d-%m-%Y")
        ride["time_fmt"] = dt.datetime.fromtimestamp(started).strftime("%H:%M")
        dur = ride.get("duration_sec") or 0
        ride["duration_fmt"] = f"{dur // 60}m {dur % 60}s" if dur else "—"
        dist = ride.get("distance_km") or 0
        ride["distance_fmt"] = f"{dist:.1f} km" if dist else "—"
        ride["engaged_fmt"] = f"{ride.get('engaged_pct', 0):.0f}%"
        rides.append(ride)

    total_km = sum(r.get("distance_km") or 0 for r in rides)
    total_rides = len(rides)
    total_engaged = sum(r.get("engaged_pct", 0) for r in rides) / max(total_rides, 1)

    return render(request, "rides.html", page="rides",
        rides=rides, total_km=total_km, total_rides=total_rides,
        avg_engaged=total_engaged,
    )


@app.get("/rides/{ride_id}", response_class=HTMLResponse)
def ride_detail_page(request: Request, ride_id: int):
    user = get_session(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        row = conn.execute(
            "SELECT r.*, d.device_name, d.car_model FROM rides r LEFT JOIN devices d ON r.dongle_id=d.dongle_id WHERE r.id=?",
            (ride_id,),
        ).fetchone()

    if not row:
        flash(request, "error", "Rit niet gevonden.")
        return RedirectResponse("/rides", status_code=303)

    ride = dict(row)
    import datetime as dt
    ride["date_fmt"] = dt.datetime.fromtimestamp(ride["started_at"]).strftime("%d-%m-%Y %H:%M")
    dur = ride.get("duration_sec") or 0
    ride["duration_fmt"] = f"{dur // 60}m {dur % 60}s"
    ride["route"] = json.loads(ride.get("route_json") or "[]")

    return render(request, "ride_detail.html", page="rides", ride=ride)


init_db()
