"""科目目录树（数据即代码种子 → 前端科目树数据源，FR-03 底稿工作台）。

种子随版本发布于 backend/data/（worksheet_catalog.json 企业准则 132 条挂 77、
worksheet_catalog_sme.json 小企业准则 99 条挂 49），解析为三级结构：
阶段组（groups）→ 子组（subgroups，如「进一步审计程序底稿（一）控制测试」）
→ 科目条目（items，4xxx 实质性程序科目挂 template_key 指向模板矩阵键）。

科目模板键 → Template 行的登记口径（type, version 唯一约束）：
type = 「科目编号 科目名」（如 4101 货币资金）、version = 准则版本
（cas2025/sme2025）、skeleton_key = 模板矩阵键。创建底稿传 template_key
时按此幂等 get-or-create，不与行业模板矩阵（type=行业）冲突。
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from ...models.knowledge import Template

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# 准则 → 种子文件（随版本发布；键与 AuditProject.business_type 准则口径一致）
CATALOG_FILES: dict[str, str] = {
    "企业会计准则": "worksheet_catalog.json",
    "小企业会计准则": "worksheet_catalog_sme.json",
}


def _load_seed(standard: str) -> dict:
    path = DATA_DIR / CATALOG_FILES[standard]
    if not path.exists():
        # 种子随版本发布应恒存在；缺失属部署缺陷，不静默降级（宁可 5xx 不给错树）
        raise RuntimeError(f"科目目录种子缺失：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_catalog(standard: str | None = None) -> list[dict]:
    """科目目录树（只读，无库副作用）。

    standard=None 返回两准则全部；指定准则返回单树。返回种子原结构
    （standard/version/groups），由路由层 Pydantic Schema 序列化。
    未知准则 → ValueError（路由 422）。
    """
    standards = [standard] if standard else list(CATALOG_FILES)
    if standard is not None and standard not in CATALOG_FILES:
        raise ValueError(f"未知会计准则：{standard}（可选：{'、'.join(CATALOG_FILES)}）")
    return [_load_seed(s) for s in standards]


def resolve_template_by_key(db: Session, template_key: str) -> Template:
    """科目模板键 → Template 行（幂等 get-or-create）。

    键形如 templates/cas/4101.xlsx / templates/sme/4101.xlsx；type 取
    「科目编号 科目名」（种子 items 查得），version 取准则版本。
    键不在种子里 → ValueError（不允自造模板键绕过发布矩阵）。
    """
    standard = "小企业会计准则" if "/sme/" in template_key else "企业会计准则"
    version = "sme2025" if standard == "小企业会计准则" else "cas2025"
    item = _find_item(template_key, _load_seed(standard))
    if item is None:
        raise ValueError(f"模板键不在科目目录种子中：{template_key}")
    type_name = f"{item['code']} {item['title']}"
    row = db.query(Template).filter_by(type=type_name, version=version).first()
    if row is not None:
        return row
    row = Template(type=type_name, version=version, skeleton_key=template_key)
    db.add(row)
    db.flush()
    return row


def _find_item(template_key: str, seed: dict) -> dict | None:
    """种子树内按 template_key 查科目条目（groups → subgroups → items）。"""
    def _walk(groups: list[dict]) -> dict | None:
        for g in groups:
            for it in g.get("items", []):
                if it.get("template_key") == template_key:
                    return it
            found = _walk(g.get("subgroups", []))
            if found is not None:
                return found
        return None

    return _walk(seed.get("groups", []))
