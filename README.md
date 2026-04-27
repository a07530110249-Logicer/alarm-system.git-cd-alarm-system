一个基于 Python 的工业参数监测报警程序，支持阈值配置和日志记录。

## 功能

- 实时监测温度、压力、水位三项指标
- 温度 ≥ 100 时，若水位 > 10 或压力 > 100000 触发报警
- 支持通过 `config.json` 自定义阈值
- 自动记录运行日志到 `alarm_YYYYMMDD.log`

## 快速开始

1. 克隆仓库
   ```bash
   git clone https://github.com/a07530110249-Logicer/
