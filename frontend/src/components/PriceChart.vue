<script setup>
import { computed, ref } from 'vue'
import { bandPath, extent, formatNumber, isNumber, linePath, linearScale, nearestIndex, ticks, toTime } from '../lib.js'
import { useWidth } from '../useWidth.js'

const props = defineProps({
  prices: { type: Array, required: true },   // [{date, close}]
  history: { type: Object, required: true }, // {20: [예측 행], 60: [...]}
  latest: { type: Array, required: true },
})
const HEIGHT = 300
const MARGIN = { top: 16, right: 16, bottom: 28, left: 48 }
const horizon = ref(20)
const box = ref(null)
const width = useWidth(box)

const prices = computed(() => props.prices.filter(row => isNumber(row.close)))
const start = computed(() => prices.value[0]?.date)
const current = computed(() => props.latest.find(row => row.horizon === horizon.value))
// 목표일이 이미 지난 과거 예측의 범위(아직 오지 않은 목표일은 오늘의 범위 부채꼴로만 보여 준다)
const band = computed(() => (props.history[horizon.value] || [])
  .filter(row => isNumber(row.price_low) && row.target_date >= start.value && row.target_date <= prices.value.at(-1)?.date))
const byTarget = computed(() => new Map(band.value.map(row => [row.target_date, row])))

const x = computed(() => {
  const end = current.value?.target_date || prices.value.at(-1)?.date
  return linearScale([toTime(start.value), toTime(end)], [MARGIN.left, width.value - MARGIN.right])
})
const yDomain = computed(() => extent([
  ...prices.value.map(row => row.close),
  ...band.value.flatMap(row => [row.price_low, row.price_high]),
  current.value?.price_low, current.value?.price_high,
]))
const y = computed(() => linearScale(yDomain.value, [HEIGHT - MARGIN.bottom, MARGIN.top]))

const point = (date, value) => [x.value(toTime(date)), y.value(value)]
const pricePath = computed(() => linePath(prices.value.map(row => point(row.date, row.close))))
const pastBand = computed(() => bandPath(
  band.value.map(row => point(row.target_date, row.price_high)),
  band.value.map(row => point(row.target_date, row.price_low))))
const fan = computed(() => {
  const row = current.value
  if (!row || !isNumber(row.price_low)) return ''
  const [ox, oy] = point(row.origin_date, row.origin_close)
  const [tx, high] = point(row.target_date, row.price_high)
  const low = y.value(row.price_low)
  return `M${ox},${oy} L${tx},${high} L${tx},${low} Z`
})
const yTicks = computed(() => ticks(yDomain.value))
const xTicks = computed(() => {
  const [t0, t1] = [x.value.invert(MARGIN.left), x.value.invert(width.value - MARGIN.right)]
  const step = width.value < 600 ? 3 : 2
  const result = []
  for (let d = new Date(t0); d.getTime() <= t1; d.setUTCMonth(d.getUTCMonth() + 1)) {
    d.setUTCDate(1)
    if (d.getTime() >= t0 && d.getUTCMonth() % step === 0) {
      result.push({ time: d.getTime(), label: `${String(d.getUTCFullYear()).slice(2)}.${d.getUTCMonth() + 1}` })
    }
  }
  return result
})

const hover = ref(null)
function onMove(event) {
  const rect = event.currentTarget.getBoundingClientRect()
  const time = x.value.invert(event.clientX - rect.left)
  const lastPrice = prices.value.at(-1)
  if (current.value && time > toTime(lastPrice.date)) {
    hover.value = { left: x.value(toTime(current.value.target_date)), date: current.value.target_date, future: current.value }
    return
  }
  const index = nearestIndex(prices.value.map(row => toTime(row.date)), time)
  const row = prices.value[index]
  hover.value = { left: x.value(toTime(row.date)), date: row.date, close: row.close, past: byTarget.value.get(row.date) }
}
</script>

<template>
  <section aria-labelledby="chart-title">
    <div class="section-head">
      <h2 id="chart-title">가격과 예상 범위 <span class="muted">센트/파운드</span></h2>
      <div class="segmented" role="group" aria-label="지평 선택">
        <button v-for="h in [20, 60]" :key="h" :class="{ active: horizon === h }" :aria-pressed="horizon === h" @click="horizon = h">
          {{ h }}거래일
        </button>
      </div>
    </div>
    <ul class="legend">
      <li><i class="key line" />종가</li>
      <li><i class="key band" />{{ horizon }}거래일 전에 예측한 80% 범위</li>
      <li><i class="key fan" />오늘 기준 예상 범위</li>
    </ul>
    <div ref="box" class="chart-box">
      <svg :width="width" :height="HEIGHT" role="img"
           :aria-label="`KC=F 종가와 ${horizon}거래일 80% 예상 범위. 표는 아래 '표로 보기'에 있습니다.`"
           @pointermove="onMove" @pointerleave="hover = null">
        <g class="grid">
          <line v-for="(tick, i) in yTicks" :key="i" :x1="MARGIN.left" :x2="width - MARGIN.right" :y1="y(tick)" :y2="y(tick)" />
        </g>
        <g class="axis-label">
          <text v-for="(tick, i) in yTicks" :key="`y${i}`" :x="MARGIN.left - 8" :y="y(tick) + 4" text-anchor="end">{{ Math.round(tick) }}</text>
          <text v-for="tick in xTicks" :key="tick.time" :x="x(tick.time)" :y="HEIGHT - 8" text-anchor="middle">{{ tick.label }}</text>
        </g>
        <path :d="pastBand" class="band" />
        <path :d="fan" class="fan" />
        <path :d="pricePath" class="price" />
        <g v-if="hover">
          <line class="crosshair" :x1="hover.left" :x2="hover.left" :y1="MARGIN.top" :y2="HEIGHT - MARGIN.bottom" />
        </g>
      </svg>
      <div v-if="hover" class="tooltip" :style="{ left: `${Math.min(hover.left + 12, width - 210)}px` }">
        <strong>{{ hover.date }}</strong>
        <template v-if="hover.future">
          <span>오늘 기준 {{ horizon }}거래일 예상 범위</span>
          <span>{{ formatNumber(hover.future.price_low) }} ~ {{ formatNumber(hover.future.price_high) }}</span>
        </template>
        <template v-else>
          <span>종가 {{ formatNumber(hover.close) }}</span>
          <span v-if="hover.past">예측 범위 {{ formatNumber(hover.past.price_low) }} ~ {{ formatNumber(hover.past.price_high) }}
            <small>(기준일 {{ hover.past.origin_date }})</small></span>
        </template>
      </div>
    </div>
    <details>
      <summary>표로 보기 (최근 20개 예측)</summary>
      <table>
        <thead><tr><th>기준일</th><th>목표일</th><th class="num">기준 종가</th><th class="num">80% 범위</th><th class="num">실제 종가</th></tr></thead>
        <tbody>
          <tr v-for="row in (history[horizon] || []).slice(-20).reverse()" :key="row.origin_date">
            <td>{{ row.origin_date }}</td><td>{{ row.target_date }}</td><td class="num">{{ formatNumber(row.origin_close) }}</td>
            <td class="num">{{ formatNumber(row.price_low) }} ~ {{ formatNumber(row.price_high) }}</td>
            <td class="num">{{ formatNumber(row.actual_close) }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </section>
</template>
