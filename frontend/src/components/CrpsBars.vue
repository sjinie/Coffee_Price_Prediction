<script setup>
import { computed, ref } from 'vue'
import { HORIZONS, formatSigned, isNumber, linearScale, ticks } from '../lib.js'
import { useWidth } from '../useWidth.js'

// 분포 모델의 CRPS를 B1(현재가 유지 + HAR 폭)과 비교한 변화(%). 값은 모델 metadata의
// distribution.horizons[h].dev·holdout에서 읽는다. 0보다 왼쪽이면 예측 분포가 실제 가격에 더 가까웠다.
const props = defineProps({ metadata: { type: Object, default: null } })
const box = ref(null)
const width = useWidth(box)
const M = { top: 8, right: 72, bottom: 26, left: 56 }, BH = 14, INNER = 4, GROUP = 22
const groups = computed(() => HORIZONS.map(h => {
  const item = props.metadata?.distribution?.horizons?.[h]
  return { h, dev: item?.dev?.crps_vs_b1_pct, holdout: item?.holdout?.crps_vs_b1_pct }
}))
const values = computed(() => groups.value.flatMap(g => [g.dev, g.holdout]).filter(isNumber))
const domain = computed(() => [Math.min(-1, Math.floor(Math.min(0, ...values.value))),
  Math.max(1, Math.ceil(Math.max(0, ...values.value)))])
const x = computed(() => linearScale(domain.value, [M.left, width.value - M.right]))
const groupY = i => M.top + i * (BH * 2 + INNER + GROUP)
const height = computed(() => groupY(groups.value.length) + M.bottom)
const gridValues = computed(() => ticks(domain.value, 5))
// 0에서 값 쪽으로 가는 막대(음수면 왼쪽). 값 쪽 끝만 둥글다.
function hbar(value, top) {
  const x0 = x.value(0), w = Math.max(3, Math.abs(x.value(value) - x0)), r = Math.min(4, w / 2, BH / 2)
  if (value < 0) return `M${x0},${top} h-${w - r} a${r},${r} 0 0 0 -${r},${r} v${BH - 2 * r} a${r},${r} 0 0 0 ${r},${r} h${w - r} Z`
  return `M${x0},${top} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${BH - 2 * r} a${r},${r} 0 0 1 -${r},${r} h-${w - r} Z`
}
</script>

<template>
  <div ref="box" class="chart-box">
    <svg :width="width" :height="height" role="img" aria-label="지평별 분포 모델의 CRPS, 현재가 유지와 HAR 폭 대비 변화. 0보다 왼쪽이면 실제 가격에 더 가깝다.">
      <g class="grid"><line v-for="v in gridValues" :key="v" :x1="x(v)" :x2="x(v)" :y1="M.top" :y2="height - M.bottom" /></g>
      <g class="axis-label"><text v-for="v in gridValues" :key="`t${v}`" :x="x(v)" :y="height - 6" text-anchor="middle">{{ v ? `${formatSigned(v, Number.isInteger(v) ? 0 : 1)}%` : '0' }}</text></g>
      <g v-for="(g, i) in groups" :key="g.h">
        <text class="direct-label strong" :x="M.left - 12" :y="groupY(i) + BH + 4" text-anchor="end">{{ g.h }}일</text>
        <template v-for="(item, j) in [['dev', g.dev], ['holdout', g.holdout]]" :key="item[0]">
          <path v-if="isNumber(item[1])" :class="['hbar', item[0]]" :d="hbar(item[1], groupY(i) + j * (BH + INNER))" />
          <text class="direct-label" :x="x(isNumber(item[1]) ? Math.max(0, item[1]) : 0) + 8" :y="groupY(i) + j * (BH + INNER) + BH / 2 + 4">{{ isNumber(item[1]) ? `${formatSigned(item[1])}%` : '확인 불가' }}</text>
        </template>
      </g>
      <line class="origin-rule" :x1="x(0)" :x2="x(0)" :y1="M.top - 4" :y2="height - M.bottom" />
    </svg>
  </div>
</template>
