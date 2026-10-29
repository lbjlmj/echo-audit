"""命名引擎（共享规则 R6，FR-01-03、§15-12）——唯一实现点。

规则：「项目编号+委托方+年份+类别」，类别简称枚举配置化；
命名由系统执行，人工不可改写。
"""
from __future__ import annotations

# 类别简称枚举（PRD §15-12，配置资产）
CATEGORY_CODES: dict[str, str] = {
    "CJ": "业绩类底稿",   # 占位说明，实际类别语义以 templates/ 准则映射总表为准
    "JH": "计划类",
    "FX": "风险类",
    "NK": "内控类",
    "DG": "底稿类",
    "HZ": "函证类",
    "BG": "报告类",
    "ZK": "专项类",
    "GD": "归档类",
    "QT": "其他",
}


class NamingError(ValueError):
    pass


def build_document_name(project_no: str, client_name: str, year: int, category: str) -> str:
    if category not in CATEGORY_CODES:
        raise NamingError(f"非法类别简称：{category}，合法枚举：{list(CATEGORY_CODES)}")
    if not project_no or not client_name:
        raise NamingError("项目编号与委托方名称不可为空")
    if not (2000 <= year <= 2100):
        raise NamingError(f"年份异常：{year}")
    # 系统生成，人工不可改写：文件名一经生成即登记留痕（由 audit_middleware 落 AuditTrail）
    return f"{project_no}-{client_name}-{year}-{category}"
