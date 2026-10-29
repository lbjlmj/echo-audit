/** knowledge 知识域 API（/knowledge；KN-1xxx）——FR-07-01 准则 RAG 问答。
 * 知识条目审批链（§15-3）/ Skill 统一管理（FR-07-03）/ 知识图谱（FR-07-02）
 * 端点已在后端就绪，前端对接随后续批次补充。 */
import { http } from './client'
import type { QaHistoryOut, QaOut } from './types'

/** 准则问答（FR-07-01：任一角色；项目上下文必填——问答留痕归属项目） */
export function askQa(projectId: string, question: string) {
  return http.post<never, QaOut>('/knowledge/qa', { project_id: projectId, question })
}

/** 问答历史（append-only 留痕回放，问答记录可追溯） */
export function qaHistory(projectId: string) {
  return http.get<never, QaHistoryOut[]>('/knowledge/qa', { params: { project_id: projectId } })
}
