# Echo — 中小事务所智能审计作业系统

将审计作业流程（立项 → 计划 → 底稿 → 函证 → 复核 → 质控归档）数字化，以「数字员工」形态提供 AI 辅助，符合中国审计准则与监管要求。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Element Plus + Vite + Pinia |
| 后端 | Python 3.13 + FastAPI + SQLAlchemy 2.0 + Pydantic v2 |
| 存储 | PostgreSQL 16 + pgvector、Redis 7、MinIO |
| 异步 | Celery + Celery Beat |
| 部署 | Docker Compose 七容器，支持全离线安装 |

## 仓库结构

```
echo-audit/
├── frontend/          # Vue 3 前端
├── backend/
│   ├── app/
│   │   ├── api/v1/    # 路由层（薄）
│   │   ├── core/      # config / security(JWT+RBAC) / audit_middleware
│   │   ├── models/    # SQLAlchemy ORM
│   │   ├── schemas/   # Pydantic DTO
│   │   ├── services/  # 业务逻辑层（厚），按 PRD 七大 FR 域分包
│   │   ├── engine/    # 阶段门 + 状态机 + R5/R8
│   │   ├── ai/        # 六大 AI 模块 + pipeline
│   │   ├── workers/   # Celery 任务
│   │   └── integrations/ # 外部适配器（出站白名单）
│   ├── templates/     # 行业模板矩阵（数据即代码）
│   ├── data/          # 科目目录种子（CAS/SME）
│   └── tests/
├── deploy/            # docker-compose / 离线安装 / 备份
└── docs/
```

## 快速开始

### 后端

```powershell
cd backend
uv sync
alembic upgrade head
uvicorn app.main:app --reload
pytest
```

### 前端

```powershell
cd frontend
pnpm install
pnpm dev
```

### 部署

```powershell
docker compose -f deploy/docker-compose.yml up -d
```

## 文档

- `AGENTS.md` — 编码规范、硬约束、命令速查（开发必读）
- `Echo——中小事务所智能审计作业系统产品需求文档（PRD）v1.2.md` — 需求与业务规则唯一来源
- `Echo审计作业平台技术设计文档（TECH_DESIGN）v1.1.md` — 技术方案唯一来源

## 合规红线

- 状态流转只走 `engine/`
- AI 只产出线索，必须经人工复核落库
- AI 调用强制留痕 + 哈希链
- 权限以后端 RBAC 为准
- 出站网络默认全关闭
- Excel 模板写回前后结构指纹校验
