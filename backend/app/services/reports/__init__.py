"""报告域（FR-05-03/04）服务出口。"""
from .render import FingerprintMismatch, TemplateNotRegistered
from .service import (
    Locked,
    NotFound,
    ProjectFrozen,
    apply_note_approved,
    countersign_report,
    finalize_report,
    generate_note_draft,
    get_report_file,
    list_report_ai_runs,
    list_report_templates,
    list_report_trails,
    list_reports,
    sign_report,
)

__all__ = [
    "FingerprintMismatch",
    "Locked",
    "NotFound",
    "ProjectFrozen",
    "TemplateNotRegistered",
    "apply_note_approved",
    "countersign_report",
    "finalize_report",
    "generate_note_draft",
    "get_report_file",
    "list_report_ai_runs",
    "list_report_templates",
    "list_report_trails",
    "list_reports",
    "sign_report",
]
