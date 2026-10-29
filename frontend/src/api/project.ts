/** projects 立项域 API（/projects；PR-1xxx）。 */
import { http } from './client'
import type {
  ClientOut,
  IndustryTemplateOut,
  IntakeSubmitBody,
  MaterialityOut,
  ProjectOut,
  TeamOverviewOut,
} from './types'

export function listProjects() {
  return http.get<never, ProjectOut[]>('/projects')
}

export function listClients() {
  return http.get<never, ClientOut[]>('/projects/clients')
}

export function submitIntake(body: IntakeSubmitBody) {
  return http.post<never, ProjectOut>('/projects', body)
}

export function approveProject(id: string) {
  return http.post<never, ProjectOut>(`/projects/${id}/approve`)
}

export function listIndustryTemplates() {
  return http.get<never, IndustryTemplateOut[]>('/projects/industry-templates')
}

export function selectIndustryTemplate(id: string, industryTemplate: string) {
  return http.post<never, ProjectOut>(`/projects/${id}/industry-template`, {
    industry_template: industryTemplate,
  })
}

export function configureTeamHours(
  id: string,
  members: { user_id: string; project_role?: string; budget_hours?: number; actual_hours?: number }[],
) {
  return http.put<never, TeamOverviewOut>(`/projects/${id}/team-hours`, { members })
}

export function teamOverview(id: string) {
  return http.get<never, TeamOverviewOut>(`/projects/${id}/team`)
}

/** 查询重要性水平（R8：plan.confirm ≥ VIEW；未设置 404） */
export function getMateriality(id: string) {
  return http.get<never, MaterialityOut>(`/projects/${id}/materiality`)
}

/** 录入/修改重要性水平（FR-02-01：R8 经 engine 计算，修改留痕并重置报批；plan.confirm ≥ OPERATE） */
export function saveMateriality(
  id: string,
  body: {
    benchmark_type: string
    basis_amount: number
    exec_ratio_pct: number
    trivial_ratio_pct: number
  },
) {
  return http.post<never, MaterialityOut>(`/projects/${id}/materiality`, body)
}

/** 重要性水平报批确认（R8：随计划报合伙人审批，联动计划门条件；plan.confirm=APPROVE） */
export function approveMateriality(id: string) {
  return http.post<never, MaterialityOut>(`/projects/${id}/materiality/approve`)
}
