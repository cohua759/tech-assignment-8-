import asyncpg, os

async def get_db():
    return await asyncpg.connect(
        host="db",
        database=os.getenv("DB_NAME"),
        user="postgres",
        password=os.getenv("DB_PASSWORD")
    )

async def insert_device(mac):
    db = await get_db()
    await db.execute(
        "INSERT INTO devices (mac_address) VALUES ($1) ON CONFLICT DO NOTHING", mac
    )
    await db.close()

async def insert_reading(mac, pixels, thermistor, prediction, confidence):
    db = await get_db()
    row = await db.fetchrow(
        """INSERT INTO readings (mac_address, pixels, thermistor_temp, prediction, confidence)
           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
        mac, pixels, thermistor, prediction, confidence
    )
    await db.close()
    return row["id"]

async def get_readings(device_mac=None):
    db = await get_db()
    if device_mac:
        rows = await db.fetch("SELECT * FROM readings WHERE mac_address=$1", device_mac)
    else:
        rows = await db.fetch("SELECT * FROM readings")
    await db.close()
    return [dict(r) for r in rows]

async def delete_reading(id):
    db = await get_db()
    await db.execute("DELETE FROM readings WHERE id=$1", id)
    await db.close()

async def get_devices():
    db = await get_db()
    rows = await db.fetch("SELECT * FROM devices")
    await db.close()
    return [dict(r) for r in rows]
