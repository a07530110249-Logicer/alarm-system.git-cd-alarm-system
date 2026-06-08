# Technical Documentation — Industrial Boiler Monitoring System

> For developers, operators, and technical reviewers.  
> Describes architecture, deployment, API contracts, and troubleshooting.

---

## 1. System Architecture

### 1.1 Layered Stack

```

Data Layer (Sensors / Simulators)
│
│ HTTP POST /api/sensor-data
▼
Service Layer (Flask API + Alarm Logic)
│
├──→ PostgreSQL (Persistent Storage)
│
└──→ JSON Response to Client
│
▼
Presentation Layer (Grafana / demo.html)

```

### 1.2 Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Protocol | HTTP REST | Universal, debug-friendly, any device can push |
| Database | PostgreSQL | Complex queries, mature JSON ecosystem, simple Docker deploy |
| Orchestration | Docker Compose | Single-file infrastructure, one-command lifecycle |
| Alarm Logic | Independent tri-parameter | Prevents temperature from masking pressure/level anomalies |
| Configuration | External JSON | Thresholds adjustable without code changes |

---

## 2. Deployment Guide

### 2.1 Requirements

- OS: Windows 10/11, Linux, or macOS
- Docker Desktop (with Docker Compose)
- Python 3.10+
- PowerShell 7+ (Windows) / Bash (Linux/macOS)

### 2.2 Install Dependencies

```bash
pip install -r requirements.txt
```

Contents of `requirements.txt`:

```
flask>=2.0
psycopg2-binary>=2.9
requests>=2.28
```

### 2.3 Startup Sequence

Must start in order due to service dependencies.
 
Step 1:  .\manage.ps1 up  — Start PostgreSQL + Grafana containers
 
Step 2:  python boiler_api.py  — Start Flask API; auto-creates tables
 
Step 3:  python sensor_simulator.py  — Start sensor simulator


## 3. Database Schema

### 3.1 Table: sensor_logs

id:  SERIAL , PRIMARY KEY — Auto-increment
 
created_at:  TIMESTAMP , DEFAULT CURRENT_TIMESTAMP — Ingestion time
 
temper:  INTEGER , NOT NULL — Temperature (°C)
 
high:  INTEGER , NOT NULL — Water level
 
press:  INTEGER , NOT NULL — Pressure (Pa)
 
status:  VARCHAR(10) , NOT NULL —  safe  or  error 
 
is_alarm:  BOOLEAN , NOT NULL — Alarm triggered

### 3.2 Index

```sql
CREATE INDEX idx_sensor_logs_time ON sensor_logs(created_at);
```

Purpose: Accelerates time-range queries for Grafana and log retrieval.

### 3.3 Common Queries

```sql
-- Latest 10 records
SELECT * FROM sensor_logs ORDER BY created_at DESC LIMIT 10;

-- Alarm count in last hour
SELECT COUNT(*) FROM sensor_logs 
WHERE is_alarm = true 
AND created_at > NOW() - INTERVAL '1 hour';

-- Temperature trend for Grafana
SELECT created_at, temper FROM sensor_logs 
WHERE created_at > NOW() - INTERVAL '1 hour' 
ORDER BY created_at;
```

---

## 4. API Specification

### 4.1 `POST /api/sensor-data`

Headers:

```
Content-Type: application/json
```

Request Body:

```json
{
    "temper": 105,
    "high": 12,
    "press": 100001
}
```

Response 200 OK:

```json
{
    "received": true,
    "status": "error",
    "is_alarm": true,
    "timestamp": "2026-05-14T17:45:00"
}
```

Response 400 Bad Request:

```json
{
    "error": "Invalid or missing parameters"
}
```

Response 500 Internal Server Error:

```json
{
    "error": "Database write failed: <details>"
}
```

### 4.2 `GET /api/health`

Response:

```json
{
    "status": "ok"
}
```

Usage: Docker health checks, load-balancer heartbeat.

---

## 5. Alarm Logic

### 5.1 Configuration (`config.json`)

```json
{
    "TEMP_LIMIT": 100,
    "HIGH_LIMIT": 10,
    "PRESS_LIMIT": 100000,
    "interval": 5
}
```

### 5.2 Pseudocode

```python
if temper >= TEMP_LIMIT or high > HIGH_LIMIT or press > PRESS_LIMIT:
    return "error", True
else:
    return "safe", False
```

### 5.3 Known Defect History

v1.0 Flaw: Early versions checked `if temper < 100: return "safe"` first, causing pressure/level anomalies to be ignored when temperature was low.

v1.1 Fix: Changed to independent tri-parameter `OR` check. Any single parameter exceeding its threshold triggers an alarm.

Commit: `f21d25e` — fix: independent tri-parameter alarm check

---

## 6. Troubleshooting

### 6.1 Database Connection Refused

Symptom: `psycopg2.OperationalError: connection refused`

Checklist:

1. Container running? `docker ps` — look for `boiler-postgres`
2. Port 5432 occupied? `netstat -ano | findstr 5432`
3. Password mismatch? Compare `DB_CONFIG` in `boiler_api.py` with `docker-compose.yml` environment variables

### 6.2 Simulator Cannot Reach API

Symptom: `requests.exceptions.ConnectionError`

Checklist:

1. `boiler_api.py` running and listening on `0.0.0.0:5000`?
2. Firewall blocking port 5000?
3. Using `demo.html`? Verify CORS headers are added to `boiler_api.py`

### 6.3 Grafana Fails to Connect PostgreSQL

Connection Settings:

- Host: `host.docker.internal:5432` (Windows/macOS) or `postgres:5432` (Linux)
- Database: `postgres`
- User: `postgres`
- Password: `123456`
- SSL Mode: `disable`

---

## 7. Security & Compliance

> Prototype / Educational Use Only. Not certified for industrial safety (SIL).

Production boiler control requires:

- PLC hardware safety interlocks (millisecond response)
- Compliance with TSG 11 (Boiler Safety Technical Regulations)
- Cybersecurity level protection (if deployed on factory networks)

This Python layer serves as supervisory monitoring and data logging only. It does not replace underlying safety controllers.

---

## 8. Extension Roadmap

P1: Real sensor integration —  pymodbus  (ModBus TCP/RTU)
 
P2: Alarm notifications —  smtplib , enterprise WeChat Webhook
 
P3: Data compression — PostgreSQL partitioning + scheduled jobs
 
P4: User authentication — Flask-Login + RBAC
 
P5: Production deployment — Gunicorn + Nginx + HTTPS


## 9. Glossary

SCADA: Supervisory Control and Data Acquisition
 
HMI: Human-Machine Interface
 
PLC: Programmable Logic Controller
 
ModBus: Industrial fieldbus communication protocol
 
SIL: Safety Integrity Level
 
CORS: Cross-Origin Resource Sharing