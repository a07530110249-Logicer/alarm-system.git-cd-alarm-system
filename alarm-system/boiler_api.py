"""锅炉监控数据接收接口 (Flask HTTP API)."""

import json
import os
from datetime import datetime

import psycopg2
from flask import Flask, request, jsonify

app = Flask(__name__)

# ========== 加载配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

# ========== 数据库连接（与 docker-compose 对齐） ==========
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "123456",
    "dbname": "postgres"
}


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    """首次运行时自动建表。"""
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
                    is_alarm BOOLEAN NOT NULL
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_sensor_logs_time 
                ON sensor_logs(created_at);
            """)
        conn.commit()
    finally:
        conn.close()


def check_alarm(temper: int, high: int, press: int) -> tuple[str, bool]:
    """
    独立监控三项指标，任一参数超过安全阈值即触发报警。
    
    规则：
    - 温度 >= TEMP_LIMIT（达到阈值即报警）
    - 水位 > HIGH_LIMIT（超过阈值才报警）
    - 压力 > PRESS_LIMIT（超过阈值才报警）
    """
    if temper >= CFG["TEMP_LIMIT"] or high > CFG["HIGH_LIMIT"] or press > CFG["PRESS_LIMIT"]:
        return "error", True
    
    return "safe", False


@app.route('/api/sensor-data', methods=['POST'])
def receive_sensor_data():
    """
    接收外部传感器数据。
    JSON 示例: {"temper": 105, "high": 12, "press": 100001}
    """
    try:
        data = request.get_json(force=True)
        temper = int(data.get("temper"))
        high = int(data.get("high"))
        press = int(data.get("press"))
    except (TypeError, ValueError, KeyError):
        return jsonify({"error": "参数错误或缺失"}), 400

    status, is_alarm = check_alarm(temper, high, press)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sensor_logs (temper, high, press, status, is_alarm)
                VALUES (%s, %s, %s, %s, %s)
            """, (temper, high, press, status, is_alarm))
        conn.commit()
    except Exception as e:
        return jsonify({"error": f"数据库写入失败: {e}"}), 500
    finally:
        conn.close()

    return jsonify({
        "received": True,
        "status": status,
        "is_alarm": is_alarm,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200


if __name__ == '__main__':
    init_db()
    print("数据库已初始化")
    print("启动 Flask 服务: http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)

