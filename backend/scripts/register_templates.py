"""模板登记扫描器：真实模板 → manifest.json（fill_cells + structure_fingerprint）。

数据即代码工具链（随版本发布执行，不参与运行时）：
1. 扫描模板每个工作表的「标签：值」表头要素位（被审计单位/项目/编制/复核/日期/
   截止日/索引号/编号/致），登记标签右侧空白格为 fill_cells 白名单；
2. 含「××」占位符的格子同样登记（询证函抬头、正文留空位）；
3. 逐表计算结构指纹（engine/fingerprint 单点）写入 structure_fingerprint；
4. 与现有 manifest 合并（同键覆盖），--check 模式只比对不写入（CI 指纹回归）；
5. --batch-cas：扫描模板源材料区（_staging）实质性程序目录，科目编号即键
   （templates/cas/{code}.xlsx），批量复制入库并登记（第三批 70 张底稿口径）。

口径：仅登记表头要素与占位符格——数据表格区（明细行）不在白名单，
行级写回待 FR-03-02 明细取数二期扩展；公式区与数据验证零触碰由指纹闸保证。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

from app.engine.fingerprint import compute_fingerprint

TEMPLATES_ROOT = Path(__file__).resolve().parents[1] / "templates"


def _staging_dir(name_keyword: str, *, exclude: str = "") -> Path:
    """模板源材料区包目录（zip 解压区，.gitignore 忽略；仅构建期使用）。

    按关键字匹配；exclude 排除（如「企业会计准则」须排除「小企业」——
    后者包名含前者子串，不排除会匹配两个包）。
    """
    root = TEMPLATES_ROOT / "_staging"
    if not root.exists():
        raise SystemExit(f"模板源材料区缺失：{root}")
    packs = [
        p for p in root.iterdir()
        if p.is_dir() and name_keyword in p.name and not (exclude and exclude in p.name)
    ]
    if len(packs) != 1:
        raise SystemExit(f"关键字「{name_keyword}」应恰匹配一个模板包：{[p.name for p in root.iterdir() if p.is_dir()]}")
    return packs[0]

# 表头要素标签（去空白后包含即命中）；右侧相邻格为登记目标
_LABELS = ("被审计单位", "项目", "编制", "复核", "日期", "截止日", "索引号", "编号", "致")
# 占位符标记：模板预留在正文中的待填位
_PLACEHOLDER_MARKS = ("××", "×××")


def _label_of(value: object) -> str | None:
    """表头要素标签（去空白后以「标签：」或裸标签命中）→ 标签名。"""
    if not isinstance(value, str):
        return None
    text = value.replace("\u3000", "").replace(" ", "")
    for label in _LABELS:
        if text.startswith(f"{label}：") or text == label:
            return label
    return None


def _label_hit(value: object) -> bool:
    return _label_of(value) is not None


def _placeholder_hit(value: object) -> bool:
    return isinstance(value, str) and any(mark in value for mark in _PLACEHOLDER_MARKS)


def scan_template(path: Path) -> tuple[dict[str, list[str]], dict[str, str], dict[str, dict[str, str]]]:
    """模板文件 → (fill_cells, structure_fingerprint, header_map)。

    header_map：每表的「标签 → 值位坐标」，供底稿 AI 初稿写回按语义定位。
    值位 = 标签右侧第一个独立格（跳过标签合并延伸的 MergedCell——如
    「复核：」占 F4:G4 合并、真值位在 H4 值区锚点；B2/G2 等 MergedCell
    写入会落到标签锚点覆写标签，绝不可登记）；预填值格（如索引号 4101）
    不登记，模板预填保留。占位模板（无标签）无 header_map，调用方兜底。
    """
    from openpyxl.cell.cell import MergedCell

    wb = load_workbook(path)
    fill_cells: dict[str, list[str]] = {}
    header_map: dict[str, dict[str, str]] = {}
    for ws in wb.worksheets:
        coords: list[str] = []
        seen: set[str] = set()
        labels: dict[str, str] = {}
        for row in ws.iter_rows():
            for cell in row:
                if _placeholder_hit(cell.value):
                    # 占位符格（××抬头/留空位）：自身即渲染覆写目标
                    if cell.coordinate not in seen:
                        coords.append(cell.coordinate)
                        seen.add(cell.coordinate)
                    continue
                label = _label_of(cell.value)
                if label is None:
                    continue
                # 向右跳过标签合并延伸（MergedCell），取第一个独立格为值位
                col = cell.column + 1
                target = ws.cell(row=cell.row, column=col)
                while isinstance(target, MergedCell):
                    col += 1
                    target = ws.cell(row=cell.row, column=col)
                if target.value is None and target.coordinate not in seen:
                    coords.append(target.coordinate)
                    seen.add(target.coordinate)
                    # 同标签先到先记（编制日期 B5 先于复核日期 G5）
                    labels.setdefault(label, target.coordinate)
        if coords:
            fill_cells[ws.title] = sorted(coords, key=lambda c: (int("".join(filter(str.isdigit, c)) or 0), c))
        if labels:
            header_map[ws.title] = labels
    fingerprints = compute_fingerprint(wb)
    wb.close()
    return fill_cells, fingerprints, header_map


def register(entries: dict[str, dict], *, check_only: bool) -> None:
    """entries：模板键 → {label, version, file}。逐个扫描并登记/比对。"""
    changed: list[str] = []
    for key, meta in entries.items():
        manifest_path = TEMPLATES_ROOT / meta["domain"] / "manifest.json"
        manifest: dict = {}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = TEMPLATES_ROOT / meta["domain"] / meta["file"]
        if not path.exists():
            raise SystemExit(f"模板文件缺失：{path}")
        fill_cells, fingerprints, header_map = scan_template(path)
        entry = {
            **meta,
            "fill_cells": fill_cells,
            "header_map": header_map,
            "structure_fingerprint": fingerprints,
        }
        old = manifest.get(key)
        if old == entry:
            continue
        changed.append(key)
        if check_only:
            continue
        manifest[key] = entry
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    action = "指纹比对差异" if check_only else "已登记"
    print(f"{action}: {changed if changed else '无（与 manifest 一致）'}")


def collect_cas_entries() -> tuple[dict[str, dict], list[str]]:
    """扫描源材料区实质性程序目录：科目编号文件 → 批量登记条目。

    返回（entries, skipped）：无编号前缀的文件（如「凭证测试表（通用）」）跳过并报告。
    """
    src = _staging_dir("企业会计准则", exclude="小企业") / "4实质性程序工作底稿（企业会计准则版）"
    if not src.exists():
        raise SystemExit(f"实质性程序目录缺失：{src}")
    entries: dict[str, dict] = {}
    skipped: list[str] = []
    for f in sorted(src.glob("*.xlsx")):
        m = re.match(r"^(4\d{3})", f.name)
        if not m:
            skipped.append(f.name)
            continue
        code = m.group(1)
        label = re.sub(rf"^{code}\s*", "", f.stem)
        entries[f"templates/cas/{code}.xlsx"] = {
            "label": f"{label}（企业会计准则底稿）",
            "version": "cas2025",
            "file": f"cas/{code}.xlsx",
            "domain": "worksheet",
            "src": f,
        }
    return entries, skipped


def collect_sme_entries() -> tuple[dict[str, dict], list[str]]:
    """扫描小企业准则包实质性程序目录：4xxx 科目底稿 → 批量登记条目。

    仅接实质性程序域（4xxx）；阶段表与函证子包不在本批，若与企业版版式
    有差异须以独立键区分后按需接入。
    """
    pkg = _staging_dir("小企业会计准则")
    subs = [d for d in pkg.iterdir() if d.is_dir() and d.name.startswith("5")]
    if len(subs) != 1:
        raise SystemExit(f"实质性程序目录匹配异常：{[d.name for d in pkg.iterdir() if d.is_dir()]}")
    src = subs[0]
    entries: dict[str, dict] = {}
    skipped: list[str] = []
    for f in sorted(src.glob("*.xlsx")):
        m = re.match(r"^(4\d{3})", f.name)
        if not m:
            skipped.append(f.name)
            continue
        code = m.group(1)
        label = re.sub(rf"^{code}\s*", "", f.stem)
        entries[f"templates/sme/{code}.xlsx"] = {
            "label": f"{label}（小企业会计准则底稿）",
            "version": "sme2025",
            "file": f"sme/{code}.xlsx",
            "domain": "worksheet",
            "src": f,
        }
    return entries, skipped


def _batch_register(entries: dict[str, dict], subdir: str) -> None:
    """批量入库通用：源材料复制到 worksheet/{subdir}/ → manifest 登记。"""
    dest_dir = TEMPLATES_ROOT / "worksheet" / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    for meta in entries.values():
        dest = dest_dir / meta["file"].split("/")[-1]
        if not dest.exists():
            shutil.copyfile(meta.pop("src"), dest)
        else:
            meta.pop("src")
    register(entries, check_only=False)
    print(f"worksheet/{subdir}/ 现有模板文件: {len(list(dest_dir.glob('*.xlsx')))} 张")


def collect_sme_domain_entries() -> dict[str, dict]:
    """小企业准则域级模板 → 差异化独立键条目（计划/复核/核对表/往来函）。

    银行询证函为监管统一版式（两准则包文件字节一致）共用 bank/v2025.xlsx，
    不在本批；4xxx 科目底稿已由 --batch-sme 批量入库。
    """
    pkg = _staging_dir("小企业会计准则")
    done_src = pkg / "6业务完成阶段工作底稿（小企业会计准则版）"
    ar_src = (
        pkg / "5实质性程序工作底稿 （小企业会计准则版）" / "询证函格式汇总及函证控制程序底稿"
        / "常用询证函格式汇总" / "5 往来账项询证函.xlsx"
    )
    entries: dict[str, dict] = {
        "plan-sme/v2025.xlsx": {
            "label": "审计计划（小企业会计准则版）",
            "version": "sme2025",
            "file": "plan_sme_v2025.xlsx",
            "domain": "planning",
            "src": pkg / "2风险评估工作底稿（小企业会计准则版）" / "2600 审计计划.xlsx",
        },
        "review-sme/v2025.xlsx": {
            "label": "复核工作底稿（小企业会计准则版）",
            "version": "sme2025",
            "file": "review_sme_v2025.xlsx",
            "domain": "review",
            "src": done_src / "6700 复核工作底稿.xlsx",
        },
        "checklist-sme/v2025.xlsx": {
            "label": "审计工作完成情况核对表（小企业会计准则版）",
            "version": "sme2025",
            "file": "checklist_sme_v2025.xlsx",
            "domain": "quality",
            "src": done_src / "6600 审计工作完成情况核对表.xlsx",
        },
        "ar-sme/v2025.xlsx": {
            "label": "往来账项询证函（小企业会计准则版）",
            "version": "sme2025",
            "file": "ar_sme_v2025.xlsx",
            "domain": "confirmation",
            "src": ar_src,
        },
    }
    for meta in entries.values():
        if not meta["src"].exists():
            raise SystemExit(f"域级模板缺失：{meta['src']}")
    return entries


def batch_sme_domain() -> None:
    """小企业域级模板差异化入库：源材料复制到各自域目录 → manifest 登记。"""
    entries = collect_sme_domain_entries()
    for meta in entries.values():
        dest = TEMPLATES_ROOT / meta["domain"] / meta["file"]
        if not dest.exists():
            shutil.copyfile(meta.pop("src"), dest)
        else:
            meta.pop("src")
    register(entries, check_only=False)


def batch_cas() -> None:
    """企业准则批量入库：源材料 → cas/ 复制 → manifest 登记。"""
    entries, skipped = collect_cas_entries()
    if skipped:
        print(f"跳过（无科目编号前缀）: {skipped}")
    _batch_register(entries, "cas")


def batch_sme() -> None:
    """小企业准则批量入库：源材料 → sme/ 复制 → manifest 登记。"""
    entries, skipped = collect_sme_entries()
    if skipped:
        print(f"跳过（非 4xxx 科目底稿）: {len(skipped)} 个文件")
    _batch_register(entries, "sme")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只比对指纹不写 manifest（CI 用）")
    parser.add_argument("--batch-cas", action="store_true",
                        help="批量入库企业准则实质性程序科目底稿（编号即键）")
    parser.add_argument("--batch-sme", action="store_true",
                        help="批量入库小企业准则 4xxx 科目底稿（编号即键）")
    parser.add_argument("--batch-sme-domain", action="store_true",
                        help="小企业准则域级模板差异化入库（计划/复核/核对表/往来函独立键）")
    args = parser.parse_args()

    if args.batch_cas:
        batch_cas()
        return
    if args.batch_sme:
        batch_sme()
        return
    if args.batch_sme_domain:
        batch_sme_domain()
        return

    first_batch = {
        # 底稿域：企业会计准则首批（科目编号即键，skeleton_key 同步 Template 登记；
        # label 与 --batch-cas 批量登记同口径，保证 --check 指纹回归不因元数据误报）
        "templates/cas/4101.xlsx": {
            "label": "货币资金（企业会计准则底稿）",
            "version": "cas2025",
            "file": "cas/4101.xlsx",
            "domain": "worksheet",
        },
        # 函证域首批：询证函两式 + 控制三表
        "bank/v2025.xlsx": {"label": "银行询证函（财政银行业监管标准版式）", "version": "v2025", "file": "bank_v2025.xlsx", "domain": "confirmation"},
        "ar/v2025.xlsx": {"label": "往来账项询证函（积极式+消极式）", "version": "v2025", "file": "ar_v2025.xlsx", "domain": "confirmation"},
        "control/v2025.xlsx": {"label": "函证程序控制措施表", "version": "v2025", "file": "control_v2025.xlsx", "domain": "confirmation"},
        "send_list/v2025.xlsx": {"label": "发函清单", "version": "v2025", "file": "send_list_v2025.xlsx", "domain": "confirmation"},
        "reply_check/v2025.xlsx": {"label": "回函核对记录", "version": "v2025", "file": "reply_check_v2025.xlsx", "domain": "confirmation"},
        # 第二批：计划/复核/质控域出稿载体
        "plan/v2025.xlsx": {"label": "审计计划（总体策略+具体计划+重要性+计划矩阵）", "version": "v2025", "file": "plan_v2025.xlsx", "domain": "planning"},
        "review/v2025.xlsx": {"label": "复核工作底稿（业务复核核对表+三级复核表）", "version": "v2025", "file": "review_v2025.xlsx", "domain": "review"},
        "checklist/v2025.xlsx": {"label": "审计工作完成情况核对表（质控检查报告载体）", "version": "v2025", "file": "checklist_v2025.xlsx", "domain": "quality"},
    }
    register(first_batch, check_only=args.check)


if __name__ == "__main__":
    main()
