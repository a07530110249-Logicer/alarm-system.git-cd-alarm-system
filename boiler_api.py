"""锅炉监控数据接收接口 (Flask HTTP API + ModBus + PID + 三阀门控制).

阀门说明:
    - feed_valve  进料阀(燃料): 由压力 PID 控制, 压力偏低 -> 开大进料
    - water_valve 进水阀:       由水位 PID 控制, 水位偏低 -> 开大进水
    - steam_valve 蒸汽阀:       正常情况下由负荷侧(现场)决定;
                                手动模式或超压联锁时由本系统下发指令

安全联锁(优先级高于 PID 与手动模式):
    - 缺水(低水位): 进料阀=0(停炉), 进水阀=100(强制补水), 蒸汽阀=0
    - 超压:         进料阀=0, 蒸汽阀=100(紧急泄压)
    - 超温:         进料阀=0
    - 高水位:       进水阀=0
"""

import json
import os
from datetime import datetime

import psycopg2
from flask import Flask, jsonify, request

# 可选：ModBus 支持（没有 pymodbus 也能跑纯 HTTP 模式）
try:
    from pymodbus.client import ModbusTcpClient
    MODBUS_AVAILABLE = True
except ImportError:
    MODBUS_AVAILABLE = False

app = Flask(__name__)

# ========== 加载配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

ALARM_CFG = CFG["alarm"]
SETPOINTS = CFG["setpoints"]
INTERVAL = float(CFG.get("interval", 2))  # 控制周期(秒), PID 的 dt 与之一致

# ========== 数据库连接（环境变量优先，便于 Docker 组网） ==========
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", CFG["database"]["host"]),
    "port": int(os.environ.get("DB_PORT", CFG["database"]["port"])),
    "user": os.environ.get("DB_USER", CFG["database"]["user"]),
    "password": os.environ.get("DB_PASSWORD", CFG["database"]["password"]),
    "dbname": os.environ.get("DB_NAME", CFG["database"]["dbname"]),
}

# ========== ModBus 配置 ==========
MODBUS_CONFIG = {
    "host": os.environ.get("MODBUS_HOST", CFG["modbus"]["host"]),
    "port": int(os.environ.get("MODBUS_PORT", CFG["modbus"]["port"])),
    "enabled": CFG["modbus"].get("enabled", False),
}

# ModBus 保持寄存器映射（读写双方必须遵守同一张表）
#   读取(传感器): 0=温度x10(°C)  1=水位x10(%)  2=压力(kPa)
#   写入(阀门):   10=进水阀x100  11=进料阀x100  12=蒸汽阀x100   (0-10000)
REG_TEMPER, REG_LEVEL, REG_PRESS = 0, 1, 2
REG_WATER_VALVE, REG_FEED_VALVE, REG_STEAM_VALVE = 10, 11, 12


# ========== PID 控制器（带抗积分饱和） ==========
class PID:
    """简单 PID 控制器, 输出限幅 0-100%（阀门开度）.

    bias 为稳态前馈开度: 误差为零时阀门停在 bias 附近,
    避免启动阶段从全关开始"等积分爬上来"的慢热过程.
    """

    def __init__(self, Kp: float, Ki: float, Kd: float, setpoint: float,
                 bias: float = 50.0):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.setpoint = setpoint
        self.bias = bias
        self.integral = 0.0
        self.prev_error = 0.0
        self.output_limit = (0.0, 100.0)

    def compute(self, measured: float, dt: float = 1.0) -> float:
        error = self.setpoint - measured
        derivative = (error - self.prev_error) / dt if dt > 0 else 0.0
        self.prev_error = error

        # 条件积分抗饱和：输出未顶到限幅, 或积分方向有助于脱离饱和时才累积
        trial_integral = self.integral + error * dt
        raw = self.bias + self.Kp * error + self.Ki * trial_integral + self.Kd * derivative
        lo, hi = self.output_limit
        output = max(lo, min(hi, raw))
        if output == raw or (output == hi and error < 0) or (output == lo and error > 0):
            self.integral = trial_integral
        return output


# 初始化 PID 实例（参数全部来自 config.json）
_pid_w = CFG["pid"]["water"]
_pid_p = CFG["pid"]["pressure"]
pid_water = PID(setpoint=SETPOINTS["water_level"], **_pid_w)      # 水位 -> 进水阀
pid_pressure = PID(setpoint=SETPOINTS["pressure_mpa"], **_pid_p)  # 压力 -> 进料阀

# ========== 控制模式状态（auto / manual） ==========
CONTROL = {
    "mode": "auto",
    "manual": {"feed_valve": 40.0, "water_valve": 50.0, "steam_valve": 40.0},
}
LAST_COMMAND = {"feed_valve": 40.0, "water_valve": 50.0, "steam_valve": None}


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    """首次运行自动建表; 对老库自动补列（幂等迁移）."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sensor_logs (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    temper INT NOT NULL,
                    high INT NOT NULL,
                    press INT NOT NULL,
                    status VARCHAR(10) NOT NULL,
                    is_alarm BOOLEAN NOT NULL,
                    water_valve INT DEFAULT 0,
                    feed_valve INT DEFAULT 0,
                    steam_valve INT DEFAULT 0
                );
            """)
            # 老版本表结构迁移：缺什么列补什么列
            for col in ("water_valve", "feed_valve", "steam_valve"):
                cur.execute(f"""
                    ALTER TABLE sensor_logs
                    ADD COLUMN IF NOT EXISTS {col} INT DEFAULT 0;
                """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_sensor_logs_time
                ON sensor_logs(created_at);
            """)
        conn.commit()
    finally:
        conn.close()


def check_alarm(temper: int, high: int, press: int) -> tuple[str, bool, list]:
    """四阈值独立监控.

    修正说明(v2):
      - 水位不再只有"高于10%就报警"的错误逻辑, 改为:
        高于 LEVEL_HIGH 报高水位, 低于 LEVEL_LOW 报缺水(锅炉真正的危险工况)
      - 压力阈值 PRESS_HIGH=1.5MPa, 高于控制目标 1.0MPa, 不再互相打架
    """
    alarms = []
    if temper >= ALARM_CFG["TEMP_HIGH"]:
        alarms.append("超温")
    if press >= ALARM_CFG["PRESS_HIGH"]:
        alarms.append("超压")
    if high >= ALARM_CFG["LEVEL_HIGH"]:
        alarms.append("高水位")
    if high <= ALARM_CFG["LEVEL_LOW"]:
        alarms.append("缺水")
    if alarms:
        return "error", True, alarms
    return "safe", False, []


def apply_interlocks(temper: int, high: int, press: int,
                     feed: float, water: float, steam):
    """安全联锁: 返回 (feed, water, steam, interlock_name or None)."""
    if high <= ALARM_CFG["LEVEL_LOW"]:
        return 0.0, 100.0, 0.0, "LOW_WATER_TRIP"       # 缺水: 停炉+强制补水
    if press >= ALARM_CFG["PRESS_HIGH"]:
        return 0.0, water, 100.0, "OVERPRESSURE_VENT"  # 超压: 停炉+紧急泄压
    if temper >= ALARM_CFG["TEMP_HIGH"]:
        return 0.0, water, steam, "OVERTEMP_CUT"       # 超温: 切断进料
    if high >= ALARM_CFG["LEVEL_HIGH"]:
        return feed, 0.0, steam, "HIGH_LEVEL_CUT"      # 高水位: 关闭进水
    return feed, water, steam, None


def compute_valves(temper: int, high: int, press: int) -> dict:
    """核心控制律: PID(自动) 或 手动指令, 再经安全联锁修正."""
    press_mpa = press / 1_000_000.0  # Pa -> MPa (修正: 旧代码除以1e5, 差了10倍)

    if CONTROL["mode"] == "manual":
        m = CONTROL["manual"]
        feed, water, steam = m["feed_valve"], m["water_valve"], m["steam_valve"]
    else:
        water = pid_water.compute(high, dt=INTERVAL)         # 水位 -> 进水阀
        feed = pid_pressure.compute(press_mpa, dt=INTERVAL)  # 压力 -> 进料阀
        steam = None  # 自动模式下蒸汽阀由负荷侧决定, None=不干预

    feed, water, steam, interlock = apply_interlocks(
        temper, high, press, feed, water, steam)

    command = {
        "feed_valve": round(feed, 1),
        "water_valve": round(water, 1),
        "steam_valve": round(steam, 1) if steam is not None else None,
        "interlock": interlock,
        "mode": CONTROL["mode"],
    }
    LAST_COMMAND.update(command)
    return command


# ========== ModBus 读写 ==========
def read_modbus_sensors():
    """从 ModBus 虚拟 PLC 读取传感器数据(统一换算: x10 / x10 / kPa->Pa)."""
    if not MODBUS_AVAILABLE or not MODBUS_CONFIG["enabled"]:
        return None
    try:
        client = ModbusTcpClient(MODBUS_CONFIG["host"],
                                 port=MODBUS_CONFIG["port"])
        if not client.connect():
            return None
        result = client.read_holding_registers(address=0, count=3)
        client.close()
        if result.isError():
            return None
        return {
            "temper": result.registers[REG_TEMPER] / 10.0,
            "high": result.registers[REG_LEVEL] / 10.0,
            "press": result.registers[REG_PRESS] * 1000,  # kPa -> Pa
        }
    except Exception:
        return None


def write_modbus_valves(command: dict) -> bool:
    """向 ModBus 虚拟 PLC 写入三只阀门开度(x100 存储, 0-10000)."""
    if not MODBUS_AVAILABLE or not MODBUS_CONFIG["enabled"]:
        return False
    steam = command["steam_valve"]
    if steam is None:
        return False  # 自动模式下蒸汽阀不下发, 无需写
    try:
        client = ModbusTcpClient(MODBUS_CONFIG["host"],
                                 port=MODBUS_CONFIG["port"])
        if not client.connect():
            return False
        client.write_registers(REG_WATER_VALVE, [
            int(command["water_valve"] * 100),
            int(command["feed_valve"] * 100),
            int(steam * 100),
        ])
        client.close()
        return True
    except Exception:
        return False


# ========== HTTP 接口 ==========
def store_record(temper, high, press, status, is_alarm, command, actual=None):
    """写入一条日志. actual 为现场上报的阀门实际开度(优先于指令值)."""
    if actual:
        wv = actual.get("water_valve", command["water_valve"])
        fv = actual.get("feed_valve", command["feed_valve"])
        sv = actual.get("steam_valve", command["steam_valve"] or 0)
    else:
        wv = command["water_valve"]
        fv = command["feed_valve"]
        sv = command["steam_valve"] or 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sensor_logs
                (temper, high, press, status, is_alarm,
                 water_valve, feed_valve, steam_valve)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (temper, high, press, status, is_alarm,
                  int(wv), int(fv), int(sv)))
        conn.commit()
    finally:
        conn.close()


@app.route('/api/sensor-data', methods=['POST'])
def receive_sensor_data():
    """接收传感器数据 -> 报警判定 -> 计算三阀门指令 -> 入库 -> 返回指令(闭环)."""
    try:
        data = request.get_json(force=True)
        temper = int(data.get("temper"))
        high = int(data.get("high"))
        press = int(data.get("press"))
    except (TypeError, ValueError):
        return jsonify({"error": "参数错误或缺失"}), 400

    actual = data.get("valves")  # 可选: 现场阀门实际开度
    status, is_alarm, alarms = check_alarm(temper, high, press)
    command = compute_valves(temper, high, press)

    try:
        store_record(temper, high, press, status, is_alarm, command, actual)
    except Exception as e:
        return jsonify({"error": f"数据库写入失败: {e}"}), 500

    if MODBUS_CONFIG["enabled"]:
        write_modbus_valves(command)

    return jsonify({
        "received": True,
        "status": status,
        "is_alarm": is_alarm,
        "alarms": alarms,
        "valves": {
            "feed_valve": command["feed_valve"],
            "water_valve": command["water_valve"],
            "steam_valve": command["steam_valve"],  # null = 由现场负荷决定
        },
        "interlock": command["interlock"],
        "mode": command["mode"],
        "timestamp": datetime.now().isoformat(),
    }), 200


@app.route('/api/valves', methods=['GET'])
def get_valves():
    """查询当前控制模式与最近一次阀门指令."""
    return jsonify({
        "mode": CONTROL["mode"],
        "manual": CONTROL["manual"],
        "last_command": LAST_COMMAND,
    }), 200


@app.route('/api/valves', methods=['POST'])
def set_valves():
    """切换控制模式.

    {"mode": "auto"}  恢复自动 PID
    {"mode": "manual", "feed_valve": 0-100, "water_valve": 0-100, "steam_valve": 0-100}
    """
    data = request.get_json(force=True) or {}
    mode = data.get("mode")
    if mode == "auto":
        CONTROL["mode"] = "auto"
        return jsonify({"mode": "auto"}), 200
    if mode == "manual":
        try:
            for key in ("feed_valve", "water_valve", "steam_valve"):
                val = float(data[key])
                if not 0.0 <= val <= 100.0:
                    raise ValueError
                CONTROL["manual"][key] = val
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "manual 模式需提供 0-100 的 "
                                     "feed_valve/water_valve/steam_valve"}), 400
        CONTROL["mode"] = "manual"
        return jsonify({"mode": "manual", "manual": CONTROL["manual"]}), 200
    return jsonify({"error": "mode 只能是 auto 或 manual"}), 400


@app.route('/api/modbus-read', methods=['GET'])
def modbus_read():
    """手动触发 ModBus 读取（调试用）."""
    data = read_modbus_sensors()
    if data is None:
        return jsonify({"error": "ModBus 未连接或不可用"}), 503
    return jsonify(data), 200


@app.route('/api/modbus-collect', methods=['POST'])
def modbus_collect():
    """ModBus 全回路: 读传感器 -> 报警 -> 算阀门 -> 入库 -> 回写阀门."""
    data = read_modbus_sensors()
    if data is None:
        return jsonify({"error": "ModBus 未连接或不可用"}), 503

    temper = int(round(data["temper"]))
    high = int(round(data["high"]))
    press = int(round(data["press"]))

    status, is_alarm, alarms = check_alarm(temper, high, press)
    command = compute_valves(temper, high, press)
    try:
        store_record(temper, high, press, status, is_alarm, command)
    except Exception as e:
        return jsonify({"error": f"数据库写入失败: {e}"}), 500
    written = write_modbus_valves(command)

    return jsonify({
        "sensors": {"temper": temper, "high": high, "press": press},
        "status": status,
        "is_alarm": is_alarm,
        "alarms": alarms,
        "valves": command,
        "modbus_written": written,
    }), 200


@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "mode": CONTROL["mode"]}), 200


if __name__ == '__main__':
    init_db()
    print("数据库已初始化")
    print(f"PID 控制器已就绪 (水位目标 {SETPOINTS['water_level']}%, "
          f"压力目标 {SETPOINTS['pressure_mpa']} MPa)")
    print(f"控制周期 {INTERVAL}s | 三阀门: 进料/进水/蒸汽")
    app.run(host=CFG["api"]["host"], port=int(CFG["api"]["port"]), debug=False)
