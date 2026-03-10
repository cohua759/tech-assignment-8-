from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Cookie, Depends, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
import uvicorn
import asyncio
import aiomysql
import os
import json
import uuid
import bcrypt
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT   = 1883
MQTT_TOPIC  = os.getenv("MQTT_TOPIC")

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_USER     = os.getenv("DB_USER", "root")
DB_NAME     = os.getenv("DB_NAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")

connected_clients: list[WebSocket] = []
latest_frame = {"pixels": [0.0] * 64, "thermistor": 0.0, "prediction": "EMPTY", "confidence": 0.0}
latest_mac   = ""


# ── DB connection ─────────────────────────────────────────────────────────────

async def get_db():
    return await aiomysql.connect(
        host=DB_HOST, db=DB_NAME, user=DB_USER, password=DB_PASSWORD, autocommit=True
    )


# ── Auth dependency ───────────────────────────────────────────────────────────

async def get_current_user(session_token: str | None = Cookie(None)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = await get_db()
    async with db.cursor(aiomysql.DictCursor) as cur:
        await cur.execute(
            "SELECT users.id, users.username FROM sessions "
            "JOIN users ON sessions.user_id = users.id "
            "WHERE sessions.session_token = %s",
            (session_token,),
        )
        user = await cur.fetchone()
    db.close()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


# ── Existing DB helpers (unchanged) ──────────────────────────────────────────

async def db_insert_device(mac: str):
    db = await get_db()
    async with db.cursor() as cur:
        await cur.execute(
            "INSERT IGNORE INTO devices (mac_address) VALUES (%s)", (mac,)
        )
    db.close()


async def db_insert_reading(mac, pixels, thermistor, prediction, confidence) -> int:
    db = await get_db()
    async with db.cursor() as cur:
        await cur.execute(
            """INSERT INTO readings (mac_address, pixels, thermistor_temp, prediction, confidence)
               VALUES (%s, %s, %s, %s, %s)""",
            (mac, json.dumps(pixels), thermistor, prediction.upper(), confidence)
        )
        reading_id = cur.lastrowid
    db.close()
    return reading_id


async def db_get_readings(device_mac: str = None) -> list:
    db = await get_db()
    async with db.cursor(aiomysql.DictCursor) as cur:
        if device_mac:
            await cur.execute("SELECT * FROM readings WHERE mac_address=%s", (device_mac,))
        else:
            await cur.execute("SELECT * FROM readings ORDER BY id")
        rows = await cur.fetchall()
    db.close()
    result = []
    for r in rows:
        d = dict(r)
        d["pixels"] = json.loads(d["pixels"]) if isinstance(d["pixels"], str) else d["pixels"]
        d.pop("timestamp", None)
        result.append(d)
    return result


async def db_delete_reading(reading_id: int) -> bool:
    db = await get_db()
    async with db.cursor() as cur:
        await cur.execute("DELETE FROM readings WHERE id=%s", (reading_id,))
        affected = cur.rowcount
    db.close()
    return affected > 0


async def db_get_devices() -> list:
    db = await get_db()
    async with db.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("SELECT * FROM devices ORDER BY id")
        rows = await cur.fetchall()
    db.close()
    return [{"id": r["id"], "mac_address": r["mac_address"]} for r in rows]


# ── MQTT (unchanged) ──────────────────────────────────────────────────────────

_loop: asyncio.AbstractEventLoop = None


def on_mqtt_message(client, userdata, msg):
    global latest_frame, latest_mac
    try:
        data = json.loads(msg.payload.decode())
        pixels = data.get("pixels", [])
        if not isinstance(pixels, list) or len(pixels) != 64:
            return
        mac        = data.get("mac_address", "")
        thermistor = float(data.get("thermistor", 0.0))
        prediction = str(data.get("prediction", "EMPTY")).upper()
        confidence = float(data.get("confidence", 0.0))
        if not mac:
            return
        latest_mac   = mac
        latest_frame = {
            "mac_address": mac,
            "pixels":      pixels,
            "thermistor":  thermistor,
            "prediction":  prediction,
            "confidence":  confidence
        }
        if _loop:
            asyncio.run_coroutine_threadsafe(
                save_and_broadcast(mac, pixels, thermistor, prediction, confidence), _loop
            )
    except Exception as e:
        print(f"[MQTT] parse error: {e}")


async def save_and_broadcast(mac, pixels, thermistor, prediction, confidence):
    await db_insert_device(mac)
    await db_insert_reading(mac, pixels, thermistor, prediction, confidence)
    for ws in list(connected_clients):
        try:
            await ws.send_json(latest_frame)
        except Exception:
            connected_clients.remove(ws)


mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.on_message = on_mqtt_message


# ── Lifespan (unchanged) ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    global _loop
    _loop = asyncio.get_running_loop()
    for attempt in range(10):
        try:
            db = await get_db()
            db.close()
            print("[DB] connected")
            break
        except Exception as e:
            print(f"[DB] waiting... attempt {attempt + 1}: {e}")
            await asyncio.sleep(2)
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.subscribe(MQTT_TOPIC)
    mqtt_client.loop_start()
    print(f"[MQTT] subscribed to {MQTT_TOPIC}")
    yield
    mqtt_client.loop_stop()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="TA8 Thermal Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Page routes ───────────────────────────────────────────────────────────────

@app.get("/")
async def serve_frontend(session_token: str | None = Cookie(None)):
    if not session_token:
        return RedirectResponse(url="/login", status_code=302)
    db = await get_db()
    async with db.cursor() as cur:
        await cur.execute("SELECT id FROM sessions WHERE session_token = %s", (session_token,))
        row = await cur.fetchone()
    db.close()
    if not row:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse("static/index.html")


@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")


@app.get("/register")
async def register_page():
    return FileResponse("static/register.html")


# ── Auth API endpoints ────────────────────────────────────────────────────────

@app.post("/api/register")
async def register(body: dict):
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db = await get_db()
    try:
        async with db.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                    (username, hashed)
                )
            except aiomysql.IntegrityError:
                raise HTTPException(status_code=409, detail="Username already exists")
    finally:
        db.close()
    return JSONResponse(content={"detail": "User created"}, status_code=201)


@app.post("/api/login")
async def login(body: dict, response: Response):
    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")
    db = await get_db()
    async with db.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = await cur.fetchone()
    db.close()
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = str(uuid.uuid4())
    db = await get_db()
    async with db.cursor() as cur:
        await cur.execute(
            "INSERT INTO sessions (user_id, session_token) VALUES (%s, %s)",
            (user["id"], token)
        )
    db.close()
    response.set_cookie(key="session_token", value=token, httponly=True)
    return {"detail": "Logged in"}


@app.post("/api/logout")
async def logout(response: Response, session_token: str | None = Cookie(None)):
    if session_token:
        db = await get_db()
        async with db.cursor() as cur:
            await cur.execute("DELETE FROM sessions WHERE session_token = %s", (session_token,))
        db.close()
    response.delete_cookie("session_token")
    return {"detail": "Logged out"}


# ── Protected TA7 routes ──────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_live(websocket: WebSocket):
    session_token = websocket.cookies.get("session_token")
    if not session_token:
        await websocket.close(code=1008)
        return
    db = await get_db()
    async with db.cursor() as cur:
        await cur.execute("SELECT id FROM sessions WHERE session_token = %s", (session_token,))
        row = await cur.fetchone()
    db.close()
    if not row:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


@app.post("/api/command")
async def send_command(body: dict, user=Depends(get_current_user)):
    command = body.get("command", "")
    valid = {"get_one", "start_continuous", "stop"}
    if command not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown command: {command}")
    payload = {"command": command}
    if latest_mac:
        payload["target"] = latest_mac
    mqtt_client.publish(MQTT_TOPIC, json.dumps(payload))
    return {"status": "ok", "command": command}


@app.post("/api/readings")
async def add_reading(body: dict, user=Depends(get_current_user)):
    mac        = body.get("mac_address", "")
    pixels     = body.get("pixels", [])
    thermistor = float(body.get("thermistor", 0.0))
    prediction = str(body.get("prediction", "EMPTY")).upper()
    confidence = float(body.get("confidence", 0.0))
    if not mac or len(pixels) != 64:
        raise HTTPException(status_code=400, detail="Invalid payload")
    await db_insert_device(mac)
    reading_id = await db_insert_reading(mac, pixels, thermistor, prediction, confidence)
    return {"id": reading_id}


@app.get("/api/readings")
async def get_readings(device_mac: str = None, user=Depends(get_current_user)):
    return await db_get_readings(device_mac)


@app.delete("/api/readings/{reading_id}")
async def delete_reading(reading_id: int, user=Depends(get_current_user)):
    deleted = await db_delete_reading(reading_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Reading not found")
    return {"status": "deleted", "id": reading_id}


@app.get("/api/devices")
async def get_devices(user=Depends(get_current_user)):
    return await db_get_devices()

#placeholder if im not too lazy to go get a client id (or busy)
@app.get("/api/oauth/login")
async def oauth_login():
    return RedirectResponse("https://phylax.ece140.site/authorize")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)


