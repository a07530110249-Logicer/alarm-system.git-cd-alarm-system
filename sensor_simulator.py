"""锅炉传感器模拟器：物理仿真 + 闭环执行 API 下发的三阀门指令.

工作流程:
    1. 物理模型步进, 产出 温度/水位/压力
    2. POST 到 API (附带当前阀门实际开度, 便于 Grafana 记录真实值)
    3. 应用 API 返回的阀门指令:
       - feed_valve / water_valve: 自动(PID)或手动模式下均执行
       - steam_valve: 为 null 时保持本地负荷扰动, 否则执行(手动/联锁泄压)
    4. 若 API 未返回阀门字段(兼容旧版服务端), 退化为本地 P 控制
"""

import json
import os
import random
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)

API_URL = f"http://localhost:{CFG['api']['port']}/api/sensor-data"
INTERVAL = float(CFG.get("interval", 2))  # 与 API 侧控制周期一致


class BoilerModel:
    """简化的锅炉物理模型(v4, 闭环调参验证通过).

    稳态工作点: 水位~50% 压力~1.0MPa 温度~120°C
    阀门语义与 API 侧一致: feed=进料(燃料), water=进水, steam=蒸汽(负荷)
    """

    def __init__(self):
        self.water_level = 50.0      # 水位 %
        self.steam_pressure = 0.8    # 压力 MPa
        self.temperature = 110.0     # 温度 °C (演示标度, 非精确物理量)
        self.feed_valve = 40.0       # 进料阀开度 %
        self.water_valve = 50.0      # 进水阀开度 %
        self.steam_valve = 40.0      # 蒸汽阀开度 %

    def step(self, dt=1.0):
        # 蒸汽负荷流量: 开度越大、压力越高, 带出蒸汽越多
        steam_mass = self.steam_valve * (0.3 + 0.7 * self.steam_pressure / 2.0) * 0.04

        # 水位: 进水 - 蒸发消耗
        self.water_level += (self.water_valve * 0.02 - steam_mass) * dt

        # 压力: 进料产汽 - 负荷耗汽
        self.steam_pressure += (self.feed_valve * 0.0011 - steam_mass * 0.05) * dt

        # 温度: 由进料阀驱动的一阶惯性环节(热惯性约20s)
        target_t = 40.0 + self.feed_valve * 2.0 - steam_mass * 6.0
        self.temperature += (target_t - self.temperature) * dt / 20.0

        # 物理限幅
        self.water_level = max(0.0, min(100.0, self.water_level))
        self.steam_pressure = max(0.05, min(2.0, self.steam_pressure))
        self.temperature = max(20.0, min(250.0, self.temperature))

        return {
            "temper": round(self.temperature, 1),
            "high": round(self.water_level, 1),
            "press": round(self.steam_pressure * 1_000_000, 0),  # MPa -> Pa
        }

    def disturb_load(self):
        """蒸汽阀模拟负荷扰动: 小概率小幅随机游走(负荷侧行为)."""
        if random.random() < 0.03:
            self.steam_valve = max(25.0, min(60.0,
                                 self.steam_valve + random.uniform(-8, 8)))

    def local_fallback_control(self):
        """服务端未返回阀门指令时的本地P控制(向后兼容)."""
        level_err = CFG["setpoints"]["water_level"] - self.water_level
        press_err = CFG["setpoints"]["pressure_mpa"] - self.steam_pressure
        self.water_valve = max(0.0, min(100.0, 50 + level_err * 2.0))
        self.feed_valve = max(0.0, min(100.0, 40 + press_err * 30.0))


def main():
    print("锅炉三阀门闭环仿真器启动")
    print(f"目标: 水位 {CFG['setpoints']['water_level']}% | "
          f"压力 {CFG['setpoints']['pressure_mpa']} MPa | 周期 {INTERVAL}s")
    print("阀门: 进料(压力PID) 进水(水位PID) 蒸汽(负荷/联锁)")
    print("按 Ctrl+C 停止")

    boiler = BoilerModel()
    tick = 0

    while True:
        try:
            boiler.disturb_load()
            data = boiler.step(dt=INTERVAL)

            resp = requests.post(API_URL, json={
                "temper": int(data["temper"]),
                "high": int(data["high"]),
                "press": int(data["press"]),
                "valves": {  # 上报现场实际开度, 入库供 Grafana 使用
                    "feed_valve": round(boiler.feed_valve, 1),
                    "water_valve": round(boiler.water_valve, 1),
                    "steam_valve": round(boiler.steam_valve, 1),
                },
            }, timeout=5)

            result = resp.json()
            valves = result.get("valves")
            if valves:  # 闭环: 执行服务端下发的阀门指令
                boiler.feed_valve = valves["feed_valve"]
                boiler.water_valve = valves["water_valve"]
                if valves["steam_valve"] is not None:
                    boiler.steam_valve = valves["steam_valve"]
            else:       # 旧版服务端: 本地退化控制
                boiler.local_fallback_control()

            alarm = "⚠️ ALARM" if result.get("is_alarm") else "✅ SAFE"
            extra = ""
            if result.get("alarms"):
                extra += f" [{'/'.join(result['alarms'])}]"
            if result.get("interlock"):
                extra += f" 🔒{result['interlock']}"
            if result.get("mode") == "manual":
                extra += " (手动)"

            print(f"[{tick:04d}] T:{data['temper']:6.1f}°C "
                  f"L:{data['high']:5.1f}% "
                  f"P:{data['press'] / 1_000_000:5.2f}MPa "
                  f"FV:{boiler.feed_valve:5.1f}% "
                  f"WV:{boiler.water_valve:5.1f}% "
                  f"SV:{boiler.steam_valve:5.1f}% → {alarm}{extra}")

            tick += 1
            time.sleep(INTERVAL)

        except requests.exceptions.ConnectionError:
            print("连接失败: Flask服务是否启动？")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n已停止")
            break
        except Exception as e:
            print(f"错误: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
