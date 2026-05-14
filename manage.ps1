param(
    [Parameter()]
    [ValidateSet("up", "down", "logs", "db", "test", "status", "sim")]
    [string]$Command = "status"
)

# 切换到脚本所在目录，确保 docker compose 能找到配置文件
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($ScriptDir) { Set-Location $ScriptDir }

switch ($Command) {
    "up" {
        Write-Host "启动 PostgreSQL + Grafana..."
        docker compose up -d
        Write-Host ""
        Write-Host "服务已启动:"
        Write-Host "  PostgreSQL : localhost:5432  (postgres / 123456)"
        Write-Host "  Grafana    : http://localhost:3000  (admin / admin)"
        Write-Host ""
        Write-Host "下一步: python boiler_api.py"
    }
    "down" {
        Write-Host "停止所有 Docker 服务..."
        docker compose down
    }
    "logs" {
        docker compose logs -f
    }
    "db" {
        docker exec -it boiler-postgres psql -U postgres
    }
    "test" {
        Write-Host "发送一条 HTTP 测试数据..."
        try {
            $resp = Invoke-RestMethod -Uri "http://localhost:5000/api/sensor-data" `
                -Method Post -ContentType "application/json" `
                -Body '{"temper":105,"high":12,"press":100001}'
            Write-Host "响应: $($resp | ConvertTo-Json -Depth 3)"
        } catch {
            Write-Host "请求失败: $_"
            Write-Host "请确认 Flask 服务已启动 (python boiler_api.py)"
        }
    }
    "sim" {
        Write-Host "启动传感器模拟器..."
        python sensor_simulator.py
    }
    "status" {
        docker compose ps
    }
}
