<script setup>
import { computed } from 'vue'
import { arcPath, isNumber } from '../lib.js'

// 연간 시계: 고리 하나가 한 해(안쪽이 첫해), 12시 방향에서 1월이 시작한다. 칸 색은 값의 크기다.
const props = defineProps({
  grid: { type: Array, required: true },   // 연도 × 52주
  years: { type: Array, required: true },
  kind: { type: String, required: true },   // rain: 한 가지 색의 진하기, return: 하락·상승 두 색
  label: { type: String, required: true },
})
const R = 190, R0 = 48
const ring = computed(() => (R - R0) / Math.max(props.years.length, 1))
const angle = week => (week / 52) * 2 * Math.PI
const scaleMax = computed(() => {
  if (props.kind === 'return') return 0.05   // 주간 로그수익률 ±5%에서 색이 가장 진하다
  const values = props.grid.flat().filter(isNumber).sort((a, b) => a - b)
  return values[Math.floor(values.length * 0.98)] || 1
})
// 색은 CSS 변수로 섞어 테마가 바뀌어도 다시 그리지 않는다
function fill(value) {
  if (props.kind === 'rain') return `color-mix(in oklab, var(--rain-0), var(--rain-1) ${Math.round(Math.min(1, Math.sqrt(Math.max(0, value) / scaleMax.value)) * 100)}%)`
  const side = value >= 0 ? 'var(--up)' : 'var(--down)'
  return `color-mix(in oklab, var(--div-mid), ${side} ${Math.round(Math.min(1, Math.abs(value) / scaleMax.value) * 100)}%)`
}
const text = (year, week, value) => {
  const start = new Date(Date.UTC(year, 0, 1 + week * 7))
  const shown = props.kind === 'rain' ? `강수 ${value.toFixed(0)}mm (7일 기준)` : `로그수익률 ${(value * 100).toFixed(2)}%`
  return `${year}년 ${start.getUTCMonth() + 1}월 ${start.getUTCDate()}일부터 한 주, ${shown}`
}
const cells = computed(() => props.grid.flatMap((row, y) => row.map((value, week) => (isNumber(value) ? {
  key: `${y}-${week}`, d: arcPath(R0 + y * ring.value, R0 + (y + 1) * ring.value - 0.6, angle(week) + 0.004, angle(week + 1) - 0.004),
  fill: fill(value), title: text(props.years[y], week, value),
} : null)).filter(Boolean)))
const months = Array.from({ length: 12 }, (_, m) => {
  const mid = ((Date.UTC(2021, m, 15) - Date.UTC(2021, 0, 1)) / 86400000 / 365) * 2 * Math.PI
  return { label: `${m + 1}월`, x: Math.sin(mid) * (R + 17), y: -Math.cos(mid) * (R + 17) }
})
</script>

<template>
  <svg class="clock" viewBox="-222 -222 444 444" role="img" :aria-label="label">
    <path v-for="cell in cells" :key="cell.key" class="cell" :d="cell.d" :style="{ fill: cell.fill }"><title>{{ cell.title }}</title></path>
    <text v-for="m in months" :key="m.label" :x="m.x" :y="m.y + 4" text-anchor="middle">{{ m.label }}</text>
    <line class="origin-rule" x1="0" x2="0" :y1="-R0 + 4" :y2="-R - 4" />
  </svg>
</template>
