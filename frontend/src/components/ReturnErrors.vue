<script setup>
import { computed, ref } from 'vue'
import { HORIZONS, formatSigned, isNumber, linearScale } from '../lib.js'
import { LSTM_RMSE_VS_NAIVE } from '../research.js'
import { useWidth } from '../useWidth.js'

// 현재가 유지(Naive) 대비 RMSE(%). 값은 모델 metadata의 return.horizons[h].dev·holdout에서 읽는다.
const props = defineProps({ metadata: { type: Object, default: null } })
const box = ref(null)
const width = useWidth(box)
const M = { top: 8, right: 104, bottom: 26, left: 56 }, BH = 14, INNER = 4, GROUP = 22
const groups = computed(() => HORIZONS.map(h => {
  const item = props.metadata?.return?.horizons?.[h]
  return { h, dev: item?.dev?.rmse_vs_naive_pct, holdout: item?.holdout?.rmse_vs_naive_pct }
}))
const maxValue = computed(() => Math.max(LSTM_RMSE_VS_NAIVE + 1, ...groups.value.flatMap(g => [g.dev, g.holdout]).filter(isNumber)))
const x = computed(() => linearScale([0, maxValue.value], [M.left, width.value - M.right]))
const groupY = i => M.top + i * (BH * 2 + INNER + GROUP)
const lstmY = computed(() => groupY(groups.value.length) + 4)
const height = computed(() => lstmY.value + BH + 18 + M.bottom)
const gridValues = computed(() => [0, 5, 10, 15, 20].filter(v => v <= maxValue.value))
// 0에서 오른쪽으로 가는 막대. 값 쪽 끝만 둥글다.
function hbar(value, top) {
  const w = Math.max(3, x.value(value) - x.value(0)), r = Math.min(4, w / 2, BH / 2), x0 = x.value(0)
  return `M${x0},${top} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${BH - 2 * r} a${r},${r} 0 0 1 -${r},${r} h-${w - r} Z`
}
</script>

<template>
  <div ref="box" class="chart-box">
    <svg :width="width" :height="height" role="img" aria-label="지평별 현재가 유지 대비 RMSE. 오른쪽일수록 오차가 크다.">
      <g class="grid"><line v-for="v in gridValues" :key="v" :x1="x(v)" :x2="x(v)" :y1="M.top" :y2="height - M.bottom" /></g>
      <g class="axis-label"><text v-for="v in gridValues" :key="`t${v}`" :x="x(v)" :y="height - 6" text-anchor="middle">{{ v ? `+${v}%` : '0' }}</text></g>
      <g v-for="(g, i) in groups" :key="g.h">
        <text class="direct-label strong" :x="M.left - 12" :y="groupY(i) + BH + 4" text-anchor="end">{{ g.h }}일</text>
        <template v-for="(item, j) in [['dev', g.dev], ['holdout', g.holdout]]" :key="item[0]">
          <path v-if="isNumber(item[1])" :class="['hbar', item[0]]" :d="hbar(item[1], groupY(i) + j * (BH + INNER))" />
          <text class="direct-label" :x="x(isNumber(item[1]) ? item[1] : 0) + 8" :y="groupY(i) + j * (BH + INNER) + BH / 2 + 4">{{ isNumber(item[1]) ? `${formatSigned(item[1])}%` : '확인 불가' }}</text>
        </template>
      </g>
      <text class="direct-label strong" :x="M.left - 12" :y="lstmY + BH / 2 + 4" text-anchor="end">LSTM</text>
      <rect class="hbar-outline" :x="x(0)" :y="lstmY" :width="x(LSTM_RMSE_VS_NAIVE) - x(0)" :height="BH" />
      <text class="direct-label" :x="x(LSTM_RMSE_VS_NAIVE) + 8" :y="lstmY + BH / 2 + 4">+{{ LSTM_RMSE_VS_NAIVE }}% 20일</text>
      <line class="origin-rule" :x1="x(0)" :x2="x(0)" :y1="M.top - 4" :y2="height - M.bottom" />
    </svg>
  </div>
</template>
