# 备份脚本（RPO≤24h，§4.2.6/§4.7）：pg_dump + MinIO 同步
# 由 Celery Beat（23:00）或宿主计划任务调用；备份加密后落本地 NAS
# 恢复演练脚本同部署包交付（TECH_DESIGN §7 部署演练）
param(
    [string]$BackupRoot = "D:\echo-backup"
)

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dir = Join-Path $BackupRoot $stamp
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# 1) PostgreSQL 逻辑备份
docker compose -f (Join-Path $PSScriptRoot "..\docker-compose.yml") exec -T pg `
    pg_dump -U echo -d echo_audit -Fc | Out-File -Encoding binary (Join-Path $dir "db.dump")

# 2) MinIO 对象同步（归档包/AI 留痕文件）
docker compose -f (Join-Path $PSScriptRoot "..\docker-compose.yml") exec -T minio `
    sh -c "mc alias set local http://localhost:9000 `"` && mc mirror --overwrite /data /backup/minio" 2>$null

# 3) 加密（生产环境接入所内密钥体系后启用）
# robocopy $dir \\nas\echo-backup\$stamp

Write-Output "backup completed: $dir"
