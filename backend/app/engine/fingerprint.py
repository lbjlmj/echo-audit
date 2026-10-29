"""模板结构指纹（硬约束 8，FR-03-02 保真写回）——唯一实现点。

Excel 底稿写回前后必须做结构指纹校验：公式区哈希 + 数据验证规则清单，
不一致即失败回滚——模板公式与下拉框在编制过程中不可被改写（FR-03-02「公式不改、下拉框不发挥」）。
纯 openpyxl 遍历实现，无新增依赖（硬约束 10）；MinIO 文件流接线后由底稿服务在版本落链前后调用。
"""
from __future__ import annotations

import hashlib
import json

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet as XLWorksheet


class FingerprintError(ValueError):
    pass


def _sheet_fingerprint(ws: XLWorksheet) -> dict[str, object]:
    """单表结构要素：公式区（坐标→公式文本）+ 数据验证规则清单。"""
    formulas: dict[str, str] = {}
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                formulas[cell.coordinate] = cell.value
    validations = sorted(
        f"{dv.type}|{dv.formula1}|{dv.sqref}" for dv in ws.data_validations.dataValidation
    )
    return {"formulas": formulas, "validations": validations}


def compute_fingerprint(workbook: Workbook) -> dict[str, str]:
    """结构指纹：逐表计算 SHA256（键=工作表名）。"""
    result: dict[str, str] = {}
    for name in workbook.sheetnames:
        raw = json.dumps(_sheet_fingerprint(workbook[name]), ensure_ascii=False, sort_keys=True)
        result[name] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return result


def verify_structure(baseline: dict[str, str], current: dict[str, str]) -> None:
    """写回校验：结构不一致抛 FingerprintError（调用方必须失败回滚，硬约束 8）。"""
    if baseline.keys() != current.keys():
        raise FingerprintError(
            f"工作表集合变动：基线 {sorted(baseline)} ≠ 当前 {sorted(current)}"
        )
    changed = sorted(s for s in baseline if baseline[s] != current[s])
    if changed:
        raise FingerprintError(f"模板结构指纹不一致（公式区/验证规则被改写）：{changed}")
