# Technical Documentation — Industrial Boiler Monitoring System (v2)

> For developers, operators, and technical reviewers.
> v2: three-valve closed-loop control (feed / water / steam), safety interlocks,
> corrected alarm directions and pressure unit chain.

---

## 1. System Architecture

### 1.1 Layered Stack

```
Data Layer (Sensors / Simulator / ModBus PLC)
│
│ HTTP POST /api/sensor-data  (or ModBus holding registers)
▼
Service Layer (Flask API)
│  ├─ Alarm logic: over-temp / over-pressure / high-level / low-water
│  ├─ Control law: level PID → water valve; pressure PID → feed valve
│  │                steam valve: load-side (auto) or commanded (manual/interlock)
│  └─ Safety interlocks (override PID and manual)
│
├──→ PostgreSQL (persistent storage, 3 valve columns)
├──→ ModBus PLC (valve write-back, optional)
└──→ JSON response with valve commands (closed loop)
│
▼
Presentation Layer (Grafana)
```

### 1.2 Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Protocol | HTTP REST (ModBus optional) | Universal, debug-friendly |
| Loop closure | API returns valve commands | Single source of control truth; simulator executes |
| Pressure control | Feed (fuel) valve, not steam valve | Adding heat raises pressure; opening steam valve lowers it (v1 had positive feedback) |
| Steam valve | Load disturbance in auto mode | Mimics real plant load; system commands it only in manual/interlock |
| Interlocks | Override everything, incl. manual | Safety outranks control |
| PID bias | Steady-state feedforward | No cold-start integral wind-up lag |
| Configuration | Single config.json for both processes | One tuning surface: thresholds, setpoints, PID, ports |

---

## 2. Deployment Guide

### 2.1 Requirements

- OS: Windows 10/11, Linux, or macOS
- Docker Desktop (with Docker Compose)
- Python 3.10+ (for host-run mode)

### 2.2 Startup Sequence

Full Docker:

```bash
docker compose up --build        # postgres + api + grafana
python sensor_simulator.py       # simulator runs on host
```

Host development mode:

```bash
docker compose up postgres grafana
pip install -r requirements.txt
python boiler_api.py
python sensor_simulator.py
```

Environment variables `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` and
`MODBUS_HOST/MODBUS_PORT` override `config.json` (used by the api container).

---

## 3. Database Schema

### 3.1 Table: sensor_logs

| Column | Type | Note |
|---|---|---|
| id | SERIAL PK | Auto-increment |
| created_at | TIMESTAMP | Ingestion time |
| temper | INTEGER | Temperature (°C) |
| high | INTEGER | Water level (%) — legacy column name, kept for Grafana compatibility |
| press | INTEGER | Pressure (**Pa**, v1 mislabelled the ×1e5 scale as Pa) |
| status | VARCHAR(10) | `safe` or `error` |
| is_alarm | BOOLEAN | Any alarm active |
| water_valve | INTEGER | Water inlet valve % |
| feed_valve | INTEGER | Feed (fuel) valve % — **new in v2** |
| steam_valve | INTEGER | Steam valve % |

`init_db()` runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` so old databases
migrate in place without data loss.

### 3.2 Common Queries

```sql
SELECT * FROM sensor_logs ORDER BY created_at DESC LIMIT 10;

SELECT COUNT(*) FROM sensor_logs
WHERE is_alarm = true AND created_at > NOW() - INTERVAL '1 hour';

-- Valve action trends for Grafana
SELECT created_at, feed_valve, water_valve, steam_valve
FROM sensor_logs WHERE created_at > NOW() - INTERVAL '1 hour' ORDER BY created_at;
```

---

## 4. API Specification

### 4.1 `POST /api/sensor-data`

Request:

```json
{
    "temper": 120,
    "high": 50,
    "press": 1000000,
    "valves": {"feed_valve": 43.0, "water_valve": 47.0, "steam_valve": 37.0}
}
```

`valves` (optional): actual field valve positions, logged for Grafana.
Units: `press` in Pa (1.0 MPa = 1000000).

Response 200:

```json
{
    "received": true,
    "status": "safe",
    "is_alarm": false,
    "alarms": [],
    "valves": {"feed_valve": 43.0, "water_valve": 47.0, "steam_valve": null},
    "interlock": null,
    "mode": "auto",
    "timestamp": "2026-07-19T16:00:00"
}
```

- `valves.steam_valve: null` → field keeps its own load setting (auto mode).
- `interlock`: one of `LOW_WATER_TRIP`, `OVERPRESSURE_VENT`,
  `OVERTEMP_CUT`, `HIGH_LEVEL_CUT`, or `null`.
- Errors: 400 invalid params, 500 database failure.

### 4.2 `GET /api/valves` / `POST /api/valves`

```json
POST {"mode": "manual", "feed_valve": 55, "water_valve": 60, "steam_valve": 45}
POST {"mode": "auto"}
```

Manual commands are validated 0–100. **Interlocks still override manual mode.**

### 4.3 `POST /api/modbus-collect`

Full ModBus loop: read sensors → alarm → compute valves → store → write valves.

### 4.4 ModBus Register Map (holding registers)

| Address | Direction | Content | Scaling |
|---|---|---|---|
| 0 | read | Temperature | ×10 (1234 = 123.4 °C) |
| 1 | read | Water level | ×10 (%) |
| 2 | read | Pressure | kPa (1000 = 1.0 MPa) |
| 10 | write | Water valve | ×100 (0–10000) |
| 11 | write | Feed valve | ×100 |
| 12 | write | Steam valve | ×100 |

---

## 5. Alarm & Interlock Logic

### 5.1 Configuration (`config.json`)

```json
"alarm": {"TEMP_HIGH": 160, "PRESS_HIGH": 1500000, "LEVEL_HIGH": 90, "LEVEL_LOW": 25}
```

### 5.2 Interlock Table (priority over PID and manual)

| Condition | feed_valve | water_valve | steam_valve | Code |
|---|---|---|---|---|
| Level ≤ 25% (low water) | 0 (trip) | 100 (force fill) | 0 | LOW_WATER_TRIP |
| Pressure ≥ 1.5 MPa | 0 | PID | 100 (emergency vent) | OVERPRESSURE_VENT |
| Temp ≥ 160 °C | 0 (cut fuel) | PID | — | OVERTEMP_CUT |
| Level ≥ 90% | PID | 0 | — | HIGH_LEVEL_CUT |

### 5.3 Defect History

- **v1.0**: `if temper < 100: return "safe"` masked pressure/level anomalies.
- **v1.1** (`f21d25e`): independent tri-parameter OR check.
- **v2.0** (this version):
  - Level alarm direction fixed — v1.1 alarmed whenever level > 10%, i.e.
    permanently during normal 50% operation; real danger (low water) was unguarded.
    Now four thresholds: high/low level, over-pressure, over-temp.
  - Pressure unit chain unified to SI Pa (simulator ×1e6, limit 1.5e6, API ÷1e6);
    v1 mixed a ×1e5 scale mislabelled as Pa, and the alarm limit (0.1 MPa real)
    sat *below* the 1.0 MPa control target.
  - Pressure PID output routed to **feed valve** — v1 routed it to the steam
    valve, a positive-feedback loop (low pressure → open steam → even lower).
  - Physics model rebalanced: v1 heat input permanently exceeded losses, so
    temperature railed at 200 °C and pressure at 2.0 MPa within minutes.
  - PID anti-windup (conditional integration) + steady-state bias added.
  - `write_modbus_valves` was dead code in v1; valve write-back now happens
    on every cycle when ModBus is enabled.

---

## 6. Troubleshooting

### 6.1 Database Connection Refused

1. Container running? `docker ps` → `boiler-postgres`
2. Port 5432 occupied? `netstat -ano | findstr 5432`
3. Host-run API: `config.json` database host must be `localhost`;
   container-run API: env `DB_HOST=postgres` (compose default).

### 6.2 Simulator Cannot Reach API

1. `boiler_api.py` listening on `0.0.0.0:5000`?
2. Both sides read the same `config.json` (`api.port`, `interval`)?
3. Firewall blocking port 5000?

### 6.3 Grafana → PostgreSQL

- Host: `postgres:5432` (same compose network — works on every OS;
  `host.docker.internal` is only for reaching a DB on the Docker host)
- Database/User: `postgres`, Password: `123456`, SSL Mode: `disable`

---

## 7. Security & Compliance

> Prototype / Educational Use Only. Not certified for industrial safety (SIL).

Production boiler control requires PLC hardware interlocks (millisecond
response), TSG 11 compliance, and network zone protection. The software
interlocks here are supervisory backups, not safety-rated trips.

---

## 8. Glossary

SCADA / HMI / PLC / ModBus / SIL — standard industrial automation terms.
Interlock: a hard-wired or supervisory rule that forces safe actuator states
regardless of controller output.
