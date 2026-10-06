<script setup>
import { computed, ref } from 'vue'
import { isNumber, linearScale, ticks } from '../lib.js'
import { useWidth } from '../useWidth.js'

// 행마다 몇 개의 값을 한 축 위의 점으로 찍는 작은 그림(구간별 성적, 단계별 점수, 전후 비교).
// 기준값(reference)에 세로선을 긋고 기준값에서 점들까지 선을 이어, 기준에서 얼마나 떨어졌는지 보이게 한다.
const props = defineProps({
  rows: { type: Array, required: true },       // [{ label, values: { [series.key]: number | null } }]
  series: { type: Array, required: true },     // [{ key, name, color: 'muted' | 'amber' | 'cyan' | 'ink' }]
  domain: { type: Array, required: true },
  reference: { type: Number, default: 0 },
  format: { type: Function, default: value => `${value}` },
  tickFormat: { type: Function, default: null },   // 축 눈금. 없으면 format
  label: { type: String, required: true },     // 그림 전체의 설명(스크린 리더)
  labelWidth: { type: Number, default: 56 },
})
const box = ref(null)
const width = useWidth(box)
const RH = 58, GAP = 34   // GAP: 같은 쪽 숫자 둘이 이보다 가까우면 한 줄 더 띄운다(px)
const M = computed(() => ({ top: 30, right: 28, bottom: 26, left: props.labelWidth }))
const height = computed(() => M.value.top + props.rows.length * RH + M.value.bottom - 10)
const x = computed(() => linearScale(props.domain, [M.value.left, width.value - M.value.right]))
const gridValues = computed(() => ticks(props.domain, 5))
const lines = computed(() => props.rows.map((row, i) => {
  const dots = props.series.filter(s => isNumber(row.values[s.key])).map(s => ({ ...s, value: row.values[s.key] }))
    .sort((a, b) => a.value - b.value)
  const values = dots.map(d => d.value)
  // 숫자는 위·아래로 번갈아 놓고, 같은 쪽에서 겹치면 바깥으로 한 줄씩 민다
  const placed = []
  const labeled = dots.map((d, j) => {
    const side = j % 2 ? 1 : -1, cx = x.value(d.value)
    let level = 0
    while (placed.some(p => p.side === side && p.level === level && Math.abs(p.cx - cx) < GAP)) level += 1
    placed.push({ side, level, cx })
    return { ...d, cx, dy: side < 0 ? -12 - level * 13 : 20 + level * 13 }
  })
  return { label: row.label, y: M.value.top + i * RH, dots: labeled, from: Math.min(props.reference, ...values), to: Math.max(props.reference, ...values) }
}))
</script>

<template>
  <figure class="dotplot">
    <ul class="keys"><li v-for="s in series" :key="s.key"><i :class="['key', 'dot', `c-${s.color}`]" />{{ s.name }}</li></ul>
    <div ref="box" class="chart-box">
      <svg :width="width" :height="height" role="img" :aria-label="label">
        <g class="grid"><line v-for="v in gridValues" :key="v" :x1="x(v)" :x2="x(v)" :y1="M.top - 16" :y2="height - M.bottom" /></g>
        <g class="axis-label"><text v-for="v in gridValues" :key="`t${v}`" :x="x(v)" :y="height - 6" text-anchor="middle">{{ (tickFormat ?? format)(v) }}</text></g>
        <line class="origin-rule" :x1="x(reference)" :x2="x(reference)" :y1="M.top - 16" :y2="height - M.bottom" />
        <g v-for="row in lines" :key="row.label">
          <text class="direct-label strong" :x="M.left - 12" :y="row.y + 4" text-anchor="end">{{ row.label }}</text>
          <line class="range-line" :x1="x(row.from)" :x2="x(row.to)" :y1="row.y" :y2="row.y" />
          <g v-for="d in row.dots" :key="d.key">
            <circle :class="['dot', `c-${d.color}`]" r="5.5" :cx="d.cx" :cy="row.y"><title>{{ d.name }} {{ format(d.value) }}</title></circle>
            <text class="direct-label" :x="d.cx" :y="row.y + d.dy" text-anchor="middle">{{ format(d.value) }}</text>
          </g>
        </g>
      </svg>
    </div>
    <figcaption v-if="$slots.default" class="cap"><slot /></figcaption>
  </figure>
</template>
