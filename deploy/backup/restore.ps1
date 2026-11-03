# 恢复脚本（TECH_DESIGN §4.7：备份与恢复演练脚本随部署包交付；RTO 目标 ≤4h）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File restore.ps1 -BackupDir D:\echo-backup\20261101-120000
#
# 流程（七步，任一步失败即中止，不产生半恢复状态）：
#   1. 校验备份目录（db.dump + objects/）
#   2. 起基础设施（pg/redis），停应用容器（api/worker/beat）防恢复期间写入
#   3. PostgreSQL 还原：断残留连接 → dropdb → createdb → pg_restore（-Fc 自定义格式）
#   4. objects 卷还原：清空命名卷 → 回灌备份（含文件数核对）
#   5. 起全部服务（migrate 容器对已恢复的 alembic_version 幂等无操作）
#   6. 验证：API 健康 + 库内行数清点
#
# 注意：
# - 向现存卷恢复时，WORM（chattr +a）归档包会阻断卷清空——此时应
#   docker compose down -v 销毁卷后做全量恢复（drill.ps1 即此口径）
# - 备份产物经 Windows/NTFS 中转，chattr 属性不随 cp 保留：恢复后的归档包
#   WORM 锁不自动重加（内容哈希不变，防篡改校验仍可通过）；如需重锁，
#   在 api 容器内对归档包执行 chattr +a 或走重新归档链
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupDir
)

$ErrorActionPreference = "Continue"  # 原生命令 stderr 不中断脚本；安全靠逐条 exit code 检查
$BackupDir = (Resolve-Path $BackupDir).Path
$compose = Join-Path $PSScriptRoot "..\docker-compose.yml"

function Run-Docker {
    param([string[]]$ArgList, [string]$Fail)
    $out = & docker @ArgList 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$Fail (exit=$LASTEXITCODE)`n$out" }
    return $out
}

# —— 1) 备份目录校验 ——
$dbDump = Join-Path $BackupDir "db.dump"
$objSrc = Join-Path $BackupDir "objects"
if (-not (Test-Path $dbDump)) { throw "备份缺失：$dbDump" }
if (-not (Test-Path $objSrc)) { throw "备份缺失：$objSrc" }
$objSrcCount = (Get-ChildItem $objSrc -Recurse -File).Count
Write-Output ("[1/7] 备份校验通过：db.dump={0} 字节，objects={1} 个文件" -f (Get-Item $dbDump).Length, $objSrcCount)

# —— 2) 基础设施就绪 + 停应用容器 ——
Run-Docker @("compose", "-f", $compose, "up", "-d", "--wait", "pg", "redis") "pg/redis 启动失败"
& docker compose -f $compose stop api worker beat 2>$null | Out-Null
$running = & docker compose -f $compose ps --status running --format "{{.Service}}" 2>$null
if ($running -contains "api") { throw "api 容器未能停止，拒绝在写入活动期恢复" }
Write-Output "[2/7] 基础设施就绪，应用容器已停止"

# —— 3) PostgreSQL 还原 ——
Run-Docker @("compose", "-f", $compose, "exec", "-T", "pg", "psql", "-U", "echo", "-d", "postgres", "-c",
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='echo_audit' AND pid <> pg_backend_pid();"
) "断开 echo_audit 残留连接失败"
Run-Docker @("compose", "-f", $compose, "exec", "-T", "pg", "dropdb", "-U", "echo", "--if-exists", "echo_audit") "dropdb 失败"
Run-Docker @("compose", "-f", $compose, "exec", "-T", "pg", "createdb", "-U", "echo", "echo_audit") "createdb 失败"
Run-Docker @("compose", "-f", $compose, "cp", $dbDump, "pg:/tmp/db.dump") "db.dump 载入 pg 容器失败"
Run-Docker @("compose", "-f", $compose, "exec", "-T", "pg", "pg_restore", "-U", "echo", "-d", "echo_audit", "--no-owner", "/tmp/db.dump") "pg_restore 失败"
Run-Docker @("compose", "-f", $compose, "exec", "-T", "pg", "rm", "-f", "/tmp/db.dump") "清理容器内临时 dump 失败"
Write-Output "[3/7] PostgreSQL 还原完成"

# —— 4) objects 卷还原（侧车容器直写命名卷）——
#      侧车用 redis:7-alpine（部署必在场镜像，离线环境零新增拉取）
Run-Docker @("compose", "-f", $compose, "create", "api") "创建 api 容器（连带 objects_data 卷）失败"
# 定位容器用 label 过滤（compose ps 不支持 --filter service=，实测 unknown filter）
$cid = (& docker ps -a --filter "label=com.docker.compose.service=api" --format "{{.ID}}" 2>$null | Select-Object -First 1)
if (-not $cid) { throw "无法定位 api 容器" }
# 挂载解析用无引号模板（Go 模板内双引号经 PS 传参会丢失，eq 比较会报
# "unexpected / in operand"），输出「卷名=容器内路径;」对在 PS 侧拆解
$mounts = (& docker inspect $cid --format "{{range .Mounts}}{{.Name}}={{.Destination}};{{end}}" 2>$null | Out-String).Trim()
$vol = $null
foreach ($pair in ($mounts -split ";")) {
    if (-not $pair) { continue }
    $mName, $mDest = $pair -split "=", 2
    if ($mDest -eq "/srv/data/objects") { $vol = $mName; break }
}
if (-not $vol) { throw "无法定位 objects_data 卷（api 容器无 /srv/data/objects 挂载）：$mounts" }

& docker run --rm -v "${vol}:/data" redis:7-alpine sh -c "rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null; true" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "objects 卷清空命令失败 (exit=$LASTEXITCODE)" }
$leftover = (& docker run --rm -v "${vol}:/data" redis:7-alpine sh -c "ls -A /data | wc -l" 2>$null | Out-String).Trim()
if ([int]$leftover -gt 0) {
    throw "objects 卷清空后仍有 ${leftover} 项残留——若为 WORM(chattr +a) 归档包，请先 docker compose down -v 销毁卷后全量恢复"
}
& docker run --rm -v "${vol}:/data" -v "${BackupDir}:/backup:ro" redis:7-alpine sh -c "cp -a /backup/objects/. /data/" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "objects 卷回灌失败 (exit=$LASTEXITCODE)" }
$volCount = (& docker run --rm -v "${vol}:/data" redis:7-alpine sh -c "find /data -type f | wc -l" 2>$null | Out-String).Trim()
if ([int]$volCount -ne $objSrcCount) { throw "objects 文件数核对不一致：备份 ${objSrcCount} vs 卷 ${volCount}" }
Write-Output "[4/7] objects 卷还原完成（${volCount} 个文件核对一致）"

# —— 5) 起全部服务 ——
Run-Docker @("compose", "-f", $compose, "up", "-d") "服务启动失败"
Write-Output "[5/7] 服务已启动（migrate 对已恢复的 alembic_version 幂等无操作）"

# —— 6) API 健康验证（最长 90 秒）——
$ok = $false
foreach ($i in 1..30) {
    try {
        $h = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 3
        if ($h.status -eq "ok") { $ok = $true; break }
    } catch { Start-Sleep -Seconds 3 }
}
if (-not $ok) { throw "API 健康检查未通过（90 秒内 /health 未就绪）" }
Write-Output "[6/7] API 健康检查通过"

# —— 7) 库内行数清点（供演练比对/人工核对）——
$sql = "SELECT 'users='||(SELECT count(*) FROM users)" +
       "||' clients='||(SELECT count(*) FROM clients)" +
       "||' projects='||(SELECT count(*) FROM audit_projects)" +
       "||' worksheets='||(SELECT count(*) FROM worksheets)" +
       "||' versions='||(SELECT count(*) FROM worksheet_versions)" +
       "||' trails='||(SELECT count(*) FROM audit_trails)" +
       "||' airuns='||(SELECT count(*) FROM ai_runs)" +
       "||' confirmations='||(SELECT count(*) FROM confirmations)" +
       "||' reports='||(SELECT count(*) FROM audit_reports)"
$counts = (& docker compose -f $compose exec -T pg psql -U echo -d echo_audit -t -A -c $sql 2>$null | Out-String).Trim()
if (-not $counts) { throw "库内行数清点失败（psql 无输出）" }
Write-Output "[7/7] 库内清点：$counts"
Write-Output "restore completed: $BackupDir"
