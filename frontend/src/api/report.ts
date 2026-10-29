/** reports 报告域 API（/reports；RP-1xxx）：FR-05-03 附注 AI + FR-05-04 审批流三步。 */
import { http } from './client'
import type { AiDraftOut, NoteDraftOut, ReportOut, ReportTemplateOut, ReportTrailOut } from './types'

/** 项目报告清单（登录可读，全程状态可见） */
export function listReports(projectId: string) {
  return http.get<never, ReportOut[]>('/reports', { params: { project_id: projectId } })
}

/** 发起附注 AI（FR-05-03：完成门通过后经理发起，产物为线索态） */
export function generateNoteDraft(projectId: string) {
  return http.post<never, NoteDraftOut>('/reports/notes-draft', { project_id: projectId })
}

/** 报告的附注 AI 运行线索（第三段采信数据源；采信复用 /worksheets/ai-runs/{run_no}/review） */
export function listReportAiRuns(reportId: string) {
  return http.get<never, AiDraftOut[]>(`/reports/${reportId}/ai-runs`)
}

/** 已登记报告模板清单（FR-05-04 出稿下拉数据源，数据即代码随版本发布） */
export function listReportTemplates() {
  return http.get<never, ReportTemplateOut[]>('/reports/templates')
}

/** 报告模板出稿（FR-05-04：附注采信为前置；模板保真写回+结构指纹校验，经理执行） */
export function finalizeReport(reportId: string, templateKey: string) {
  return http.post<never, ReportOut>(`/reports/${reportId}/finalize`, { template_key: templateKey })
}

/** 下载出稿产物（保真写回的 xlsx；未出稿 → 422） */
export function downloadReportFile(reportId: string) {
  return http.get<never, Blob>(`/reports/${reportId}/file`, { responseType: 'blob' })
}

/** 质控会签（report.sign ≥ OPERATE：出稿后草稿→会签，意见必填留痕） */
export function countersignReport(reportId: string, opinion: string) {
  return http.post<never, ReportOut>(`/reports/${reportId}/countersign`, { opinion })
}

/** 合伙人签发（report.sign=APPROVE：须先会签；升级未裁决分歧阻签 FR-05-05） */
export function signReport(reportId: string) {
  return http.post<never, ReportOut>(`/reports/${reportId}/sign`)
}

/** 单份报告留痕时间线（附注发起/采信/出稿/会签/签发全程可追溯） */
export function listReportTrails(reportId: string) {
  return http.get<never, ReportTrailOut[]>(`/reports/${reportId}/trails`)
}
