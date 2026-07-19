#!/usr/bin/env bash
# 锅炉监控系统管理脚本 (Linux / macOS)
# 用法: bash manage.sh [up|dev|api|down|logs|db|test|sim|status]
set -euo pipefail

CMD="${1:-status}"

case "$CMD" in
    up)
        echo "启动 PostgreSQL + API + Grafana (Docker)..."
        docker compose up -d --build
        echo ""
        echo "服务已启动:"
        echo "  PostgreSQL : localhost:5432  (postgres / 123456)"
        echo "  API        : http://localhost:5000  (GET /api/health)"
        echo "  Grafana    : http://localhost:3000  (admin / admin)"
        echo ""
        echo "下一步: bash manage.sh sim   # 启动闭环仿真器"
        ;;
    dev)
        echo "仅启动 PostgreSQL + Grafana (API 在宿主机调试)..."
        docker compose up -d postgres grafana
        echo ""
        echo "下一步: python3 boiler_api.py   (另开终端: bash manage.sh sim)"
        ;;
    api)
        echo "宿主机启动 Flask API (开发模式, Ctrl+C 停止)..."
        python3 boiler_api.py
        ;;
    down)
        echo "停止所有 Docker 服务..."
        docker compose down
        ;;
    logs)
        docker compose logs -f
        ;;
    db)
        docker exec -it boiler-postgres psql -U postgres -d postgres
        ;;
    test)
        echo "发送一条正常工况测试数据 (T=120 L=50% P=1.0MPa)..."
        if resp=$(curl -s -X POST http://localhost:5000/api/sensor-data \
                -H "Content-Type: application/json" \
                -d '{"temper":120,"high":50,"press":1000000}'); then
            echo "响应 (应返回 safe 及三阀门指令):"
            echo "$resp"
        else
            echo "请求失败，请确认 API 已启动 (bash manage.sh up 或 bash manage.sh api)"
            exit 1
        fi
        ;;
    sim)
        echo "启动传感器模拟器 (Ctrl+C 停止)..."
        python3 sensor_simulator.py
        ;;
    status)
        docker compose ps
        ;;
    *)
        echo "用法: bash manage.sh {up|dev|api|down|logs|db|test|sim|status}"
        exit 1
        ;;
esac
