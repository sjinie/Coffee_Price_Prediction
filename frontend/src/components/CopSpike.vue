<script setup>
import { computed, ref } from 'vue'
import { formatNumber, linePath, linearScale, ticks, toTime } from '../lib.js'
import { useWidth } from '../useWidth.js'

// Yahoo 페소 환율(COP/USD) 원자료와 이상치 규칙(coffee.features.clean_sources)을 거친 값.
// 자료는 coffee.portfolio가 만든다: { dates, raw, replaced: [{ i, used }] }
const props = defineProps({ fx: { type: Object, required: true } })
const box = ref(null)
const width = useWidth(box)
const H = 260, M = { top: 24, right: 16, bottom: 26, left: 52 }
const used = computed(() => {
  const values = [...props.fx.raw]
  for (const { i, used: value } of props.fx.replaced) values[i] = value
  return values
})
const x = computed(() => linearScale([toTime(props.fx.dates[0]), toTime(props.fx.dates.at(-1))], [M.left, width.value - M.right]))
const yMax = computed(() => Math.max(...props.fx.raw, ...used.value) * 1.05)
const y = computed(() => linearScale([0, yMax.value], [H - M.bottom, M.top]))
const path = values => linePath(values.map((v, i) => [x.value(toTime(props.fx.dates[i])), y.value(v)]))
const spikes = computed(() => props.fx.replaced.map(({ i }) => ({ i, cx: x.value(toTime(props.fx.dates[i])), cy: y.value(props.fx.raw[i]) })))
const lowest = computed(() => props.fx.replaced.reduce((a, b) => (props.fx.raw[b.i] < props.fx.raw[a.i] ? b : a)))
const years = computed(() => {
  const first = Number(props.fx.dates[0].slice(0, 4)), last = Number(props.fx.dates.at(-1).slice(0, 4))
  return Array.from({ length: last - first + 1 }, (_, k) => first + k).filter(year => `${year}-07-01` <= props.fx.dates.at(-1))
})
</script>

<template>
  <figure>
    <ul class="keys"><li><i class="key raw" />원자료</li><li><i class="key line" />규칙을 거친 값</li><li><i class="key dot c-amber" />바꾼 날</li></ul>
    <div ref="box" class="chart-box">
      <svg :width="width" :height="H" role="img" :aria-label="`페소 환율 원자료와 이상치 규칙을 거친 값. 원자료가 ${fx.replaced.length}번 거의 0으로 떨어졌다 돌아온다.`">
        <g class="grid"><line v-for="t in ticks([0, yMax], 4)" :key="t" :x1="M.left" :x2="width - M.right" :y1="y(t)" :y2="y(t)" /></g>
        <g class="axis-label">
          <text v-for="t in ticks([0, yMax], 4)" :key="`t${t}`" :x="M.left - 8" :y="y(t) + 4" text-anchor="end">{{ t.toLocaleString('ko-KR') }}</text>
          <text v-for="year in years" :key="year" :x="x(toTime(`${year}-07-01`))" :y="H - 6" text-anchor="middle">{{ year }}</text>
        </g>
        <path class="raw-line" :d="path(fx.raw)" />
        <path class="price" :d="path(used)" />
        <circle v-for="s in spikes" :key="s.i" class="pred-dot" r="3.5" :cx="s.cx" :cy="s.cy" />
        <text class="direct-label strong" :x="x(toTime(fx.dates[lowest.i])) + 8" :y="H - M.bottom - 8">
          {{ fx.dates[lowest.i] }} {{ formatNumber(fx.raw[lowest.i], 2) }} → {{ formatNumber(lowest.used, 0) }}
        </text>
      </svg>
    </div>
    <figcaption class="cap"><slot /></figcaption>
  </figure>
</template>
