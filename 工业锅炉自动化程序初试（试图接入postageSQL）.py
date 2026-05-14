"""温度、压力和水位报警系统（PostgreSQL 版）."""

import json
import os
import time
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor

# ========== 1. 加载配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

# ========== 2. 数据库连接配置 ==========
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",          # 默认超级用户
    "password": "123456",        # 你启动容器时设的密码
    "dbname": "postgres"         # 默认数据库
}


def get_db_connection():
    """建立数据库连接。"""
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    """初始化数据表（如果不存在）。"""
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
    判断当前状态是否报警。
    返回: (状态字符串, 是否报警)
    """
    if temper < CFG["TEMP_LIMIT"]:
        return "safe", False
    
    if high > CFG["HIGH_LIMIT"] or press > CFG["PRESS_LIMIT"]:
        return "error", True
    
    return "safe", False


def read_sensors():
    """模拟读取传感器，实际替换为串口/ModBus读取"""
    # TODO: 接入真实硬件
    return {"temper": 105, "high": 12, "press": 100001}


def save_to_db(temper, high, press, status, is_alarm):
    """将传感器数据写入 PostgreSQL。"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sensor_logs (temper, high, press, status, is_alarm)
                VALUES (%s, %s, %s, %s, %s)
            """, (temper, high, press, status, is_alarm))
        conn.commit()
    except Exception as e:
        print(f"[数据库写入失败] {e}")
    finally:
        conn.close()


def main():
    print("报警系统启动（PostgreSQL 版），按 Ctrl+C 停止")
    
    # 启动时初始化数据库
    init_db()
    
    while True:
        try:
            data = read_sensors()
            status, is_alarm = check_alarm(**data)
            
            msg = f"[{datetime.now():%H:%M:%S}] {data} -> {status}"
            print(msg)
            
            # 写入数据库
            save_to_db(
                data["temper"], 
                data["high"], 
                data["press"], 
                status, 
                is_alarm
            )
            
            if is_alarm:
                print("⚠️  报警触发！已记录到数据库")
            
            time.sleep(CFG.get("interval", 5))
            
        except KeyboardInterrupt:
            print("\n系统已手动停止")
            break
        except Exception as e:
            print(f"运行错误: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
