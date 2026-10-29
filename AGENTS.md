# AGENTS.md — Echo 审计作业平台

本文件面向 AI 编码代理（自动读取）。它定义在本仓库工作时必须遵守的命令、结构与硬约束。

## 项目概述

Echo——中小事务所智能审计作业系统。将审计作业流程（立项→计划→底稿→函证→复核→质控归档）数字化，以"数字员工"形态提供 AI 辅助，符合中国审计准则与监管要求。

**文档基线（改代码前先读对应章节）：**

- `Echo——中小事务所智能审计作业系统产品需求文档（PRD）v1.2.md` — 需求与业务规则唯一来源：§4 对象与状态机、§5 功能需求行为链、§7 共享规则 R1–R8、§11 合规红线、§15 决策记录 17 项
- `Echo审计作业平台技术设计文档（TECH_DESIGN）v1.1.md` — 技术方案唯一来源：§2 项目结构与接口域、§3 数据模型、§4 关键技术、§5 分期交付
- 版本联动：PRD 变更须同步修订 TECH_DESIGN §6 两张映射表；两者冲突时以 PRD 为准

## 技术栈（不得擅自更换）

| 层 | 选型 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Element Plus + Vite + Pinia（薄前端，路由守卫仅兜底） |
| 后端 | Python 3.13 + FastAPI + SQLAlchemy 2.0 + Pydantic v2 |
| 存储 | PostgreSQL 16 + pgvector（一库两用，不引入独立向量库/图数据库）、Redis 7、MinIO |
| 异步 | Celery + Celery Beat |
| Excel | openpyxl 3.1.5 + xlrd（保真写回 + 结构指纹校验） |
| 算法 | 孤立森林/本福特定律纯 Python 零依赖实现（Phase 2 验收口径） |
| 大模型 | vLLM 私有化，默认关闭（见下方门禁） |
| 部署 | Docker Compose 七容器（web/api/worker/beat/pg/redis/minio），支持全离线安装 |

## 常用命令

```powershell
# 后端（backend/）
uv sync                                    # 安装依赖
alembic upgrade head                       # 数据库迁移
uvicorn app.main:app --reload              # 本地启动 API
pytest                                     # 运行测试（backend/tests/）
python skills/build_audit_zips.py          # 打包 Skill zip（版本化发布链）

# 前端（frontend/）
pnpm install
pnpm dev                                   # Vite 开发服务器
pnpm build                                 # 生产构建
pnpm lint                                  # ESLint + Prettier 检查

# 部署（deploy/）
docker compose -f deploy/docker-compose.yml up -d      # 七容器启动
docker compose -f deploy/docker-compose.yml logs -f api
```

注意：Windows PowerShell 5 环境不支持 `&&`，多命令用 `;` 分隔。

## 仓库结构（monorepo）

```
echo-audit/
├── frontend/          # Vue 3；views/ 六个界面严格对应 PRD §12
├── backend/
│   ├── app/
│   │   ├── api/v1/    # 路由层（薄）：只做鉴权与出入参
│   │   ├── core/      # config / security(JWT+权限码RBAC) / audit_middleware(留痕切面)
│   │   ├── models/    # SQLAlchemy ORM（TECH_DESIGN §3 数据模型）
│   │   ├── schemas/   # Pydantic DTO
│   │   ├── services/  # 业务逻辑层（厚），按 PRD 七大 FR 域分包
│   │   ├── engine/    # 全局规则引擎：阶段门 + 三个状态机 + R5 + R8
│   │   ├── ai/        # 六大 AI 模块 + pipeline 统一管道
│   │   ├── workers/   # Celery 任务与 Beat 定时任务
│   │   └── integrations/ # 外部适配器（顺丰等，一律走适配器 + 出站白名单）
│   ├── alembic/       # 迁移版本化，可回滚
│   ├── templates/     # 行业模板矩阵（数据即代码，随版本发布）
│   ├── mapping/       # 非标数据字段映射模板
│   ├── skills/        # Skill 资产 21 个（存量 16 + 新增 5）
│   └── tests/
├── deploy/            # docker-compose / offline-installer / backup
└── docs/
```

## 编码规范

- Python：`ruff` 格式化与检查；类型注解必须完整；ORM 模型与 Pydantic Schema 分离（models/ ≠ schemas/）
- TypeScript：strict 模式；API 调用按后端路由域分文件放 `src/api/`；组件命名 PascalCase
- 路由层薄、服务层厚：业务规则一律写在 `services/`，禁止在 `api/v1/` 写业务逻辑
- 错误码按域分段（如 WS-1xxx 底稿域）；状态变更类接口必须幂等（请求 ID + Redis 锁）
- 出入参一律 Pydantic Schema，禁止裸 dict 跨层传递
- 注释与标识符用中文业务术语时保持与 PRD 术语表（§16）一致

## 硬约束（违反任何一条即代码不可合并）

1. **去品牌化**：代码、注释、模板、文档中不得出现 PwC 及其受版权保护的模板内容、外部文档 ID。
2. **状态流转只走 engine**：底稿（草稿→待复核→复核中→复核通过→终审通过→已归档）、函证（八状态）、问题（六状态）、项目（四状态含暂停/终止）的全部流转必须经 `engine/`，禁止在 service 内直接改状态字段或自创状态名。
3. **AI 只产出线索**：AI 模块输出为 lead，不得直接写业务表；必须经 `ai/pipeline.py` 三段式（线索→人工复核标记→证据）落库。AI 输出无复核标记不可归档。
4. **AI 留痕强制**：所有 AI 调用写 10 字段留痕记录 + 哈希链（append-only）；审计日志不可更新删除。
5. **权限以后端为准**：10 个权限码 RBAC（`core/permissions.py` 配置化）；编制人=复核人拒绝（R1）；前端路由守卫仅兜底。
6. **大模型通道默认关闭**：vLLM 通道需满足三条件门禁才可启用；RAG 答案必须带准则锚点引用，无引用不出答案。
7. **出站网络白名单**：默认全关闭，仅放行已配置的适配器（首期仅顺丰 `integrations/sf_express.py`，预留 `ExpressAdapter` 接口）。
8. **模板保真**：Excel 底稿写回前后必须做结构指纹校验（公式区哈希 + 验证规则清单），不一致即失败回滚。
9. **共享规则不得重复实现**：R1–R8（双签、AI 留痕、阶段门、权限、风险分级、命名引擎、交叉索引、重要性水平）只在 engine/core 单点实现，services 只调用。
10. **零新增依赖须申报**：引入新第三方库需说明理由并更新本文件技术栈表；核心算法（孤立森林/本福特定律）保持纯 Python 零依赖。

## 测试要求

按 TECH_DESIGN §7 八类测试覆盖关键改动：状态机单测（含非法流转拦截）、模板指纹回归、权限矩阵断言、留痕完整性（哈希链整链验证）、AI 门禁负向用例、性能基准、部署演练、函证联调。改状态机、权限、留痕、AI 管道相关代码必须附带对应负向测试用例。

## 提交规范

- 提交信息：`<type>(<scope>): <中文描述>`，type ∈ feat/fix/refactor/test/docs/chore
- 涉及业务规则变动的提交，需在描述中注明对应 PRD 条款（如 R8、FR-04-02、§15-N）
- 禁止提交：密钥、真实客户数据、离线镜像 tar 包
