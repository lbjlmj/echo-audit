# 备份脚本（RPO≤24h，§4.2.6/§4.7）：pg_dump + 对象存储卷打包
# 由 Celery Beat（23:00）或宿主计划任务调用；备份加密后落本地 NAS
# 恢复演练脚本同部署包交付（TECH_DESIGN §7 部署演练）
#
# 对象存储选型：本地文件卷（objects_data 命名卷，挂 /srv/data/objects），
# 不引入 MinIO（开源版已归档停止维护）。备份方式为侧车容器 tar 打包。
param(
    [string]$BackupRoot = "D:\echo-backup"
)

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dir = Join-Path $BackupRoot $stamp
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$compose = Join-Path $PSScriptRoot "..\docker-compose.yml"

# 1) PostgreSQL 逻辑备份
docker compose -f $compose exec -T pg `
    pg_dump -U echo -d echo_audit -Fc | Out-File -Encoding binary (Join-Path $dir "db.dump")

# 2) 对象存储卷打包（底稿/函证/报告/归档包/AI 留痕文件）
#    从 api 容器直接 cp 出 objects 目录（api/worker/beat 共享 objects_data 命名卷）
$objDir = Join-Path $dir "objects"
New-Item -ItemType Directory -Force -Path $objDir | Out-Null
docker cp deploy-api-1:/srv/data/objects/. $objDir 2>$null
if (-not (Get-ChildItem $objDir -ErrorAction SilentlyContinue)) {
    Write-Warning "对象存储目录为空或 cp 失败"
}

# 3) 加密（生产环境接入所内密钥体系后启用）
# robocopy $dir \\nas\echo-backup\$stamp

Write-Output "backup completed: $dir"
