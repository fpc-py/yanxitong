<script setup lang="ts">
// 置信度仪表：SVG 弧线 + 数字滚动动画
import { computed, onUnmounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    value: number // 0 ~ 1
    label?: string
    sublabel?: string
    size?: number
  }>(),
  { label: '', sublabel: '', size: 172 },
)

const clamped = computed(() => {
  const v = Number(props.value)
  return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0
})

const displayed = ref(0)
let raf = 0

function animateTo(target: number): void {
  cancelAnimationFrame(raf)
  const from = displayed.value
  const t0 = performance.now()
  const duration = 720
  const step = (t: number): void => {
    const p = Math.min(1, (t - t0) / duration)
    const eased = 1 - Math.pow(1 - p, 3)
    displayed.value = from + (target - from) * eased
    if (p < 1) raf = requestAnimationFrame(step)
  }
  raf = requestAnimationFrame(step)
}

watch(clamped, (v) => animateTo(v), { immediate: true })
onUnmounted(() => cancelAnimationFrame(raf))

// 弧线几何：240° 扫角
const ARC_LEN = (240 / 360) * 2 * Math.PI * 48
const TRACK_PATH = 'M 18.43 84 A 48 48 0 1 1 101.57 84'

const dash = computed(() => `${(displayed.value * ARC_LEN).toFixed(2)} ${ARC_LEN.toFixed(2)}`)
const percentText = computed(() => (displayed.value * 100).toFixed(0))

const tone = computed(() => {
  if (clamped.value >= 0.7) return 'ok'
  if (clamped.value >= 0.45) return 'warn'
  return 'low'
})

const toneColor = computed(() => {
  if (tone.value === 'ok') return 'var(--accent)'
  if (tone.value === 'warn') return 'var(--amber)'
  return 'var(--danger)'
})

// 刻度线
const ticks = computed(() =>
  Array.from({ length: 25 }, (_, i) => {
    const deg = 150 + i * 10
    const rad = (deg * Math.PI) / 180
    const long = i % 4 === 0
    const r1 = 53
    const r2 = long ? 58 : 55.5
    return {
      x1: (60 + r1 * Math.cos(rad)).toFixed(2),
      y1: (60 + r1 * Math.sin(rad)).toFixed(2),
      x2: (60 + r2 * Math.cos(rad)).toFixed(2),
      y2: (60 + r2 * Math.sin(rad)).toFixed(2),
      long,
    }
  }),
)
</script>

<template>
  <div class="gauge">
    <svg :width="size" :height="size * 0.8" viewBox="0 0 120 96" aria-hidden="true">
      <g class="ticks">
        <line
          v-for="(t, i) in ticks"
          :key="i"
          :x1="t.x1"
          :y1="t.y1"
          :x2="t.x2"
          :y2="t.y2"
          :stroke-width="t.long ? 1.2 : 0.7"
          :opacity="t.long ? 0.5 : 0.26"
        />
      </g>

      <path :d="TRACK_PATH" class="track" />
      <path :d="TRACK_PATH" class="value-arc" :style="{ stroke: toneColor, strokeDasharray: dash }" />
    </svg>

    <div class="readout">
      <div class="num-row">
        <span class="big-num mono">{{ percentText }}</span>
        <span class="pct mono">%</span>
      </div>
      <div v-if="label" class="gauge-label">{{ label }}</div>
      <div v-if="sublabel" class="gauge-sub mono">{{ sublabel }}</div>
    </div>
  </div>
</template>

<style scoped>
.gauge {
  position: relative;
  display: flex;
  justify-content: center;
}

svg {
  display: block;
  overflow: visible;
}

.track {
  fill: none;
  stroke: var(--hair-soft);
  stroke-width: 5;
  stroke-linecap: round;
}

.value-arc {
  fill: none;
  stroke-width: 5;
  stroke-linecap: round;
  filter: drop-shadow(0 0 6px var(--accent-glow));
  transition: stroke 0.4s var(--ease);
}

.ticks line {
  stroke: var(--text-3);
}

.readout {
  position: absolute;
  left: 0;
  right: 0;
  top: 46%;
  transform: translateY(-50%);
  text-align: center;
}

.num-row {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 2px;
}

.big-num {
  font-size: 34px;
  font-weight: 500;
  line-height: 1;
  letter-spacing: -0.03em;
  color: var(--text-1);
}

.pct {
  font-size: 13px;
  color: var(--text-3);
}

.gauge-label {
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--text-2);
}

.gauge-sub {
  margin-top: 2px;
  font-size: 9.5px;
  letter-spacing: 0.10em;
  color: var(--text-3);
}
</style>
