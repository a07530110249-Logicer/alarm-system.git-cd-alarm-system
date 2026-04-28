一个基于 Python 的工业参数监测报警程序，支持阈值配置和日志记录。

## 功能

- 实时监测温度、压力、水位三项指标
- 温度 ≥ 100 时，若水位 > 10 或压力 > 100000 触发报警
- 支持通过 `config.json` 自定义阈值
- 自动记录运行日志到 `alarm_YYYYMMDD.log`

## 快速开始

1. 克隆仓库
   ```bash
   git clone https://github.com/a07530110249-Logicer/alarm-system.git
   cd-alarm-system
2、待办事项
 
接入真实传感器（串口/ModBus）
 
添加邮件/微信报警通知
 
支持 Web 可视化界面

3、技术栈
 
Python 3.x
 
标准库： json ,  logging ,  time ,  datetime


## 架构图

```mermaid
flowchart TD
    subgraph Input["📡 数据采集层"]
        S1[SensorReader<br/>Simulated/ModBus/Serial]
    end
    
    subgraph Core["⚙️ 核心逻辑层"]
        A1[AlarmEngine<br/>State Machine + Hysteresis]
        M1[BoilerMonitor<br/>Lifecycle + Alarm Pipe]
    end
    
    subgraph Data["💾 数据持久层"]
        D1[(BoilerDatabase<br/>SQLite)]
        W1[Watchdog<br/>Software Monitor]
    end
    
    subgraph Output["📤 输出层"]
        L1[Log Files]
        Q1[Structured Query]
    end
    
    S1 --> A1
    A1 --> M1
    M1 --> D1
    M1 --> W1
    D1 --> Q1
    M1 --> L1
