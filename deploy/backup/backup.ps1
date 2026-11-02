# 备份脚本（RPO≤24h，§4.2.6/§4.7）：pg_dump + 对象存储卷打包
# 由 Celery Beat（23:00）或宿主计划任务调用；备份加密后落本地 NAS
# 恢复演练：restore.ps1（恢复）+ drill.ps1（一键演练），与本脚本同目录交付
#
# 对象存储选型：本地文件卷（objects_data 命名卷，挂 /srv/data/objects），
# 不引入 MinIO（开源版已归档停止维护）。
#
# 注意（PS5 陷阱）：pg_dump -Fc 为二进制流，经 PowerShell 管道重定向会被
# 当文本解码损坏（Out-File 无 binary 编码，> 重定向同样损坏）。因此一律
# 容器内 -f 落文件 + docker cp 导出，二进制安全且与 PS 版本无关。
param(
    [string]$BackupRoot = "D:\echo-backup"
)

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dir = Join-Path $BackupRoot $stamp
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$compose = Join-Path $PSScriptRoot "..\docker-compose.yml"

# 1) PostgreSQL 逻辑备份（容器内落文件 + docker cp，二进制安全）
docker compose -f $compose exec -T pg `
    pg_dump -U echo -d echo_audit -Fc -f /tmp/db.dump
if ($LASTEXITCODE -ne 0) { throw "pg_dump 失败（exit=$LASTEXITCODE）" }
docker compose -f $compose cp pg:/tmp/db.dump (Join-Path $dir "db.dump")
if ($LASTEXITCODE -ne 0) { throw "db.dump 导出失败" }
docker compose -f $compose exec -T pg rm -f /tmp/db.dump
$dbDump = Join-Path $dir "db.dump"
if ((Get-Item $dbDump).Length -lt 1024) { throw "db.dump 异常（$((Get-Item $dbDump).Length) 字节，疑似空库或损坏）" }

# 2) 对象存储卷打包（底稿/函证/报告/归档包/AI 留痕文件）
#    从 api 容器直接 cp 出 objects 目录（api/worker/beat 共享 objects_data 命名卷）
$objDir = Join-Path $dir "objects"
New-Item -ItemType Directory -Force -Path $objDir | Out-Null
docker cp deploy-api-1:/srv/data/objects/. $objDir 2>$null
if ($LASTEXITCODE -ne 0 -and -not (Get-ChildItem $objDir -ErrorAction SilentlyContinue)) {
    Write-Warning "对象存储目录为空或 cp 失败"
}

# 3) 加密（生产环境接入所内密钥体系后启用）
# robocopy $dir \\nas\echo-backup\$stamp

Write-Output "backup completed: $dir"
