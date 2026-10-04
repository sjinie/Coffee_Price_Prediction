<script setup>
import { computed, ref } from 'vue'
import { barPath, linearScale } from '../lib.js'
import { useWidth } from '../useWidth.js'

// 거래일 t의 뉴스 점수와 t+k일 수익률의 상관(coffee.portfolio가 노트북 05와 같은 계산으로 만든다).
const props = defineProps({ lag: { type: Object, required: true } })
const box = ref(null)
const width = useWidth(box)
const H = 280, M = { top: 22, right: 8, bottom: 46, left: 40 }
const y = linearScale([-0.1, 0.25], [H - M.bottom, M.top])
const slot = computed(() => (width.value - M.left - M.right) / props.lag.k.length)
const bw = computed(() => Math.min(24, slot.value * 0.68))
const cx = i => M.left + slot.value * (i + 0.5)
const bars = computed(() => props.lag.k.map((k, i) => ({
  k, r: props.lag.r[i], d: barPath(cx(i), y(0), y(props.lag.r[i]), bw.value), side: k <= 0 ? 'past' : 'future',
})))
const peak = computed(() => bars.value.reduce((a, b) => (b.r > a.r ? b : a)))
const peakX = computed(() => cx(props.lag.k.indexOf(peak.value.k)))
</script>

<template>
  <div ref="box" class="chart-box">
    <svg :width="width" :height="H" role="img" aria-label="뉴스 점수와 일간 수익률의 시차 상관. 점수보다 하루 앞선 수익률과 가장 강하게 관련된다.">
      <rect class="band-zero" :x="M.left" :width="width - M.left - M.right" :y="y(lag.band)" :height="y(-lag.band) - y(lag.band)" />
      <g class="grid"><line v-for="v in [-0.1, 0, 0.1, 0.2]" :key="v" :x1="M.left" :x2="width - M.right" :y1="y(v)" :y2="y(v)" /></g>
      <g class="axis-label"><text v-for="v in [-0.1, 0, 0.1, 0.2]" :key="`t${v}`" :x="M.left - 8" :y="y(v) + 4" text-anchor="end">{{ v.toFixed(1) }}</text></g>
      <path v-for="b in bars" :key="b.k" :class="['bar', b.side]" :d="b.d" tabindex="0"><title>k = {{ b.k > 0 ? '+' : '' }}{{ b.k }}, 상관 {{ b.r.toFixed(3) }}</title></path>
      <text class="direct-label strong" :x="peakX" :y="y(peak.r) - 8" text-anchor="middle">{{ peak.r.toFixed(2) }}</text>
      <g class="axis-label">
        <text v-for="(k, i) in lag.k" v-show="[-10, -5, -1, 1, 5, 10].includes(k)" :key="`k${k}`" :x="cx(i)" :y="H - M.bottom + 16" text-anchor="middle">{{ k > 0 ? `+${k}` : k }}</text>
      </g>
      <text class="plot-label" :x="M.left" :y="H - 6">← 점수보다 앞선 날</text>
      <text class="plot-label" :x="width - M.right" :y="H - 6" text-anchor="end">점수 뒤의 날 →</text>
    </svg>
  </div>
</template>
