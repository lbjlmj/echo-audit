/** reviews 复核工作台域 API（/reviews；RV-1xxx）：队列 + 时间线，只读视图。
 * 复核动作（受理/结论/终审）一律走 worksheet.ts 的 /worksheets 端点（硬约束 2/9）。 */
import { http } from './client'
import type { PendingReviewOut, ReviewTimelineOut } from './types'

export function pendingQueue() {
  return http.get<never, PendingReviewOut[]>('/reviews/pending')
}

export function reviewTimeline(worksheetId: string) {
  return http.get<never, ReviewTimelineOut>(`/reviews/worksheets/${worksheetId}/timeline`)
}
