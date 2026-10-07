<script setup lang="ts">
// 矛盾与空白：矛盾检测（1 篇论文说 X，另一篇说 Y → 建议）+ 研究空白（KG 稀疏节点 + 局限性归纳）
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()
const conflicts = computed(() => store.conflicts)
const gaps = computed(() => store.researchGaps)

function verifiedLabel(v: boolean | undefined): string {
  return v ? '证据已核验' : '证据待核验'
}
</script>

<template>
  <div class="insights stack">
    <section class="panel">
      <header class="panel-head">
        <span class="panel-title serif">文献矛盾检测</span>
        <span class="panel-sub">{{ conflicts.length }} CONFLICTS</span>
      </header>
      <div class="panel-body">
        <div v-if="conflicts.length" class="cf-list">
          <article v-for="(c, i) in conflicts" :key="i" class="cf-item">
            <div class="cf-row">
              <div class="cf-side">
                <span class="cf-tag mono">A</span>
                <p class="cf-claim">{{ c.claim_a }}</p>
                <span class="cf-src">
                  <a v-if="c.source_a_url" :href="c.source_a_url" target="_blank" rel="noopener noreferrer">{{ c.source_a }}</a>
                  <span v-else>{{ c.source_a }}</span>
                  <span class="cf-badge" :class="{ ok: c.evidence_a_verified }">{{ verifiedLabel(c.evidence_a_verified) }}</span>
                </span>
                <p v-if="c.evidence_a" class="cf-ev">“{{ c.evidence_a }}”</p>
              </div>
              <span class="cf-vs serif">VS</span>
              <div class="cf-side">
                <span class="cf-tag mono">B</span>
                <p class="cf-claim">{{ c.claim_b }}</p>
                <span class="cf-src">
                  <a v-if="c.source_b_url" :href="c.source_b_url" target="_blank" rel="noopener noreferrer">{{ c.source_b }}</a>
                  <span v-else>{{ c.source_b }}</span>
                  <span class="cf-badge" :class="{ ok: c.evidence_b_verified }">{{ verifiedLabel(c.evidence_b_verified) }}</span>
                </span>
                <p v-if="c.evidence_b" class="cf-ev">“{{ c.evidence_b }}”</p>
              </div>
            </div>
            <div class="cf-resolve">
              <span class="eyebrow">建议的处理方式</span>
              <p>{{ c.suggested_resolution }}</p>
            </div>
          </article>
        </div>
        <div v-else class="empty">
          <span class="empty-icon mono">⚖</span>
          <span>本次检索未检测到明显的文献结论冲突</span>
          <span class="hint-line">当多篇论文的结果字段存在对立陈述时，这里会自动列出 A/B 双方与建议。</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <header class="panel-head">
        <span class="panel-title serif">研究空白</span>
        <span class="panel-sub">{{ gaps.length }} GAPS</span>
      </header>
      <div class="panel-body">
        <div v-if="gaps.length" class="gap-list">
          <article v-for="(g, i) in gaps" :key="i" class="gap-item">
            <div class="gap-head">
              <span class="gap-idx mono">{{ String(i + 1).padStart(2, '0') }}</span>
              <p class="gap-text">{{ g.gap }}</p>
            </div>
            <p class="gap-reason">{{ g.reason }}</p>
            <div v-if="(g.related_entities ?? []).length" class="gap-ents">
              <el-tag v-for="e in g.related_entities" :key="e" size="small" effect="plain" class="gap-tag">{{ e }}</el-tag>
            </div>
          </article>
        </div>
        <div v-else class="empty">
          <span class="empty-icon mono">◌</span>
          <span>尚未识别出研究空白</span>
          <span class="hint-line">空白来自知识图谱稀疏节点（孤立实体）与论文局限性字段的归纳。</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.panel-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.cf-list,
.gap-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cf-item {
  padding: 14px 16px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
  transition: border-color 0.2s var(--ease);
}

.cf-item:hover {
  border-color: var(--accent-line);
}

.cf-row {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 12px;
  align-items: start;
}

.cf-side {
  min-width: 0;
}

.cf-tag {
  display: inline-block;
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--accent-deep);
  border: 1px solid var(--accent-line);
  border-radius: 3px;
  padding: 1px 6px;
  margin-bottom: 6px;
}

.cf-claim {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.65;
  color: var(--text-1);
}

.cf-src {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 11.5px;
  color: var(--text-2);
}

.cf-src a {
  color: var(--text-1);
  text-decoration: none;
  border-bottom: 1px solid transparent;
}

.cf-src a:hover {
  color: var(--accent);
  border-bottom-color: var(--accent-line);
}

.cf-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--ink-750);
  color: var(--text-3);
}

.cf-badge.ok {
  color: var(--accent-deep);
  background: rgba(90, 130, 100, 0.12);
}

.cf-ev {
  margin: 7px 0 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-2);
}

.cf-vs {
  align-self: center;
  font-size: 12px;
  color: var(--text-3);
  letter-spacing: 0.1em;
}

.cf-resolve {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--hair-soft);
}

.cf-resolve p {
  margin: 4px 0 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-1);
}

.gap-item {
  padding: 13px 16px;
  border: 1px solid var(--hair-soft);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.9);
}

.gap-head {
  display: flex;
  gap: 11px;
  align-items: baseline;
}

.gap-idx {
  font-size: 10.5px;
  color: var(--text-3);
  letter-spacing: 0.06em;
}

.gap-text {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--text-1);
}

.gap-reason {
  margin: 8px 0 0 24px;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-2);
}

.gap-ents {
  margin: 9px 0 0 24px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.gap-tag {
  cursor: default;
}
</style>
