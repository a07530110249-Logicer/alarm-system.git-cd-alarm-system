# 锅炉监控系统管理脚本 (Windows PowerShell)
# 用法: .\manage.ps1 [up|dev|api|down|logs|db|test|sim|status]
param(
    [Parameter()]
    [ValidateSet("up", "dev", "api", "down", "logs", "db", "test", "status", "sim")]
    [string]$Command = "status"
)

switch ($Command) {
    "up" {
        Write-Host "启动 PostgreSQL + API + Grafana (Docker)..."
        docker compose up -d --build
        Write-Host ""
        Write-Host "服务已启动:"
        Write-Host "  PostgreSQL : localhost:5432  (postgres / 123456)"
        Write-Host "  API        : http://localhost:5000  (GET /api/health)"
        Write-Host "  Grafana    : http://localhost:3000  (admin / admin)"
        Write-Host ""
        Write-Host "下一步: .\manage.ps1 sim   # 启动闭环仿真器"
    }
    "dev" {
        Write-Host "仅启动 PostgreSQL + Grafana (API 在宿主机调试)..."
        docker compose up -d postgres grafana
        Write-Host ""
        Write-Host "下一步: python boiler_api.py   (另开终端: .\manage.ps1 sim)"
    }
    "api" {
        Write-Host "宿主机启动 Flask API (开发模式, Ctrl+C 停止)..."
        python boiler_api.py
    }
    "down" {
        Write-Host "停止所有 Docker 服务..."
        docker compose down
    }
    "logs" {
        docker compose logs -f
    }
    "db" {
        docker exec -it boiler-postgres psql -U postgres -d postgres
    }
    "test" {
        Write-Host "发送一条正常工况测试数据 (T=120 L=50% P=1.0MPa)..."
        try {
            $resp = Invoke-RestMethod -Uri "http://localhost:5000/api/sensor-data" `
                -Method Post -ContentType "application/json" `
                -Body '{"temper":120,"high":50,"press":1000000}'
            Write-Host "响应 (应返回 safe 及三阀门指令):"
            $resp | ConvertTo-Json -Depth 5
        } catch {
            Write-Host "请求失败: $_"
            Write-Host "请确认 API 已启动 (.\manage.ps1 up 或 .\manage.ps1 api)"
        }
    }
    "sim" {
        Write-Host "启动传感器模拟器 (Ctrl+C 停止)..."
        python sensor_simulator.py
    }
    "status" {
        docker compose ps
    }
}
