# 备份与恢复（TECH_DESIGN §4.7：RPO≤24h、RTO≤4h）

随部署包交付三份脚本，均需在宿主（部署目录任意位置）以 PowerShell 执行：

| 脚本 | 用途 | 用法 |
| --- | --- | --- |
| `backup.ps1` | 全量备份：pg_dump（-Fc）+ objects 卷导出 | `powershell -File backup.ps1 -BackupRoot D:\echo-backup` |
| `restore.ps1` | 全量恢复：停应用容器 → 库还原 → objects 卷回灌 → 起服务 → 验证 | `powershell -File restore.ps1 -BackupDir D:\echo-backup\<时间戳>` |
| `drill.ps1` | 恢复演练（真实灾难模拟）：备份 → 采集指纹 → `down -v` 销毁全部数据卷 → 恢复 → 逐项比对 + 登录冒烟 → PASS/FAIL | `powershell -File drill.ps1 -TestLogin admin:admin123` |

## 关键口径

- **二进制安全**：pg_dump 一律容器内 `-f` 落文件 + `docker cp` 导出（PowerShell 5 管道重定向会损坏二进制流，禁止 `| >` 落盘）
- **对象存储**：本地文件卷 `objects_data`（挂 `/srv/data/objects`，api/worker/beat 共享）；恢复用 `redis:7-alpine` 侧车直写命名卷（部署必在场镜像，离线零新增拉取）
- **幂等迁移**：恢复后 `up -d` 时 migrate 容器对已恢复的 `alembic_version` 幂等无操作
- **WORM 注意**：向现存卷恢复时 `chattr +a` 归档包会阻断卷清空（脚本会明确报错）——此时应 `down -v` 后全量恢复；备份经 NTFS 中转不保留 chattr 属性，恢复后归档包内容哈希不变、防篡改校验仍通过
- **redis 不备份**：纯易失态（TECH_DESIGN §1.4），销毁后随 `up -d` 重建
- **生产建议**：备份加密后落本地 NAS（脚本第 3 步预留 robocopy 位）；每季度至少跑一次 `drill.ps1` 实演练

## 演练结果留档

2026-11-03 首次真实演练：备份 → `down -v` 销毁全部数据卷 → 恢复 → 比对 **PASS**
- 库内 9 表行数灾前/灾后逐项一致；objects 文件数一致且 SHA256 字节级一致
- 登录冒烟 + 业务数据可读（底稿列表）通过；恢复实测分钟级（远优于 RTO≤4h 目标）
- 演练中发现并修复两处脚本缺陷（见提交记录）：compose 不支持 `--filter service=`、
  Go 模板双引号经 PS 传参丢失

2026-11-03 二次完整演练（修复后单次全流程通过）：**PASS**，RTO 实测 20 秒
- 六步全过：备份 → 灾前指纹 → `down -v` 销毁 → 恢复 → 9 表行数 + objects 文件数比对一致 → 登录冒烟（admin JWT 签发）
- 修复第三处脚本缺陷：登录冒烟条件误用 `-contains`（PS 数组运算符，字符串恒 `$false`）导致该步恒跳过，
  已改为 `.Contains(":")`
