"""锅炉传感器模拟器：自动向 Flask 接口发送仿真数据."""

import random
import time

import requests

API_URL = "http://localhost:5000/api/sensor-data"
INTERVAL = 5  # 秒


def generate_sensor_data(tick: int):
    """
    模拟锅炉升温过程：
    - 温度从 80 开始缓慢上升，到 110 后波动
    - 压力随温度升高
    - 水位缓慢下降
    """
    base_temp = 80 + tick * 0.3
    if base_temp > 110:
        base_temp = 110 + random.gauss(0, 2)

    temper = round(base_temp + random.gauss(0, 1.5), 1)
    
    base_press = 50000 + (temper - 80) * 1500
    press = round(base_press + random.gauss(0, 300), 0)
    
    base_high = 20 - tick * 0.015
    high = round(base_high + random.gauss(0, 0.3), 2)
    
    return {
        "temper": temper,
        "high": high,
        "press": press
    }


def main():
    print("传感器模拟器启动，目标接口:", API_URL)
    print("按 Ctrl+C 停止")
    
    tick = 0
    while True:
        try:
            data = generate_sensor_data(tick)
            resp = requests.post(API_URL, json=data, timeout=5)
            result = resp.json()
            
            alarm_flag = "⚠️ 报警" if result.get("is_alarm") else "✅ 正常"
            print(f"[{tick:04d}] {data} -> {result['status']} {alarm_flag}")
            
            tick += 1
            time.sleep(INTERVAL)
            
        except requests.exceptions.ConnectionError:
            print("连接失败: Flask 服务是否已启动？")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n模拟器已停止")
            break
        except Exception as e:
            print(f"错误: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
