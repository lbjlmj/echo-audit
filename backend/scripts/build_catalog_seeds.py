"""科目目录种子重建：CAS 总目录 xlsx → worksheet_catalog.json（数据即代码工具链）。

背景：第三批生成的 CAS 种子漏解析「（二）实质性程序」段（55 条无挂载）；
本脚本全量重解析总目录，4xxx 条目挂载 templates/cas/{code}.xlsx（manifest
键存在才挂），重建完整树。SME 种子（worksheet_catalog_sme.json）已完整不重建。

结构与 SME 种子同构（前端两准则树形态一致）：
- 顶级组：业务完成(6)/初步(1)/风险评估(2)/其他(5)/进一步审计程序(None)
- 「进一步审计程序底稿」下挂「（一）控制测试工作底稿」子组(stage=3)
- 「（二）实质性程序工作底稿」独立顶级组(stage=4)，下挂「N.类」科目类子组
- 编号列 int/str 混排（6000 数字、Z001 文本），统一 str 归一。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
SRC = (
    ROOT / "templates" / "_staging" / "中小会计师事务所审计底稿(企业会计准则)"
    / "审计工作底稿目录（企业会计准则版）.xlsx"
)
DEST = ROOT / "data" / "worksheet_catalog.json"

STAGE_BY_SECTION = {
    "业务完成阶段工作底稿": 6,
    "初步业务活动工作底稿": 1,
    "风险评估工作底稿": 2,
    "其他项目": 5,
}

_RE_ENTRY = re.compile(r"^(?:\d{4}|Z\d{3})$")
_RE_SUBGROUP = re.compile(r"^[（(][一二三四五六][）)]")
_RE_CLASS = re.compile(r"^\d\.[\u4e00-\u9fa5]+$")


def _text(v: object) -> str:
    """单元格值 → 归一文本（int 编号 6000 → '6000'）。"""
    if v is None:
        return ""
    return str(v).strip()


def build() -> dict:
    wb = load_workbook(SRC)
    ws = wb.worksheets[0]
    groups: list[dict] = []
    current_group: dict | None = None
    current_sub: dict | None = None

    for row in ws.iter_rows(min_row=5, max_col=2, values_only=True):
        col_a, col_b = _text(row[0]), _text(row[1])
        if not col_a and not col_b:
            continue
        # 一级段标题（一、二、…）
        m = re.match(r"^([一二三四五六七八九十]+)、(.+)$", col_a)
        if m:
            section = m.group(2)
            current_group = {
                "name": section,
                "stage": STAGE_BY_SECTION.get(section),
                "subgroups": [], "items": [],
            }
            current_sub = None
            groups.append(current_group)
            continue
        if current_group is None:
            continue
        # （一）/（二）子块：控制测试挂「进一步审计程序」组下；实质性程序升独立顶级组
        if _RE_SUBGROUP.match(col_a):
            if "实质性程序" in col_a:
                current_group = {"name": col_a, "stage": 4, "subgroups": [], "items": []}
                current_sub = None
                groups.append(current_group)
            else:
                current_sub = {"name": col_a, "stage": 3, "items": []}
                current_group["subgroups"].append(current_sub)
            continue
        # 「N.类」科目类子组 → 当前顶级组（实质性程序组）下
        if _RE_CLASS.match(col_a):
            current_sub = {"name": col_a, "stage": 4, "items": []}
            current_group["subgroups"].append(current_sub)
            continue
        # 条目行：编号 + 名称
        if _RE_ENTRY.match(col_a) and col_b:
            stage = current_sub["stage"] if current_sub else current_group["stage"]
            item = {"code": col_a, "title": col_b, "stage": stage}
            (current_sub or current_group)["items"].append(item)
    wb.close()

    # 挂载模板键：4xxx → templates/cas/{code}.xlsx（manifest 键存在才挂）
    manifest = json.loads(
        (ROOT / "templates" / "worksheet" / "manifest.json").read_text(encoding="utf-8")
    )

    def _mount(items: list[dict]) -> None:
        for it in items:
            if re.match(r"^4\d{3}$", it["code"]):
                key = f"templates/cas/{it['code']}.xlsx"
                if key in manifest:
                    it["template_key"] = key

    for g in groups:
        _mount(g["items"])
        for sub in g["subgroups"]:
            _mount(sub["items"])

    return {"standard": "企业会计准则", "version": "cas2025", "groups": groups}


def main() -> None:
    seed = build()
    mounted = 0

    def _count(group: dict) -> int:
        return (
            sum(1 for it in group["items"] if it.get("template_key"))
            + sum(1 for s in group["subgroups"] for it in s["items"] if it.get("template_key"))
        )

    total = 0
    for g in seed["groups"]:
        n = len(g["items"]) + sum(len(s["items"]) for s in g["subgroups"])
        total += n
        mounted += _count(g)
        subs = [f"{s['name']}({len(s['items'])})" for s in g["subgroups"]]
        print(f"  {g['name']}: {n} 条，子组 {subs}")
    DEST.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"重建 {DEST.name}：{total} 条，挂载 {mounted} 张模板")


if __name__ == "__main__":
    main()
