from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import asyncio
import aiomysql
import os
import json
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


async def get_db():
    return await aiomysql.connect(
        host=DB_HOST, db=DB_NAME, user=DB_USER, password=DB_PASSWORD, autocommit=True
    )


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
        d.pop("timestamp", None)    # remove non-serializable datetime
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


app = FastAPI(title="TA7 Thermal Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_frontend():
    return FileResponse("static/index.html")


@app.websocket("/ws")
async def websocket_live(websocket: WebSocket):
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
async def send_command(body: dict):
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
async def add_reading(body: dict):
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
async def get_readings(device_mac: str = None):
    return await db_get_readings(device_mac)


@app.delete("/api/readings/{reading_id}")
async def delete_reading(reading_id: int):
    deleted = await db_delete_reading(reading_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Reading not found")
    return {"status": "deleted", "id": reading_id}


@app.get("/api/devices")
async def get_devices():
    return await db_get_devices()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
