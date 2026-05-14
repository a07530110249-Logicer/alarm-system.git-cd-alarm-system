"""锅炉监控数据接收接口."""

import json
import os
from datetime import datetime

import psycopg2
from flask import Flask, request, jsonify

app = Flask(__name__)

# ========== 配置加载（同之前） ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "123456",
    "dbname": "postgres"
}


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


def check_alarm(temper: int, high: int, press: int) -> tuple[str, bool]:
    """报警判断逻辑."""
    if temper < CFG["TEMP_LIMIT"]:
        return "safe", False
    if high > CFG["HIGH_LIMIT"] or press > CFG["PRESS_LIMIT"]:
        return "error", True
    return "safe", False


@app.route('/api/sensor-data', methods=['POST'])
def receive_sensor_data():
    """
    接收外部传感器数据。
    请求体 JSON 示例：
    {
        "temper": 105,
        "high": 12,
        "press": 100001
    }
    """
    try:
        data = request.get_json(force=True)
        temper = int(data.get("temper"))
        high = int(data.get("high"))
        press = int(data.get("press"))
    except (TypeError, ValueError, KeyError):
        return jsonify({"error": "参数错误或缺失"}), 400

    # 报警判断
    status, is_alarm = check_alarm(temper, high, press)

    # 写入数据库
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

    # 返回结果给发送端
    return jsonify({
        "received": True,
        "status": status,
        "is_alarm": is_alarm,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/api/health', methods=['GET'])
def health_check():
    """用于外部检测设备是否在线."""
    return jsonify({"status": "ok"}), 200


if __name__ == '__main__':
    # host='0.0.0.0' 表示允许局域网内其他设备访问
    app.run(host='0.0.0.0', port=5000, debug=False)
