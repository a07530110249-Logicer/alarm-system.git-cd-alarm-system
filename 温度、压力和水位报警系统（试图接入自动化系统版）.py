"""温度、压力和水位报警系统（自动化监测版）."""

import json
import logging
import os
import time
from datetime import datetime

# ========== 1. 加载配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

# ========== 2. 配置日志 ==========
logging.basicConfig(
    filename=f"alarm_{datetime.now():%Y%m%d}.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s"
)

# ========== 3. 报警判断函数 ==========
def check_alarm(temper: int, high: int, press: int) -> str:
    """
    判断当前状态是否报警。
    规则：温度 >= TEMP_LIMIT 时，若水位 > HIGH_LIMIT 或 压力 > PRESS_LIMIT 则报警。
    """
    if temper < CFG["TEMP_LIMIT"]:
        return "safe"
    
    if high > CFG["HIGH_LIMIT"] or press > CFG["PRESS_LIMIT"]:
        return "error"
    
    return "safe"

# ========== 4. 传感器读取（模拟） ==========
def read_sensors():
    """模拟读取传感器，实际替换为串口/ModBus读取"""
    # TODO: 接入真实硬件
    return {"temper": 105, "high": 12, "press": 100001}

# ========== 5. 主循环 ==========
def main():
    print("报警系统启动，按 Ctrl+C 停止")
    
    while True:
        try:
            data = read_sensors()
            result = check_alarm(**data)
            
            msg = f"[{datetime.now():%H:%M:%S}] {data} -> {result}"
            print(msg)
            logging.info(msg)
            
            if result == "error":
                # TODO: 发送通知（邮件/微信/蜂鸣器）
                pass
            
            time.sleep(CFG.get("interval", 5))
            
        except KeyboardInterrupt:
            print("\n系统已手动停止")
            break
        except Exception as e:
            logging.error(f"运行错误: {e}")
            print(f"错误: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
