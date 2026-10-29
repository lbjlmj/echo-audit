/** quality 质控域 API（/quality；QU-1xxx）：FR-06 检查/阶段门/归档 + FR-06-05 检查应对。
 * AI 检查第三段复核标记复用 worksheet.ts 的 reviewAiRun（/worksheets/ai-runs/{run_no}/review）。 */
import { http } from './client'
import type {
  ArchiveOut,
  ArchiveVerifyOut,
  CorrectionArtifactOut,
  CorrectionOut,
  CorrectionRevisionOut,
  GateOut,
  InspectionPackOut,
  PrecheckOut,
  ProjectOptionOut,
  QcCheckOut,
  QcRunOut,
  RearchiveOut,
} from './types'

/** 质控驾驶舱项目清单（archive.access ≥ VIEW，含已归档/终止——归档调阅场景） */
export function listArchiveProjects() {
  return http.get<never, ProjectOptionOut[]>('/quality/projects')
}

/** 发起质控 AI 检查（FR-06-01：产物为线索态，archive.access ≥ OPERATE） */
export function runQcCheck(projectId: string) {
  return http.post<never, QcCheckOut>('/quality/qc-checks', { project_id: projectId })
}

export function listQcChecks(projectId: string) {
  return http.get<never, QcRunOut[]>('/quality/qc-checks', { params: { project_id: projectId } })
}

/** 检查应对材料包（FR-06-05：锚点反查 + 质控报表，一键出包） */
export function buildInspectionPack(projectId: string) {
  return http.post<never, InspectionPackOut>('/quality/inspection-pack', { project_id: projectId })
}

/** 阶段门通过（仅 实质性/完成/报告 可手动过门，其余由业务域审批流自动串联） */
export function passGate(stage: string, projectId: string) {
  return http.post<never, GateOut>(`/quality/gates/${stage}/pass`, { project_id: projectId })
}

/** 阶段门跳过（R3：合伙人批准 + 质控专员双批） */
export function skipGate(stage: string, projectId: string, qcReviewerId: string) {
  return http.post<never, GateOut>(`/quality/gates/${stage}/skip`, {
    project_id: projectId,
    qc_reviewer_id: qcReviewerId,
  })
}

export function listGates(projectId: string) {
  return http.get<never, GateOut[]>('/quality/gates', { params: { project_id: projectId } })
}

/** 归档前完整性校验（FR-06-02：阻断清单） */
export function precheckArchive(projectId: string) {
  return http.post<never, PrecheckOut>('/quality/archive/precheck', { project_id: projectId })
}

/** 一键归档（FR-06-04：校验→封包哈希→双状态机收口，archive.access ≥ OPERATE） */
export function archiveProject(projectId: string) {
  return http.post<never, ArchiveOut>('/quality/archive', { project_id: projectId })
}

/** 下载归档实包（zip：底稿/报告产物+AI留痕+manifest+SHA256SUMS；未归档 409） */
export function downloadArchivePackage(projectId: string) {
  return http.get<never, Blob>(`/quality/archive/${projectId}/package`, { responseType: 'blob' })
}

/** 归档包防篡改校验（FR-06-04：成员哈希逐项比对+包库自证；失败 422 QU-1009） */
export function verifyArchivePackage(projectId: string) {
  return http.post<never, ArchiveVerifyOut>(`/quality/archive/${projectId}/verify`)
}

// ---------------------------------------------------------------------------
// 差错归档 v(n+1)（§15-16）：登记→合伙人批准→副本修订（版本链追加）→质控复核→重新归档
// ---------------------------------------------------------------------------

/** 差错单清单（archive.access ≥ VIEW） */
export function listCorrections(projectId: string) {
  return http.get<never, CorrectionOut[]>('/quality/corrections', {
    params: { project_id: projectId },
  })
}

/** 差错登记（质控专员，项目须已归档；在途单唯一，409 QU-1010） */
export function registerCorrection(projectId: string, description: string, worksheetIds: string[]) {
  return http.post<never, CorrectionOut>('/quality/corrections', {
    project_id: projectId,
    description,
    affected_worksheet_ids: worksheetIds,
  })
}

/** 合伙人批准/驳回（驳回必附原因；批准即解锁副本修订并落版本基线快照） */
export function approveCorrection(correctionId: string, approve: boolean, reason?: string) {
  return http.post<never, CorrectionOut>(`/quality/corrections/${correctionId}/approve`, {
    approve,
    reason,
  })
}

/** 修订产物受控上传（§15-16：raw bytes 直传对象存储，内容寻址键；返回 file_key 凭以登记版本链） */
export function uploadCorrectionArtifact(
  correctionId: string,
  worksheetId: string,
  filename: string,
  content: ArrayBuffer,
) {
  return http.post<never, CorrectionArtifactOut>(
    `/quality/corrections/${correctionId}/artifacts`,
    content,
    {
      params: { worksheet_id: worksheetId, filename },
      headers: { 'Content-Type': 'application/octet-stream' },
    },
  )
}

/** 修订登记（经理/审计员）：受影响底稿追加 v(n+1) 版本，原稿版本与 v(n) 归档包锁定保留 */
export function registerCorrectionRevision(
  correctionId: string,
  worksheetId: string,
  fileKey: string,
  changeSummary: string,
) {
  return http.post<never, CorrectionRevisionOut>(
    `/quality/corrections/${correctionId}/revisions`,
    { worksheet_id: worksheetId, file_key: fileKey, change_summary: changeSummary },
  )
}

/** 提交质控复核（逐稿校验已超批准时点版本基线） */
export function submitCorrectionReview(correctionId: string) {
  return http.post<never, CorrectionOut>(`/quality/corrections/${correctionId}/submit-review`)
}

/** 质控复核（质控专员；通过→复核通过，退回必附意见→修订中） */
export function qcReviewCorrection(correctionId: string, conclusion: '通过' | '退回', opinion?: string) {
  return http.post<never, CorrectionOut>(`/quality/corrections/${correctionId}/qc-review`, {
    conclusion,
    opinion,
  })
}

/** 重新归档 v(n+1)：新整包 supersedes 旧包哈希，旧包 WORM 不动，双哈希留痕 */
export function rearchiveProject(correctionId: string) {
  return http.post<never, RearchiveOut>(`/quality/corrections/${correctionId}/rearchive`)
}
