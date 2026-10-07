// 审阅问题的展示映射（写作页/审阅页共用）
import type { ReviewIssue, ReviewIssueStatus, ReviewIssueType, ReviewSeverity } from '@/api/types'

export const ISSUE_TYPE_LABELS: Record<ReviewIssueType, string> = {
  citation_format: '引用格式',
  expression: '学术表达',
  data_integrity: '数据完整性',
  logic: '逻辑一致性',
  ai_disclosure: 'AI 标注',
  similarity: '查重重合',
  other: '其他',
}

export const SEVERITY_LABELS: Record<ReviewSeverity, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

export const STATUS_LABELS: Record<ReviewIssueStatus, string> = {
  open: '待修',
  suggest: '建议',
  fixable: '可自动修复',
}

/** 问题类型 pill 的配色类（对应主题 .pill-tag.g/.a/.r/.b） */
export function typeClass(type: ReviewIssueType): string {
  if (type === 'citation_format') return 'a'
  if (type === 'ai_disclosure' || type === 'similarity') return 'r'
  if (type === 'expression') return 'b'
  return ''
}

/** 状态 pill 的配色类：待修=red / 建议=amber / 可自动修复=green */
export function statusClass(status: ReviewIssueStatus): string {
  if (status === 'open') return 'r'
  if (status === 'suggest') return 'a'
  return 'g'
}

/** 定位文案：「§章节 第 N 段」/「第 N 段」/「未定位到原文」 */
export function issueLocation(issue: ReviewIssue): string {
  if (!issue.located || issue.para == null) return '未定位到原文'
  const prefix = issue.section ? `§${issue.section} ` : ''
  return `${prefix}第 ${issue.para} 段`
}
