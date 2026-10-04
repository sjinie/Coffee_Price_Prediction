<script setup>
import { computed, ref } from 'vue'
import { isNumber, linePath, linearScale, ticks, toTime, weeklyLast } from '../lib.js'
import { useWidth } from '../useWidth.js'

// 2005년부터의 주간 종가. 원두 계약 사이에 가격이 얼마나 움직이는지 보여 주는 맥락 그림이다.
const props = defineProps({ prices: { type: Array, required: true } })
const box = ref(null)
const width = useWidth(box)
const H = 320, M = { top: 28, right: 16, bottom: 26, left: 40 }
const weeks = computed(() => weeklyLast(props.prices).filter(row => isNumber(row.close)))
const x = computed(() => linearScale([toTime(weeks.value[0].date), toTime(weeks.value.at(-1).date)], [M.left, width.value - M.right]))
const yMax = computed(() => Math.max(...weeks.value.map(row => row.close)))
const y = computed(() => linearScale([0, yMax.value * 1.08], [H - M.bottom, M.top]))
const points = computed(() => weeks.value.map(row => [x.value(toTime(row.date)), y.value(row.close)]))
const area = computed(() => `${linePath(points.value)} L${points.value.at(-1)[0]},${y.value(0)} L${points.value[0][0]},${y.value(0)} Z`)
const years = computed(() => {
  const first = Number(weeks.value[0].date.slice(0, 4)), last = Number(weeks.value.at(-1).date.slice(0, 4))
  const step = width.value < 600 ? 5 : 3
  return Array.from({ length: Math.floor((last - first) / step) + 1 }, (_, i) => first + 2 + i * step).filter(year => year <= last)
})
const marks = computed(() => {
  const high = weeks.value.reduce((a, b) => (b.close > a.close ? b : a))
  const low = weeks.value.reduce((a, b) => (b.close < a.close ? b : a))
  return [[high, -14], [low, 24]].map(([row, dy]) => {
    const [cx, cy] = [x.value(toTime(row.date)), y.value(row.close)]
    const right = cx > width.value * 0.7
    return { key: row.date, cx, cy, tx: cx + (right ? -10 : 10), ty: cy + dy, anchor: right ? 'end' : 'start',
      text: `${row.date.slice(0, 4)}년 ${Number(row.date.slice(5, 7))}월 ${row.close.toFixed(1)}` }
  })
})
</script>

<template>
  <div ref="box" class="chart-box">
    <svg v-if="weeks.length > 1" :width="width" :height="H" role="img" aria-label="KC=F 주간 종가, 2005년부터. 최고점과 최저점을 표시했다.">
      <g class="grid"><line v-for="t in ticks([0, yMax * 1.08], 4)" :key="t" :x1="M.left" :x2="width - M.right" :y1="y(t)" :y2="y(t)" /></g>
      <g class="axis-label">
        <text v-for="t in ticks([0, yMax * 1.08], 4)" :key="`t${t}`" :x="M.left - 8" :y="y(t) + 4" text-anchor="end">{{ t }}</text>
        <text v-for="year in years" :key="year" :x="x(toTime(`${year}-01-01`))" :y="H - 6" text-anchor="middle">{{ year }}</text>
      </g>
      <path class="area" :d="area" />
      <path class="price" :d="linePath(points)" />
      <g v-for="m in marks" :key="m.key">
        <circle class="actual-dot" r="4" :cx="m.cx" :cy="m.cy" />
        <text class="direct-label" :x="m.tx" :y="m.ty" :text-anchor="m.anchor">{{ m.text }}</text>
      </g>
    </svg>
    <p v-else class="cap">표시할 가격 자료가 없습니다.</p>
  </div>
</template>
