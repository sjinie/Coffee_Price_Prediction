<script setup>
import { computed, ref } from 'vue'
import { isNumber, linearScale } from '../lib.js'
import { VOLATILITY } from '../research.js'
import { useWidth } from '../useWidth.js'

// 직전 변동성 대비 RMSE 감소율(%)을 구간별 점으로 찍는다. 0보다 오른쪽이면 기준선보다 낫다.
const box = ref(null)
const width = useWidth(box)
const M = { top: 26, right: 24, bottom: 26, left: 56 }, RH = 54
const KINDS = [['dev', '개발'], ['holdout', '보류'], ['forward', '2026년']]
const height = M.top + VOLATILITY.length * RH + M.bottom - 10
const x = computed(() => linearScale([-5, 30], [M.left, width.value - M.right]))
const rows = computed(() => VOLATILITY.map((row, i) => {
  const dots = KINDS.filter(([k]) => isNumber(row[k])).map(([k, name]) => ({ k, name, value: row[k] })).sort((a, b) => a.value - b.value)
  const values = dots.map(d => d.value)
  return { ...row, y: M.top + i * RH, dots, from: Math.min(0, ...values), to: Math.max(0, ...values) }
}))
</script>

<template>
  <div ref="box" class="chart-box">
    <svg :width="width" :height="height" role="img" aria-label="지평별 직전 변동성 대비 RMSE 감소율">
      <g class="grid"><line v-for="v in [0, 10, 20, 30]" :key="v" :x1="x(v)" :x2="x(v)" :y1="M.top - 16" :y2="height - M.bottom" /></g>
      <g class="axis-label"><text v-for="v in [0, 10, 20, 30]" :key="`t${v}`" :x="x(v)" :y="height - 6" text-anchor="middle">{{ v }}%</text></g>
      <line class="origin-rule" :x1="x(0)" :x2="x(0)" :y1="M.top - 16" :y2="height - M.bottom" />
      <g v-for="row in rows" :key="row.horizon">
        <text class="direct-label strong" :x="M.left - 12" :y="row.y + 4" text-anchor="end">{{ row.horizon }}일</text>
        <line class="range-line" :x1="x(row.from)" :x2="x(row.to)" :y1="row.y" :y2="row.y" />
        <g v-for="(d, j) in row.dots" :key="d.k">
          <circle :class="['dot', d.k]" r="5.5" :cx="x(d.value)" :cy="row.y"><title>{{ d.name }} {{ d.value }}%</title></circle>
          <text class="direct-label" :x="x(d.value)" :y="row.y + (j % 2 ? 20 : -12)" text-anchor="middle">{{ d.value }}%</text>
        </g>
      </g>
    </svg>
  </div>
</template>
