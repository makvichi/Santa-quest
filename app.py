import hashlib, hmac, os, secrets, sqlite3, time
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
DB_PATH = os.getenv("DB_PATH", "quest.db")

app = FastAPI(title="Belgrade Quest")


def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, tg_id TEXT UNIQUE, name TEXT NOT NULL,
          nick TEXT DEFAULT '', role TEXT DEFAULT '', is_admin INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(
          token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS flats(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT DEFAULT '',
          host_id INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS slots(
          id INTEGER PRIMARY KEY, flat_id INTEGER NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
          date TEXT NOT NULL, time TEXT DEFAULT '', title TEXT NOT NULL, description TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS proposals(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          title TEXT NOT NULL, description TEXT DEFAULT '', people TEXT DEFAULT '',
          wish TEXT DEFAULT '', status TEXT DEFAULT 'new', created INTEGER);
        """)


init()


def user_by_token(token):
    if not token:
        return None
    with db() as c:
        return c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                         (token,)).fetchone()


def me(session: str | None = Cookie(None)):
    u = user_by_token(session)
    if not u:
        raise HTTPException(401, "Нужен вход")
    return dict(u)


def admin(u=Depends(me)):
    if not u["is_admin"]:
        raise HTTPException(403, "Только для организаторов")
    return u


def login_user(resp: Response, tg_id: str, name: str):
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if not u:
            first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            cur = c.execute("INSERT INTO users(tg_id,name,is_admin) VALUES(?,?,?)",
                            (tg_id, name, int(first)))
            uid = cur.lastrowid
        else:
            uid = u["id"]
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions VALUES(?,?)", (token, uid))
    resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=60 * 86400)


@app.get("/api/config")
def config():
    return {"bot_username": BOT_USERNAME, "dev_login": not BOT_TOKEN}


@app.get("/auth/telegram")
def tg_callback(request: Request):
    d = dict(request.query_params)
    h = d.pop("hash", "")
    if not BOT_TOKEN:
        raise HTTPException(400, "BOT_TOKEN не задан")
    check = "\n".join(f"{k}={d[k]}" for k in sorted(d))
    key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    if not hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), h):
        raise HTTPException(403, "Подпись Telegram не сошлась")
    if time.time() - int(d.get("auth_date", 0)) > 86400:
        raise HTTPException(403, "Вход устарел")
    name = (d.get("first_name", "") + " " + d.get("last_name", "")).strip() or d.get("username", "Участник")
    resp = RedirectResponse("/")
    login_user(resp, d["id"], name)
    return resp


class DevLogin(BaseModel):
    name: str


@app.post("/api/login/dev")
def dev_login(b: DevLogin, resp: Response):
    if BOT_TOKEN:
        raise HTTPException(403, "dev-вход выключен")
    name = b.name.strip()[:60]
    if not name:
        raise HTTPException(400, "Имя пустое")
    login_user(resp, "dev:" + name.lower(), name)
    return {"ok": True}


@app.post("/api/logout")
def logout(resp: Response, session: str | None = Cookie(None)):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (session,))
    resp.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def get_me(u=Depends(me)):
    return u


class Profile(BaseModel):
    name: str
    nick: str = ""
    role: str = ""


@app.put("/api/me")
def put_me(p: Profile, u=Depends(me)):
    with db() as c:
        c.execute("UPDATE users SET name=?,nick=?,role=? WHERE id=?",
                  (p.name.strip()[:60] or u["name"], p.nick[:40], p.role[:60], u["id"]))
    return {"ok": True}


@app.get("/api/participants")
def participants(u=Depends(me)):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT id,name,nick,role,is_admin FROM users ORDER BY name")]


class Flat(BaseModel):
    name: str
    address: str = ""
    host_id: int | None = None


@app.get("/api/flats")
def flats(u=Depends(me)):
    with db() as c:
        return [dict(r) for r in c.execute(
            "SELECT f.*, u.name AS host FROM flats f LEFT JOIN users u ON u.id=f.host_id ORDER BY f.id")]


@app.post("/api/flats")
def add_flat(f: Flat, u=Depends(admin)):
    with db() as c:
        c.execute("INSERT INTO flats(name,address,host_id) VALUES(?,?,?)", (f.name, f.address, f.host_id))
    return {"ok": True}


@app.delete("/api/flats/{fid}")
def del_flat(fid: int, u=Depends(admin)):
    with db() as c:
        c.execute("DELETE FROM flats WHERE id=?", (fid,))
    return {"ok": True}


class Slot(BaseModel):
    flat_id: int
    date: str
    time: str = ""
    title: str
    description: str = ""


@app.get("/api/schedule")
def schedule(u=Depends(me)):
    with db() as c:
        return [dict(r) for r in c.execute(
            """SELECT s.*, f.name AS flat, f.address, h.name AS host FROM slots s
               JOIN flats f ON f.id=s.flat_id LEFT JOIN users h ON h.id=f.host_id
               ORDER BY s.date, s.time""")]


@app.post("/api/slots")
def add_slot(s: Slot, u=Depends(admin)):
    with db() as c:
        c.execute("INSERT INTO slots(flat_id,date,time,title,description) VALUES(?,?,?,?,?)",
                  (s.flat_id, s.date, s.time, s.title, s.description))
    return {"ok": True}


@app.delete("/api/slots/{sid}")
def del_slot(sid: int, u=Depends(admin)):
    with db() as c:
        c.execute("DELETE FROM slots WHERE id=?", (sid,))
    return {"ok": True}


class Proposal(BaseModel):
    title: str
    description: str = ""
    people: str = ""
    wish: str = ""


@app.get("/api/proposals")
def proposals(u=Depends(me)):
    with db() as c:
        return [dict(r) for r in c.execute(
            """SELECT p.*, u.name AS author FROM proposals p JOIN users u ON u.id=p.user_id
               ORDER BY p.created DESC""")]


@app.post("/api/proposals")
def add_proposal(p: Proposal, u=Depends(me)):
    if not p.title.strip():
        raise HTTPException(400, "Нужно название")
    with db() as c:
        c.execute("INSERT INTO proposals(user_id,title,description,people,wish,created) VALUES(?,?,?,?,?,?)",
                  (u["id"], p.title.strip(), p.description, p.people, p.wish, int(time.time())))
    return {"ok": True}


class Status(BaseModel):
    status: str


@app.patch("/api/proposals/{pid}")
def set_status(pid: int, s: Status, u=Depends(admin)):
    if s.status not in ("new", "accepted", "rejected"):
        raise HTTPException(400, "Плохой статус")
    with db() as c:
        c.execute("UPDATE proposals SET status=? WHERE id=?", (s.status, pid))
    return {"ok": True}


@app.delete("/api/proposals/{pid}")
def del_proposal(pid: int, u=Depends(me)):
    with db() as c:
        row = c.execute("SELECT user_id FROM proposals WHERE id=?", (pid,)).fetchone()
        if not row or (row["user_id"] != u["id"] and not u["is_admin"]):
            raise HTTPException(403, "Нельзя")
        c.execute("DELETE FROM proposals WHERE id=?", (pid,))
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
