# 工业锅炉监控系统

基于Python的工业参数监测报警系统，支持传感器模拟、
阈值报警、时序数据存储和REST API接口。

## 快速开始

```bash
cd alarm-system
docker-compose up          # 启动完整环境
python sensor_simulator.py # 或单独运行模拟器

## 快速开始

1. 克隆仓库
   ```bash
   git clone https://github.com/a07530110249-Logicer/alarm-system.git-cd-alarm-system

2、技术栈
 
Python 3.x
 
标准库： json ,  logging ,  time ,  datetime ，docker

3、后续接入了postageSQL数据库，使得我们可以记录程序数据，以便我们实现报警功能

## 后续待扩展事项

1. 
传感器仿真器：写一个  sensor_simulator.py ，自动每隔几秒向你的 HTTP 接口发送带随机波动的数据，模拟真实锅炉升温曲线

2. 
Grafana 可视化：Docker 再起一个 Grafana 容器，对接 PostgreSQL 画实时仪表盘

3. 
ModBus 接入：把 HTTP 接口换成  pymodbus  读取虚拟 PLC，协议层更贴近工业现场

4. 
报警通知： is_alarm=True  时发邮件/企业微信，而不只是打印