/** worksheets 底稿域 API（/worksheets；WS-1xxx）：FR-03 AI 初稿三段式 + 六状态流转。 */
import { http } from './client'
import type {
  AiDraftOut,
  AiReviewBody,
  AnchorOut,
  CrossRefsOut,
  ProjectOptionOut,
  ReviewBody,
  TemplateOptionOut,
  WorksheetCreateBody,
  WorksheetOut,
} from './types'

/** 底稿工作台可选项目（编制人无 project.approve，/projects 403，用本视图） */
export function listProjectOptions() {
  return http.get<never, ProjectOptionOut[]>('/worksheets/projects')
}

export function listWorksheets(projectId: string) {
  return http.get<never, WorksheetOut[]>('/worksheets', { params: { project_id: projectId } })
}

export function listAnchors() {
  return http.get<never, AnchorOut[]>('/worksheets/anchors')
}

export function listTemplates() {
  return http.get<never, TemplateOptionOut[]>('/worksheets/templates')
}

export function listAiRuns(worksheetId: string) {
  return http.get<never, AiDraftOut[]>(`/worksheets/${worksheetId}/ai-runs`)
}

export function createWorksheet(projectId: string, body: WorksheetCreateBody) {
  return http.post<never, WorksheetOut>(`/worksheets?project_id=${encodeURIComponent(projectId)}`, body)
}

/** AI 初稿生成（FR-03-02：三层材料齐备才解锁，产物恒为线索态） */
export function generateAiDraft(worksheetId: string) {
  return http.post<never, AiDraftOut>(`/worksheets/${worksheetId}/ai-draft`)
}

/** AI 输出人工复核标记（红线 1/R2：采信才落版本链） */
export function reviewAiRun(runNo: string, body: AiReviewBody) {
  return http.post<never, AiDraftOut>(`/worksheets/ai-runs/${runNo}/review`, body)
}

export function submitWorksheet(worksheetId: string) {
  return http.post<never, WorksheetOut>(`/worksheets/${worksheetId}/submit`)
}

export function takeReview(worksheetId: string) {
  return http.post<never, WorksheetOut>(`/worksheets/${worksheetId}/review/take`)
}

export function concludeReview(worksheetId: string, body: ReviewBody) {
  return http.post<never, WorksheetOut>(`/worksheets/${worksheetId}/review/conclude`, body)
}

/** 终审（复核通过→终审通过：仅重要底稿，§15-11，worksheet.review_sign=APPROVE） */
export function finalApprove(worksheetId: string) {
  return http.post<never, WorksheetOut>(`/worksheets/${worksheetId}/final-approve`)
}

/** 交叉索引写入（FR-03-05：草稿态全量覆盖保存，自动校验断链 R7） */
export function updateCrossRefs(worksheetId: string, refs: { code: string; note: string }[]) {
  return http.put<never, CrossRefsOut>(`/worksheets/${worksheetId}/cross-refs`, { refs })
}

/** 下载当前版本产物（AI 初稿采信后的模板保真写回 xlsx；无版本 → 409） */
export function downloadWorksheetFile(worksheetId: string) {
  return http.get<never, Blob>(`/worksheets/${worksheetId}/file`, { responseType: 'blob' })
}
