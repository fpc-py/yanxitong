// Pinia：系统能力（沙箱/防线/追踪）、实时指标、知识库 —— 概览页工作台数据源
// 约定：轮询与加载类请求失败一律静默降级（console.warn），不弹错；
// 用户主动触发的上传/删除由调用组件自行 toast。

import { onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'
import api, { extractErrorMessage } from '@/api/client'
import type { KnowledgeFileItem, KnowledgeUploadResult, MetricsSummary, SystemCapabilities } from '@/api/types'

export const useSystemStore = defineStore('system', () => {
  // ---- 系统能力 ----
  const capabilities = ref<SystemCapabilities | null>(null)
  const capLoading = ref(false)

  // ---- 实时指标 ----
  const metrics = ref<MetricsSummary | null>(null)
  const metricsLoading = ref(false)

  // ---- 知识库 ----
  const kbFiles = ref<KnowledgeFileItem[]>([])
  const kbLoading = ref(false)

  // ---- 轮询（metrics 15s / capabilities 60s，同一 store 只保留一组 timer） ----
  const pollingOn = ref(false)
  const METRICS_INTERVAL = 15_000
  const CAP_INTERVAL = 60_000
  let metricsTimer: number | null = null
  let capTimer: number | null = null

  async function loadCapabilities(): Promise<void> {
    capLoading.value = true
    try {
      capabilities.value = await api.getCapabilities()
    } catch (e) {
      console.warn('[system] capabilities 获取失败：', extractErrorMessage(e))
    } finally {
      capLoading.value = false
    }
  }

  async function loadMetrics(): Promise<void> {
    if (metricsLoading.value) return // 上一轮尚未返回时跳过，避免请求堆积
    metricsLoading.value = true
    try {
      metrics.value = await api.getMetricsSummary()
    } catch (e) {
      console.warn('[system] metrics 获取失败：', extractErrorMessage(e))
    } finally {
      metricsLoading.value = false
    }
  }

  async function loadKnowledgeFiles(): Promise<void> {
    kbLoading.value = true
    try {
      kbFiles.value = await api.getKnowledgeFiles()
    } catch (e) {
      console.warn('[system] 知识库列表获取失败：', extractErrorMessage(e))
    } finally {
      kbLoading.value = false
    }
  }

  /** 上传知识库文档（library=team 共享库需登录）；失败抛出，由调用方 ElMessage 提示 */
  async function uploadKnowledge(file: File, library: 'team' | 'personal' = 'personal'): Promise<KnowledgeUploadResult> {
    try {
      const res = await api.uploadKnowledge(file, library)
      await loadKnowledgeFiles()
      return res
    } catch (e) {
      console.warn('[system] 知识库上传失败：', extractErrorMessage(e))
      throw e
    }
  }

  /** 删除知识库文件（team 共享库仅上传者可删）；成功返回 true，失败返回 false（调用方可选择提示） */
  async function removeKnowledge(name: string, library: 'team' | 'personal' = 'personal'): Promise<boolean> {
    try {
      await api.deleteKnowledgeFile(name, library)
      await loadKnowledgeFiles()
      return true
    } catch (e) {
      console.warn('[system] 知识库删除失败：', extractErrorMessage(e))
      return false
    }
  }

  function startPolling(): void {
    if (pollingOn.value) return
    pollingOn.value = true
    // 立即拉取一次，避免首屏空白
    void loadMetrics()
    void loadCapabilities()
    metricsTimer = window.setInterval(() => void loadMetrics(), METRICS_INTERVAL)
    capTimer = window.setInterval(() => void loadCapabilities(), CAP_INTERVAL)
  }

  function stopPolling(): void {
    pollingOn.value = false
    if (metricsTimer !== null) {
      window.clearInterval(metricsTimer)
      metricsTimer = null
    }
    if (capTimer !== null) {
      window.clearInterval(capTimer)
      capTimer = null
    }
  }

  // store 被销毁时兜底清理（正常路径由视图 onUnmounted 调 stopPolling）
  onScopeDispose(stopPolling)

  return {
    // state
    capabilities,
    capLoading,
    metrics,
    metricsLoading,
    kbFiles,
    kbLoading,
    pollingOn,
    // actions
    loadCapabilities,
    loadMetrics,
    loadKnowledgeFiles,
    uploadKnowledge,
    removeKnowledge,
    startPolling,
    stopPolling,
  }
})
