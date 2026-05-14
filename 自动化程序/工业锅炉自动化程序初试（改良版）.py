"""温度、压力和水位报警系统（工业监测版 v1.1）."""

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple


# ========== 0. 路径与配置 ==========
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "boiler_monitor.db"


@dataclass(frozen=True)
class ThresholdConfig:
    """不可变配置对象，启动时校验，运行时只读."""
    temp_limit: float
    high_limit: float
    press_limit: float
    interval: int
    hysteresis_ratio: float = 0.02  # 滞后区间 2%
    
    @classmethod
    def from_json(cls, path: Path) -> "ThresholdConfig":
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        
        # 启动时严格校验，失败立即崩溃并告知原因
        required = {"TEMP_LIMIT", "HIGH_LIMIT", "PRESS_LIMIT", "interval"}
        missing = required - set(raw.keys())
        if missing:
            raise ValueError(f"config.json 缺少必填字段: {missing}")
        
        return cls(
            temp_limit=float(raw["TEMP_LIMIT"]),
            high_limit=float(raw["HIGH_LIMIT"]),
            press_limit=float(raw["PRESS_LIMIT"]),
            interval=int(raw["interval"]),
            hysteresis_ratio=raw.get("hysteresis_ratio", 0.02)
        )


# ========== 1. 数据库层（SQLite） ==========
class BoilerDatabase:
    """时序数据持久化 + 审计追踪."""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._init_schema()
    
    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                temperature REAL NOT NULL,
                water_level REAL NOT NULL,
                pressure REAL NOT NULL,
                status TEXT NOT NULL,
                alarm_triggered INTEGER DEFAULT 0,
                alarm_reason TEXT
            );
            
            CREATE TABLE IF NOT EXISTS alarms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                triggered_at TEXT NOT NULL,
                resolved_at TEXT,
                alarm_type TEXT NOT NULL,
                severity INTEGER NOT NULL,
                description TEXT
            );
            
            CREATE INDEX IF NOT EXISTS idx_readings_time 
                ON readings(timestamp DESC);
        """)
        self.conn.commit()
    
    def insert_reading(
        self, 
        temper: float, 
        high: float, 
        press: float, 
        status: str,
        alarm_triggered: bool = False,
        alarm_reason: Optional[str] = None
    ):
        self.conn.execute("""
            INSERT INTO readings 
                (timestamp, temperature, water_level, pressure, status, alarm_triggered, alarm_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            temper, high, press, status,
            int(alarm_triggered),
            alarm_reason
        ))
        self.conn.commit()
    
    def insert_alarm(self, alarm_type: str, severity: int, description: str):
        self.conn.execute("""
            INSERT INTO alarms (triggered_at, alarm_type, severity, description)
            VALUES (?, ?, ?, ?)
        """, (datetime.now().isoformat(), alarm_type, severity, description))
        self.conn.commit()
    
    def get_recent_stats(self, hours: int = 1) -> Dict:
        """查询最近N小时的统计，用于看门狗自检."""
        cursor = self.conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(alarm_triggered) as alarm_count,
                AVG(temperature) as avg_temp,
                MAX(temperature) as max_temp
            FROM readings 
            WHERE timestamp > datetime('now', '-{} hours')
        """.format(hours))
        return dict(cursor.fetchone())
    
    def close(self):
        self.conn.close()


# ========== 2. 报警逻辑（含 Hysteresis） ==========
class AlarmEngine:
    """带滞后区间的状态机，防止边界震颤."""
    
    def __init__(self, cfg: ThresholdConfig):
        self.cfg = cfg
        self._alarm_active = False
        self._last_trigger_reason: Optional[str] = None
    
    def check(self, temper: float, high: float, press: float) -> Tuple[str, Optional[str]]:
        """
        返回: (status, reason)
        status: 'safe', 'alarm_enter', 'alarm_active', 'alarm_exit'
        """
        # 计算带滞后的动态阈值
        if self._alarm_active:
            # 已报警：需降到 (1 - hysteresis) * limit 以下才恢复
            temp_trigger = self.cfg.temp_limit * (1 - self.cfg.hysteresis_ratio)
        else:
            # 未报警：需升到 limit 以上才触发
            temp_trigger = self.cfg.temp_limit
        
        # 核心判断
        temp_exceeded = temper >= temp_trigger
        high_exceeded = high > self.cfg.high_limit
        press_exceeded = press > self.cfg.press_limit
        
        should_alarm = temp_exceeded and (high_exceeded or press_exceeded)
        
        # 状态机转换
        if should_alarm and not self._alarm_active:
            self._alarm_active = True
            self._last_trigger_reason = self._build_reason(temper, high, press, temp_trigger)
            return "alarm_enter", self._last_trigger_reason
        
        elif should_alarm and self._alarm_active:
            return "alarm_active", self._last_trigger_reason
        
        elif not should_alarm and self._alarm_active:
            self._alarm_active = False
            reason = self._last_trigger_reason
            self._last_trigger_reason = None
            return "alarm_exit", reason
        
        return "safe", None
    
    def _build_reason(self, temper, high, press, effective_limit) -> str:
        parts = []
        if temper >= effective_limit:
            parts.append(f"温度{temper:.1f}≥阈值{effective_limit:.1f}")
        if high > self.cfg.high_limit:
            parts.append(f"水位{high:.1f}>{self.cfg.high_limit}")
        if press > self.cfg.press_limit:
            parts.append(f"压力{press:.1f}>{self.cfg.press_limit}")
        return "；".join(parts)


# ========== 3. 看门狗（软件级） ==========
class Watchdog:
    """监控主循环健康状态."""
    
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self._last_beat = time.time()
        self._running = True
        self._thread = threading.Thread(target=self._monitor, daemon=True)
    
    def heartbeat(self):
        """主循环每次迭代调用."""
        self._last_beat = time.time()
    
    def _monitor(self):
        while self._running:
            time.sleep(self.timeout)
            elapsed = time.time() - self._last_beat
            if elapsed > self.timeout:
                # 实际工业场景中：重启进程/触发硬告警
                logging.critical(f"WATCHDOG: 主线程卡死 {elapsed:.1f}s 未响应！")
                print(f"[CRITICAL] 系统无响应，最后心跳: {elapsed:.1f}s 前")
    
    def start(self):
        self._thread.start()
    
    def stop(self):
        self._running = False


# ========== 4. 传感器接口（抽象层） ==========
class SensorReader:
    """统一接口，模拟/真实切换只需替换实现."""
    
    def read(self) -> Dict[str, float]:
        raise NotImplementedError
    
    def health_check(self) -> bool:
        return True


class SimulatedSensor(SensorReader):
    """模拟数据：用于无硬件调试."""
    
    def __init__(self, scenario: str = "normal"):
        self.scenario = scenario
        self._counter = 0
    
    def read(self) -> Dict[str, float]:
        self._counter += 1
        # 场景：正常 → 临界抖动 → 报警 → 恢复
        if self.scenario == "chatter_test":
            if self._counter % 20 < 5:
                return {"temper": 100.5, "high": 11, "press": 100001}  # 报警
            elif self._counter % 20 < 10:
                return {"temper": 99.5, "high": 9, "press": 99999}        # 安全（测试hysteresis）
            else:
                return {"temper": 105, "high": 15, "press": 120000}       # 持续报警
        
        # 默认：稳定正常
        return {"temper": 95, "high": 8, "press": 80000}


# ========== 5. 日志配置 ==========
def setup_logging():
    log_file = f"alarm_{datetime.now():%Y%m%d}.log"
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    # 同时输出到控制台
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    logging.getLogger("").addHandler(console)


# ========== 6. 主程序 ==========
class BoilerMonitor:
    """系统组装与生命周期管理."""
    
    def __init__(self):
        self.cfg = ThresholdConfig.from_json(CONFIG_PATH)
        self.db = BoilerDatabase()
        self.alarm_engine = AlarmEngine(self.cfg)
        self.watchdog = Watchdog(timeout=self.cfg.interval * 3)
        self.sensor = SimulatedSensor(scenario="chatter_test")  # 切真实硬件时替换
        self._running = False
    
    def _handle_alarm(self, status: str, reason: str):
        """统一的报警处理管道."""
        if status == "alarm_enter":
            logging.warning(f"ALARM ENTER: {reason}")
            self.db.insert_alarm("MULTI_PARAM", 2, reason)
            # TODO: 接入邮件/微信/蜂鸣器
            print(f"🔴 报警触发: {reason}")
            
        elif status == "alarm_exit":
            logging.info(f"ALARM EXIT: {reason}")
            print(f"🟢 报警解除: {reason}")
    
    def run(self):
        print("=" * 50)
        print("工业锅炉监测系统启动")
        print(f"阈值: 温度≥{self.cfg.temp_limit}, 水位>{self.cfg.high_limit}, 压力>{self.cfg.press_limit}")
        print(f"滞后区间: {self.cfg.hysteresis_ratio*100:.0f}%")
        print("按 Ctrl+C 停止")
        print("=" * 50)
        
        self.watchdog.start()
        self._running = True
        
        try:
            while self._running:
                # 1. 读取
                data = self.sensor.read()
                
                # 2. 判断
                status, reason = self.alarm_engine.check(**data)
                
                # 3. 持久化
                is_alarm = status in ("alarm_enter", "alarm_active")
                self.db.insert_reading(
                    data["temper"], data["high"], data["press"],
                    status, is_alarm, reason
                )
                
                # 4. 报警管道
                if status in ("alarm_enter", "alarm_exit"):
                    self._handle_alarm(status, reason or "")
                
                # 5. 日志与看门狗
                msg = f"[{datetime.now():%H:%M:%S}] T={data['temper']:.1f} H={data['high']:.1f} P={data['press']:.1f} | {status}"
                print(msg)
                logging.info(msg)
                self.watchdog.heartbeat()
                
                time.sleep(self.cfg.interval)
                
        except KeyboardInterrupt:
            print("\n系统已手动停止")
        finally:
            self._cleanup()
    
    def _cleanup(self):
        self.watchdog.stop()
        self.db.close()
        logging.info("系统正常关闭，数据库连接已释放")


# ========== 入口 ==========
if __name__ == "__main__":
    setup_logging()
    app = BoilerMonitor()
    app.run()
