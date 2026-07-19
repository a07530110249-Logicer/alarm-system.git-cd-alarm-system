# 工业锅炉三阀门监控系统

基于 Python 的工业锅炉监测与控制系统：传感器模拟、四阈值报警、
**进料阀 / 进水阀 / 蒸汽阀** 三阀门闭环控制、安全联锁、
时序数据存储、REST API 与可选 ModBus 接入。

## 系统结构

```
sensor_simulator.py  (物理仿真 + 负荷扰动)
        │  POST /api/sensor-data (温度/水位/压力 + 阀门实际开度)
        ▼
boiler_api.py  (Flask)
        ├─ 报警判定: 超温 / 超压 / 高水位 / 缺水
        ├─ 控制律:  水位PID→进水阀   压力PID→进料阀
        │           蒸汽阀: 负荷侧决定(自动) / 系统下发(手动、联锁)
        ├─ 安全联锁: 缺水停炉补水 / 超压泄压 / 超温断料 / 高水位断水
        ├─→ PostgreSQL (时序日志)  ─→ Grafana (仪表盘)
        └─→ 响应带回三阀门指令 (闭环)
```

## 快速开始

```bash
# 方式一: 管理脚本 (推荐, Windows / Linux 通用)
.\manage.ps1 up        # Windows PowerShell
bash manage.sh up      # Linux / macOS
# 然后: manage.ps1 sim / manage.sh sim 启动闭环仿真器
# 其他命令: dev(仅数据库) api(宿主机调试) down logs db test status

# 方式二: 手动命令
docker compose up -d --build   # postgres + api + grafana
python sensor_simulator.py     # 闭环仿真, 执行 API 下发的阀门指令
```

Grafana: http://localhost:3000 (admin/admin)，数据源指向 `postgres:5432`。

## 技术栈

- Python 3.10+，Flask，psycopg2，requests，pymodbus（可选）
- PostgreSQL 16，Grafana，Docker Compose

## 控制与报警

| 参数 | 正常目标 | 报警线 | 联锁动作 |
|---|---|---|---|
| 水位 | 50% | ≥90% 高水位 / ≤25% 缺水 | 缺水: 停炉+强制补水; 高水位: 关进水 |
| 压力 | 1.0 MPa | ≥1.5 MPa 超压 | 停料 + 蒸汽阀全开泄压 |
| 温度 | ~120°C | ≥160°C 超温 | 切断进料 |

阈值、PID 参数、端口全部在 `config.json` 中调整，无需改代码。

## API 一览

| 接口 | 说明 |
|---|---|
| `POST /api/sensor-data` | 上报传感器数据，返回报警结果与三阀门指令 |
| `GET /api/valves` | 查看控制模式与最近阀门指令 |
| `POST /api/valves` | 切换 auto / manual 模式，手动设定三阀开度 |
| `GET /api/modbus-read` | ModBus 传感器读取（调试） |
| `POST /api/modbus-collect` | ModBus 全回路采集+控制 |
| `GET /api/health` | 健康检查 |

## 后续待扩展事项

1. 报警通知：`is_alarm=true` 时发邮件/企业微信，而不只是记录
2. 真实 PLC 接入：`config.json` 打开 `modbus.enabled`，对接 Diagslave 或实物
3. 数据压缩：PostgreSQL 分区 + 定时任务
4. 用户认证：Flask-Login + RBAC
