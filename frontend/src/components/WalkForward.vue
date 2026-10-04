<script setup>
import { computed, ref } from 'vue'
import { linearScale } from '../lib.js'
import { useWidth } from '../useWidth.js'

// 학습·평가 구간(configs/settings.yaml의 periods)을 해마다 한 줄로 그린다.
const props = defineProps({ periods: { type: Object, required: true } })
const box = ref(null)
const width = useWidth(box)
const first = computed(() => Number(props.periods.train_start.slice(0, 4)))
const forward = computed(() => Number(props.periods.forward_start.slice(0, 4)))
const rows = computed(() => {
  const [d0, d1] = props.periods.development, [h0, h1] = props.periods.holdout
  const make = (years, kind) => years.map(year => ({ year, kind }))
  const range = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i)
  return [...make(range(d0, d1), 'dev'), ...make(range(h0, h1), 'holdout'), ...make([forward.value], 'forward')]
})
const RH = 10, GAP = 7, M = { top: 6, right: 8, bottom: 24, left: 44 }
const height = computed(() => M.top + rows.value.length * (RH + GAP) + M.bottom)
const x = computed(() => linearScale([first.value, forward.value + 1], [M.left, width.value - M.right]))
const axis = computed(() => [first.value, props.periods.development[0], props.periods.holdout[0], forward.value])
</script>

<template>
  <div ref="box" class="chart-box">
    <svg :width="width" :height="height" role="img" aria-label="연도별 학습 구간과 평가 연도">
      <g v-for="(row, i) in rows" :key="row.year">
        <rect class="train" :x="x(first)" :y="M.top + i * (RH + GAP)" :width="Math.max(0, x(row.year) - x(first) - 2)" :height="RH" />
        <rect :class="['test', row.kind]" :x="x(row.year)" :y="M.top + i * (RH + GAP)" :width="x(row.year + 1) - x(row.year)" :height="RH" rx="2" />
        <text class="tick" :x="M.left - 8" :y="M.top + i * (RH + GAP) + RH / 2 + 4" text-anchor="end">{{ row.year }}</text>
      </g>
      <text v-for="year in axis" :key="`a${year}`" class="tick" :x="x(year)" :y="height - 6" text-anchor="middle">{{ year }}</text>
    </svg>
  </div>
</template>
