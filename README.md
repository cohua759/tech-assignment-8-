# **SUBMISSION LINK MUST BE EXACTLY ONE LINE BELOW THIS** (FAILURE TO DO SO WILL RESULT IN A 0 GRADE)

https://youtube.com/shorts/PIAq4edsTbs?feature=share

## Video submission requirements:
 - Must clearly show the ESP 32 and Sensor working
 - Must clearly show a functioning front-end
 - Must show a webserver container and a database container running
    - Run docker ps in a terminal or show docker desktop.
 - All of the above must be included to receive credit.

# Tech Assignment 7

## Requirements

This week we will be chaning things up a bit!

We will not be giving any starter code for this assignment, we will only provide a set of requrements. Here they are:

# Implementation requirements:
 - A frontend webpage that has the following:
    - A way to see and toggle the live readings from the AMG 8833 sensor:
        - Visualized readings from the sensor
        - Ambient temperature thermistor value
        - Neural network (running on ESP 32) prediction of whether or not there is a person in frame
    - A way to see data readings stored in your database
    - [EXTRA CREDIT] Good looking CSS formatting
 - Docker containers that run the following:
    - An API webserver that has the following:
        - Serves the front-end described above
        - Has an api endpoint that allows to the toggling of ESP 32 readings (get one, send continuous, stop sending continuous)
        - Has API endpoints required to add, read and delete readings from the database
        - Has a table that stores unique ESP 32 entries.
            - (Each ESP 32 has a unique MAC addres, we expect you to associate each reading from the ESP with its MAC addres in the database)
        - [EXTRA CREDIT] Store thermal readings along with the thermister readings. Include normalization relative to thermister reading in your front-end visualization.
        - [EXTRA CREDIT] Infer more information from your data come up with something extra that you can do.
    - A database that has allows all of the above to be implemented
        - [HINT] Use two tables, one table to store readings, one table to store unique ESP 32s. Associate each reading with a MAC address of an ESP that is in the "devices" table, separate from the readings "table"

# Grading Rubric

*You cannot lose more than 100 points. Extra credit will not be available without receiving credit for the underlying points.*

*Extra credit points must be awareded by a TA in person during office hours*


| # | Requirement | Points|
|---|------------|--------|
| | **ESP 32** | |
| 1 | ESP: Working implementation | 30|
| | **Autograder** | |
|2| Autograder tests pass| 70|
| | **EXTRA CREDIT** | |
| 3 | Frontend: [EXTRA CREDIT] Good looking CSS formatting | + 10 |
| 4 | API webserver: [EXTRA CREDIT] Store thermal readings with thermistor readings; normalize in front-end visualization | + 10 |
| 5 | API webserver: [EXTRA CREDIT] Infer more information from your data | + 5 |

---
## Build and Submission Checklist (Read This First)

Use this checklist in order before you submit to Gradescope.

1. Make sure your repo has this exact top-level structure:

   ```
   your-repo/
   ├── esp32/          ← PlatformIO project
   └── server/
       ├── docker-compose.yml
       └── webserver/
           └── ...
   ```

2. Submit your **GitHub repository URL** on Gradescope.
3. Ensure your server is reachable on **port 8000** after `docker compose up`.
4. Verify your `docker-compose.yml` uses environment variables (no hard-coded credentials).
5. Record your video based on the requirements at the top of this README.
6. Use the `esp32` folder reference to find your ESP MAC address.

---

## Autograder Environment

Your submission is automatically tested by an autograder on Gradescope.

The autograder injects a `.env` file into `server/`. Your compose setup must read from env vars. The injected file contains:

```
DB_PASSWORD=...
DB_NAME=ta7db
MQTT_BROKER=broker.emqx.io
MQTT_TOPIC=ece140a/ta7/autograder
```

---

## What Is Graded Automatically (70 points)

- ESP 32, 30 pts is graded manually via video and code submission.
- Webserver + Database, 70 pts are graded by autograder tests below.

### Rubric #5 - Serves frontend (20 pts)

| Test | Points | Requirement |
|------|--------|-------------|
| 5.1 | 7 | `GET /` returns HTTP **200** |
| 5.2 | 7 | Response body contains a `<canvas>` element and at least one of: `thermistor`, `prediction`, `thermal`, `heatmap` |
| ws.1 | 6 | WebSocket connection to `ws://localhost:8000/ws` is accepted |

### Rubric #6 - ESP32 command endpoint (10 pts)

`POST /api/command` with JSON body `{"command": "<cmd>"}`:

| Test | Points | Requirement |
|------|--------|-------------|
| 6.1 | 4 | `"get_one"` returns HTTP **200** |
| 6.2 | 3 | `"start_continuous"` and `"stop"` each return HTTP **200** |
| 6.3 | 3 | Unknown command returns HTTP **4xx** |

### Rubric #7 - Add/read/delete readings (10 pts)

`POST /api/readings` expects:

```json
{
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "pixels": [<64 floats>],
  "thermistor": 24.5,
  "prediction": "PRESENT",
  "confidence": 0.8765
}
```

Response must include `{"id": <integer>}`.

| Test | Points | Requirement |
|------|--------|-------------|
| 7.1 | 2 | `POST /api/readings` returns a non-null numeric `id` |
| 7.2 | 2 | `GET /api/readings` returns a JSON array with HTTP 200 |
| 7.3 | 2 | `GET /api/readings` includes rows previously POSTed |
| 7.4 | 2 | `GET /api/readings?device_mac=AA:BB:CC:DD:EE:FF` returns only rows for that MAC |
| 7.5 | 2 | `DELETE /api/readings/{id}` returns 200 and removes the row |

### Rubric #8 - Devices table (15 pts)

| Test | Points | Requirement |
|------|--------|-------------|
| 8.1 | 6 | `GET /api/devices` returns a JSON array with HTTP 200 |
| 8.2 | 9 | MAC addresses from POSTed readings appear in `GET /api/devices` |

### Rubric #9 - Readings table contents (15 pts)

| Test | Points | Requirement |
|------|--------|-------------|
| 9.1 | 7 | Every reading has `id`, `mac_address`, `thermistor_temp`, `prediction`, `confidence`, `pixels`, and `pixels` has exactly 64 floats |
| 9.2 | 8 | Stored values match POST data (`mac_address`, `thermistor_temp` within +/-0.5 C, `prediction`, and 64 `pixels` values within +/-0.1) |

---

## Required Response Field Names

Autograder checks these exact response keys:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | reading primary key |
| `mac_address` | string | e.g. `"AA:BB:CC:DD:EE:FF"` |
| `thermistor_temp` | float | degrees Celsius |
| `prediction` | string | `"PRESENT"` or `"EMPTY"` (case-insensitive) |
| `confidence` | float | 0.0 to 1.0 |
| `pixels` | list of 64 floats | raw 8x8 thermal values as JSON array |

> **Tip:** Read thermistor on ESP32 via `amg.readThermistor()`. Send it in MQTT payload as `"thermistor"` so the server can store/return it as `thermistor_temp`.
