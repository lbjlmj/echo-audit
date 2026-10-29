/** confirmations 函证域 API（/confirmations；CF-1xxx）：FR-04 台账/发出/回函核对/替代程序/闭环。 */
import { http } from './client'
import type { CfLedgerItemBody, CfTrailOut, ConfirmationOut } from './types'

/** 函证台账列表（worksheet.edit ≥ VIEW；质控/合伙人只读可见） */
export function listConfirmations(projectId: string) {
  return http.get<never, ConfirmationOut[]>('/confirmations', { params: { project_id: projectId } })
}

/** 生成函证台账（FR-04-01：台账项进入「台账已生成」） */
export function generateLedger(projectId: string, items: CfLedgerItemBody[]) {
  return http.post<never, ConfirmationOut[]>('/confirmations', { project_id: projectId, items })
}

/** 发出函证（FR-04-02：登记运单号→「已发出」；P0 人工登记，适配器沙箱联调后自动回写） */
export function issueCf(id: string, waybillNo: string) {
  return http.post<never, ConfirmationOut>(`/confirmations/${id}/issue`, { waybill_no: waybillNo })
}

/** 回函录入并自动核对（FR-04-03：一致→核对一致；差异→差异处理，一次录入连转两态） */
export function replyCf(id: string, body: { reply_amount: number; reply_doc: string }) {
  return http.post<never, ConfirmationOut>(`/confirmations/${id}/reply`, body)
}

/** 执行替代程序（FR-04-04：超期未回/差异处理→替代程序） */
export function altCf(id: string, altTestDoc: string) {
  return http.post<never, ConfirmationOut>(`/confirmations/${id}/alt`, { alt_test_doc: altTestDoc })
}

/** 函证闭环（核对一致/替代程序→闭环；全闭环为门 5 准入条件） */
export function closeCf(id: string) {
  return http.post<never, ConfirmationOut>(`/confirmations/${id}/close`)
}

/** 单张函证留痕时间线（含 Beat 超期扫描记录，所见即所查） */
export function listCfTrails(id: string) {
  return http.get<never, CfTrailOut[]>(`/confirmations/${id}/trails`)
}
