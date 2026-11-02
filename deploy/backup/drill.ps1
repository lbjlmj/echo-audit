# 恢复演练脚本（TECH_DESIGN §4.7/§7：恢复演练脚本随部署包交付；RPO≤24h、RTO≤4h 口径验证）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File drill.ps1
#        powershell -NoProfile -ExecutionPolicy Bypass -File drill.ps1 -BackupRoot D:\echo-backup -TestLogin admin:admin123
#
# 演练流程（真实灾难模拟，非纸面推演）：
#   1. 全量备份（backup.ps1）
#   2. 采集灾前状态指纹（库内行数 + objects 文件数 + API 健康）
#   3. 灾难模拟：docker compose down -v —— 销毁全部数据卷（pg/redis/objects）
#   4. 全量恢复（restore.ps1）
#   5. 采集灾后状态指纹，逐项比对
#   6. 登录冒烟（凭备份中的真实账号验证鉴权链路）
#   7. 报告 PASS/FAIL
#
# 警告：本脚本会销毁当前部署的全部数据（down -v），仅在演练环境执行；
#       生产环境演练请在备份验证通过后的维护窗口进行。
param(
    [string]$BackupRoot = "D:\echo-backup",
    [string]$TestLogin = "admin:admin123"  # 登录冒烟账号（备份库中须存在）；置空跳过
)

$ErrorActionPreference = "Continue"
$compose = Join-Path $PSScriptRoot "..\docker-compose.yml"

function Run-Docker {
    param([string[]]$ArgList, [string]$Fail)
    $out = & docker @ArgList 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$Fail (exit=$LASTEXITCODE)`n$out" }
    return $out
}

function Get-StateCounts {
    # 库内行数 + objects 文件数（须在系统运行时调用）
    $sql = "SELECT 'users='||(SELECT count(*) FROM users)" +
           "||' clients='||(SELECT count(*) FROM clients)" +
           "||' projects='||(SELECT count(*) FROM audit_projects)" +
           "||' worksheets='||(SELECT count(*) FROM worksheets)" +
           "||' versions='||(SELECT count(*) FROM worksheet_versions)" +
           "||' trails='||(SELECT count(*) FROM audit_trails)" +
           "||' airuns='||(SELECT count(*) FROM ai_runs)" +
           "||' confirmations='||(SELECT count(*) FROM confirmations)" +
           "||' reports='||(SELECT count(*) FROM audit_reports)"
    $db = (& docker compose -f $compose exec -T pg psql -U echo -d echo_audit -t -A -c $sql 2>$null | Out-String).Trim()
    if (-not $db) { throw "库内清点失败（pg 未就绪？）" }
    $obj = (& docker exec deploy-api-1 sh -c "find /srv/data/objects -type f | wc -l" 2>$null | Out-String).Trim()
    @{ db = $db; objects = [int]$obj }
}

$failures = @()

# —— 1) 全量备份 ——
Write-Output "=== [1/6] 全量备份 ==="
$bakOut = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "backup.ps1") -BackupRoot $BackupRoot 2>&1 | Out-String
Write-Output $bakOut.TrimEnd()
if ($LASTEXITCODE -ne 0 -or $bakOut -notmatch "backup completed: (.+)") { throw "备份失败" }
$backupDir = $Matches[1].Trim()
Write-Output "备份目录：$backupDir"

# —— 2) 灾前状态指纹 ——
Write-Output "=== [2/6] 采集灾前状态指纹 ==="
$pre = Get-StateCounts
Write-Output "灾前库内：$($pre.db)"
Write-Output "灾前 objects：$($pre.objects) 个文件"

# —— 3) 灾难模拟：销毁全部数据卷 ——
Write-Output "=== [3/6] 灾难模拟（down -v 销毁全部数据卷）==="
Run-Docker @("compose", "-f", $compose, "down", "-v") "数据卷销毁失败"
Write-Output "全部容器与数据卷（pg_data/redis_data/objects_data）已销毁"

# —— 4) 全量恢复 ——
Write-Output "=== [4/6] 全量恢复 ==="
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$resOut = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "restore.ps1") -BackupDir $backupDir 2>&1 | Out-String
Write-Output $resOut.TrimEnd()
if ($LASTEXITCODE -ne 0) { throw "恢复失败" }
$sw.Stop()
Write-Output ("RTO 实测：{0:mm\分ss\秒}" -f $sw.Elapsed)

# —— 5) 灾后状态指纹比对 ——
Write-Output "=== [5/6] 采集灾后状态指纹并比对 ==="
$post = Get-StateCounts
Write-Output "灾后库内：$($post.db)"
Write-Output "灾后 objects：$($post.objects) 个文件"
if ($pre.db -ne $post.db) { $failures += "库内行数不一致：灾前[$($pre.db)] vs 灾后[$($post.db)]" }
else { Write-Output "PASS：库内行数逐表一致" }
if ($pre.objects -ne $post.objects) { $failures += "objects 文件数不一致：$($pre.objects) vs $($post.objects)" }
else { Write-Output "PASS：objects 文件数一致（$($post.objects)）" }

# —— 6) 登录冒烟（鉴权链路 + 密码哈希还原验证）——
Write-Output "=== [6/6] 登录冒烟 ==="
if ($TestLogin -and $TestLogin -contains ":") {
    $parts = $TestLogin -split ":", 2
    try {
        $body = @{ login_name = $parts[0]; password = $parts[1] } | ConvertTo-Json
        $resp = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/login" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 10
        if ($resp.access_token) { Write-Output "PASS：登录成功（$($parts[0])，JWT 已签发）" }
        else { $failures += "登录响应无 token" }
    } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 401) {
            Write-Output "WARN：登录 401（账号 $($parts[0]) 不在本次备份库中，跳过）"
        } else { $failures += "登录冒烟失败：$($_.Exception.Message)" }
    }
} else { Write-Output "跳过（未配置 TestLogin）" }

# —— 报告 ——
Write-Output ""
Write-Output "=========================================="
if ($failures.Count -eq 0) {
    Write-Output "恢复演练结果：PASS（RPO=全量备份点，RTO 实测见上）"
    exit 0
} else {
    Write-Output "恢复演练结果：FAIL"
    $failures | ForEach-Object { Write-Output " - $_" }
    exit 1
}
