<script setup>
import { computed, ref, watch } from 'vue'
import {
  HORIZONS, SIGNAL_LABELS, bandPath, extent, formatNumber, formatPercent, formatReturn, formatSigned,
  isNumber, isolatedItems, linePath, linearScale, modelFor, nearestIndex, ticks, toTime, weeklyLast,
} from '../lib.js'
import { useSize } from '../useWidth.js'

const props = defineProps({
  prices: { type: Array, required: true }, history: { type: Object, required: true }, latest: { type: Array, required: true },
  metadata: { type: Object, default: null }, horizon: { type: Number, required: true },
  allPrices: { type: Array, default: null }, fullLoading: Boolean, fullError: { type: String, default: '' }, historyFailed: Boolean,
  // hero: 첫 화면(가격과 예측만, 남은 높이를 채움), story: How it works의 단계별 그림, explore: 조절·확률 칸·표가 있는 전체 차트
  mode: { type: String, default: 'explore' },
  step: { type: Number, default: 5 },
})
const emit = defineEmits(['update:horizon', 'load-all'])
const period = ref('year')
const box = ref(null)
const { width, height: boxHeight } = useSize(box)
const hover = ref(null)
const full = computed(() => props.mode === 'explore')
const all = computed(() => full.value && period.value === 'all' && props.allPrices !== null)
const MARGIN = { left: 40, right: 88 }
const height = computed(() => full.value ? (all.value ? 408 : 520) : Math.max(240, boxHeight.value))
const PRICE = computed(() => full.value ? { top: 40, bottom: 320 } : { top: 30, bottom: height.value - 34 })
const PROB = { top: 408, bottom: 488 }
const showProb = computed(() => full.value && !all.value)
const current = computed(() => props.latest.find(row => row.horizon === props.horizon))
const threshold = computed(() => props.metadata?.direction?.horizons?.[props.horizon]?.threshold)
const prices = computed(() => all.value ? weeklyLast(props.allPrices) : props.prices)
const start = computed(() => prices.value[0]?.date || current.value?.origin_date)
const lastDate = computed(() => prices.value.at(-1)?.date || current.value?.origin_date)
const end = computed(() => [lastDate.value, current.value?.target_date].filter(Boolean).sort().at(-1))
const rows = computed(() => props.history[props.horizon] || [])
const past = computed(() => rows.value.filter(row => row.target_date >= start.value && row.target_date <= lastDate.value))
const probRows = computed(() => rows.value.filter(row => row.origin_date >= start.value && row.origin_date <= lastDate.value))
const byTarget = computed(() => new Map(past.value.map(row => [row.target_date, row])))
const byOrigin = computed(() => new Map(probRows.value.map(row => [row.origin_date, row])))
const hasData = computed(() => prices.value.some(row => isNumber(row.close)) || (!all.value && isNumber(current.value?.origin_close)))
const x = computed(() => linearScale([toTime(start.value), toTime(end.value)], [MARGIN.left, width.value - MARGIN.right]))
const yDomain = computed(() => extent([
  ...prices.value.map(row => row.close),
  ...(!all.value ? past.value.flatMap(row => [row.predicted_price, row.price_low, row.price_high]) : []),
  current.value?.origin_close, current.value?.predicted_price, current.value?.price_low, current.value?.price_high,
]))
const y = computed(() => linearScale(yDomain.value, [PRICE.value.bottom, all.value ? 96 : PRICE.value.top]))
const probDomain = computed(() => {
  const t = threshold.value
  const [low, high] = extent([...probRows.value.map(row => row.prob_up), current.value?.prob_up, .5, t, isNumber(t) ? 1 - t : null], .15)
  return [Math.max(0, low), Math.min(1, high)]
})
const yProb = computed(() => linearScale(probDomain.value, [PROB.bottom, PROB.top]))
const point = (date, value) => isNumber(value) && Number.isFinite(toTime(date)) ? [x.value(toTime(date)), y.value(value)] : null
const pricePath = computed(() => linePath(prices.value.map(row => point(row.date, row.close))))
const kinds = ['backfill', 'live']
const series = computed(() => kinds.map(kind => {
  const prediction = past.value.map(row => row.kind === kind ? point(row.target_date, row.predicted_price) : null)
  const ranges = past.value.map(row => row.kind === kind && isNumber(row.price_low) && isNumber(row.price_high)
    ? { high: point(row.target_date, row.price_high), low: point(row.target_date, row.price_low) } : null)
  const probability = probRows.value.map(row => row.kind === kind && isNumber(row.prob_up)
    ? [x.value(toTime(row.origin_date)), yProb.value(row.prob_up)] : null)
  return {
    kind, prediction: linePath(prediction), probability: linePath(probability),
    band: bandPath(ranges.map(item => item?.high), ranges.map(item => item?.low)),
    singlePrices: isolatedItems(prediction), singleRanges: isolatedItems(ranges), singleProbabilities: isolatedItems(probability),
  }
}))
const fanPath = row => {
  const from = point(row.origin_date, row.origin_close)
  if (!from || !isNumber(row.price_low) || !isNumber(row.price_high)) return ''
  return `M${from.join(',')} L${point(row.target_date, row.price_high).join(',')} L${point(row.target_date, row.price_low).join(',')} Z`
}
const origin = computed(() => current.value ? point(current.value.origin_date, current.value.origin_close) : null)
const predicted = computed(() => current.value ? point(current.value.target_date, current.value.predicted_price) : null)
const fan = computed(() => current.value ? fanPath(current.value) : '')
const directLabels = computed(() => {
  const row = current.value
  if (!row) return []
  const labels = [
    { value: row.price_high, text: `상한 ${formatNumber(row.price_high, 0)}` },
    { value: row.predicted_price, text: `예측 ${formatNumber(row.predicted_price)}`, predicted: true },
    { value: row.price_low, text: `하한 ${formatNumber(row.price_low, 0)}` },
  ].filter(item => isNumber(item.value)).map(item => ({ ...item, actualY: y.value(item.value), labelY: y.value(item.value) }))
  labels.sort((a, b) => a.actualY - b.actualY)
  for (let i = 1; i < labels.length; i++) labels[i].labelY = Math.max(labels[i].labelY, labels[i - 1].labelY + 16)
  return labels
})
// How it works: 목표일이 지난 예측 하나를 골라 '그날 알 수 있던 것 → 예측 → 실제'를 보여 준다
const story = computed(() => {
  if (props.mode !== 'story') return null
  const done = rows.value.filter(row => isNumber(row.actual_close) && isNumber(row.price_low) && isNumber(row.predicted_price) && row.origin_date >= start.value)
  const row = done[Math.floor(done.length * 0.55)]
  if (!row) return null
  const from = point(row.origin_date, row.origin_close)
  const to = point(row.target_date, row.predicted_price)
  const actual = point(row.target_date, row.actual_close)
  if (!from || !to || !actual) return null
  const near = Math.abs(to[1] - actual[1]) < 16
  return { row, from, to, actual, fan: fanPath(row), predLabelY: near ? actual[1] + (to[1] >= actual[1] ? 16 : -16) : to[1] }
})
const backfillStart = computed(() => past.value.find(row => row.kind === 'backfill' && isNumber(row.predicted_price)))
const yTicks = computed(() => ticks(yDomain.value, 5))
const probTicks = computed(() => ticks(probDomain.value, 3))
const xTicks = computed(() => {
  if (!hasData.value) return []
  const result = []
  if (all.value) {
    const lastYear = Number(end.value?.slice(0, 4))
    const years = width.value < 600 ? [2005, 2015, lastYear] : [2005, 2010, 2015, 2020, lastYear]
    return [...new Set(years)].map(year => ({ time: Math.max(toTime(start.value), toTime(`${year}-01-01`)), label: `${year}` }))
  }
  const date = new Date(toTime(start.value))
  date.setUTCDate(1)
  const step = width.value < 600 ? 4 : 2
  while (date.getTime() <= toTime(end.value)) {
    if (date.getTime() >= toTime(start.value) && date.getUTCMonth() % step === 0) result.push({ time: date.getTime(), label: `${String(date.getUTCFullYear()).slice(2)}.${date.getUTCMonth() + 1}` })
    date.setUTCMonth(date.getUTCMonth() + 1)
  }
  return result
})
// 주석은 실제로 표시하는 관측에서 위치를 정한다. 첫 화면과 단계 그림에는 주석을 달지 않는다.
const annotations = computed(() => {
  if (!full.value) return []
  if (!all.value) {
    const i = prices.value.findIndex(row => row.date === '2026-07-06')
    const row = prices.value[i], previous = prices.value[i - 1]
    if (!row || !isNumber(row.close) || !isNumber(previous?.close) || previous.close === 0) return []
    const [cx, cy] = point(row.date, row.close)
    const lx = Math.max(MARGIN.left, Math.min(cx - 160, width.value - 216))
    return [{ date: row.date, cx, cy, lx, ly: 16, text: `7월 6일 로그수익률 ${formatSigned(Math.log(row.close / previous.close) * 100)}%` }]
  }
  const peak = year => prices.value.filter(row => row.date.startsWith(year) && isNumber(row.close)).reduce((best, row) => !best || row.close > best.close ? row : best, null)
  const frost = prices.value.find(row => row.date >= '2021-07-20' && row.date <= '2021-07-27' && isNumber(row.close))
  return [[peak('2011'), '2011 고점'], [frost, '2021.7 산지 한파 뒤 급등'], [peak('2025'), '2025 고점']]
    .filter(([row]) => row).map(([row, text], i) => {
      const [cx, cy] = point(row.date, row.close)
      return { date: row.date, cx, cy, lx: MARGIN.left + 8, ly: 16 + i * 24, text }
    })
})
function selectPeriod(value) {
  period.value = value
  hover.value = null
  if (value === 'all') emit('load-all')
}
function onMove(event) {
  if (!hasData.value) return
  const rect = event.currentTarget.getBoundingClientRect()
  const time = x.value.invert((event.clientX - rect.left) * width.value / rect.width)
  if (current.value && time > toTime(lastDate.value)) {
    hover.value = { date: current.value.target_date, left: x.value(toTime(current.value.target_date)), future: current.value }
    return
  }
  if (!prices.value.length) return
  const row = prices.value[nearestIndex(prices.value.map(row => toTime(row.date)), time)]
  hover.value = { ...row, left: x.value(toTime(row.date)), past: !all.value && byTarget.value.get(row.date), prob: showProb.value && byOrigin.value.get(row.date) }
}
watch(() => [props.horizon, all.value], () => { hover.value = null })
</script>

<template>
  <section :id="full ? 'price-section' : undefined" :class="['forecast-chart', `mode-${mode}`]" :aria-labelledby="full ? 'chart-title' : undefined">
    <template v-if="full">
      <h2 id="chart-title" class="h2">전체 기간을 직접 살펴보기</h2>
      <div class="chart-controls">
        <div class="seg" role="group" aria-label="지평 선택">
          <span>지평</span>
          <button v-for="h in HORIZONS" :key="h" :aria-pressed="horizon === h" @click="emit('update:horizon', h)">{{ h }}거래일</button>
        </div>
        <div class="seg" role="group" aria-label="가격 조회 기간">
          <span>기간</span>
          <button :aria-pressed="period === 'year'" @click="selectPeriod('year')">최근 1년</button>
          <button :aria-pressed="period === 'all'" @click="selectPeriod('all')">2005년부터</button>
        </div>
      </div>
      <p v-if="period === 'all' && fullLoading" class="cap" role="status">전체 가격을 불러오는 동안 최근 1년을 보여 줍니다…</p>
      <p v-if="period === 'all' && fullError" class="cap" role="status">{{ fullError }} 현재 그래프는 최근 1년입니다. <button class="text-button" @click="emit('load-all')">다시 시도</button></p>
      <p v-if="historyFailed" class="cap">선택한 지평의 과거 예측을 불러오지 못했습니다.</p>
      <ul v-if="!all" class="keys" aria-label="가격 그래프 범례">
        <li v-if="pricePath"><i class="key line" />실제 종가</li>
        <li v-if="series.some(s => s.prediction)"><i class="key pred" />{{ horizon }}거래일 전 예측</li>
        <li v-if="series.some(s => s.band)"><i class="key band" />그때의 80% 범위</li>
        <li v-if="fan"><i class="key fan" />오늘의 예측</li>
      </ul>
    </template>
    <div ref="box" class="chart-box">
      <svg v-if="hasData" :width="width" :height="height" role="img" :class="mode === 'story' ? `step-${step}` : null"
        :aria-label="`${all ? '2005년 이후 주간' : '최근 1년 일별'} 종가와 ${horizon}거래일 예측.${showProb ? ' 아래 칸은 기준일별 상승 확률입니다.' : ''}${full ? ' 값은 아래 표에서도 확인할 수 있습니다.' : ''}`"
        @pointermove="onMove" @pointerleave="hover = null" @pointercancel="hover = null">
        <g class="grid"><line v-for="tick in yTicks" :key="tick" :x1="MARGIN.left" :x2="width - MARGIN.right" :y1="y(tick)" :y2="y(tick)" /></g>
        <g class="axis-label">
          <text v-for="tick in yTicks" :key="tick" :x="MARGIN.left - 8" :y="y(tick) + 4" text-anchor="end">{{ Math.round(tick) }}</text>
          <text v-for="tick in xTicks" :key="tick.time" :x="x(tick.time)" :y="height - 8" text-anchor="middle">{{ tick.label }}</text>
          <text :x="MARGIN.left - 32" :y="all ? 88 : PRICE.top - 14">센트/파운드</text>
        </g>
        <g v-if="!all" class="L-past">
          <path v-for="s in series" :key="`band-${s.kind}`" :d="s.band" :class="['band', s.kind]" />
          <path v-for="s in series" :key="`pred-${s.kind}`" :d="s.prediction" :class="['pred', s.kind]" />
          <g v-for="s in series" :key="`single-${s.kind}`">
            <rect v-for="(range, i) in s.singleRanges" :key="`range-${i}`" :class="['band', 'isolated-band', s.kind]" width="2"
              :x="range.high[0] - 1" :y="range.high[1]" :height="range.low[1] - range.high[1]" />
            <circle v-for="(p, i) in s.singlePrices" :key="`price-${i}`" :class="['pred-dot', 'isolated-pred', s.kind]" r="3" :cx="p[0]" :cy="p[1]" />
          </g>
        </g>
        <path :key="`price-${all}-${mode}`" :d="pricePath" class="price enter" pathLength="1" />
        <g v-if="story" class="L-mask">
          <rect class="mask" :x="story.from[0] + 1" :y="PRICE.top - 4" :width="Math.max(0, width - MARGIN.right - story.from[0] + 4)" :height="PRICE.bottom - PRICE.top + 4" />
          <line class="origin-rule" :x1="story.from[0]" :x2="story.from[0]" :y1="PRICE.top" :y2="PRICE.bottom" />
          <text class="note" :x="story.from[0] + 8" :y="PRICE.top + 12">{{ story.row.origin_date }}에는 여기까지만 안다</text>
        </g>
        <g v-if="story" class="L-one">
          <path class="fan" :d="story.fan" />
          <line class="pred solid" :x1="story.from[0]" :y1="story.from[1]" :x2="story.to[0]" :y2="story.to[1]" />
          <circle class="pred-dot" r="4.5" :cx="story.to[0]" :cy="story.to[1]" />
          <circle class="actual-dot" r="4.5" :cx="story.actual[0]" :cy="story.actual[1]" />
          <text class="direct-label predicted" :x="story.actual[0] + 10" :y="story.actual[1] + 4">실제 {{ formatNumber(story.row.actual_close) }}</text>
          <text class="direct-label" :x="story.to[0] + 10" :y="story.predLabelY + 4">예측 {{ formatNumber(story.row.predicted_price) }}</text>
        </g>
        <g class="L-today">
          <path :key="`fan-${horizon}-${all}`" :d="fan" class="fan enter" />
          <line v-if="origin" class="origin-rule" :x1="origin[0]" :x2="origin[0]" :y1="PRICE.top" :y2="showProb ? PROB.bottom : PRICE.bottom" />
          <text v-if="origin" class="plot-label" :x="Math.max(MARGIN.left, origin[0] - 8)" :y="full ? PRICE.bottom + 24 : PRICE.bottom - 8" text-anchor="end">{{ current.origin_date.slice(5) }} 기준</text>
          <g v-if="predicted && origin">
            <line class="pred solid" :x1="origin[0]" :y1="origin[1]" :x2="predicted[0]" :y2="predicted[1]" />
            <circle class="pred-dot" r="4.5" :cx="predicted[0]" :cy="predicted[1]" />
          </g>
          <text v-for="label in directLabels" :key="label.text" :class="['direct-label', { predicted: label.predicted }]"
            :x="width - MARGIN.right + 8" :y="label.labelY + 4">{{ label.text }}</text>
        </g>
        <text v-if="all && pricePath" class="direct-label" :x="MARGIN.left" :y="PRICE.bottom + 48">실제 종가, 주간 마지막 관측</text>
        <g v-for="a in annotations" :key="a.date" class="annot">
          <circle :cx="a.cx" :cy="a.cy" r="9" />
          <line :x1="a.cx" :y1="a.cy - 9" :x2="Math.min(a.lx + a.text.length * 6, width - MARGIN.right)" :y2="a.ly + 8" />
          <text :x="a.lx" :y="a.ly">{{ a.text }}</text>
        </g>
        <template v-if="showProb">
          <text v-if="backfillStart" class="plot-label" :x="Math.max(MARGIN.left, Math.min(x(toTime(backfillStart.target_date)) + 8, width - 224))" :y="PRICE.bottom + 56">← 여기부터 동결 모델의 소급 계산</text>
          <line v-if="backfillStart" class="origin-rule" :x1="x(toTime(backfillStart.target_date))" :x2="x(toTime(backfillStart.target_date))" :y1="PRICE.bottom" :y2="PRICE.bottom + 40" />
          <text class="plot-label" :x="MARGIN.left" :y="PROB.top - 16">기준일에 낸 상승 확률</text>
          <g class="axis-label"><text v-for="tick in probTicks" :key="tick" :x="MARGIN.left - 8" :y="yProb(tick) + 4" text-anchor="end">{{ formatPercent(tick) }}</text></g>
          <g v-if="isNumber(threshold)" class="threshold-label">
            <line v-for="v in [threshold, 1 - threshold]" :key="v" class="threshold" :x1="MARGIN.left" :x2="width - MARGIN.right" :y1="yProb(v)" :y2="yProb(v)" />
            <text :x="width - MARGIN.right + 8" :y="yProb(threshold) + 4">매수 {{ formatPercent(threshold) }}</text>
            <text :x="width - MARGIN.right + 8" :y="yProb(1 - threshold) + 4">미루기 {{ formatPercent(1 - threshold) }}</text>
          </g>
          <path v-for="s in series" :key="s.kind" :d="s.probability" :class="['prob', s.kind]" />
          <g v-for="s in series" :key="`prob-${s.kind}`">
            <circle v-for="(p, i) in s.singleProbabilities" :key="i" :class="['prob-dot', 'isolated-prob', s.kind]" r="3" :cx="p[0]" :cy="p[1]" />
          </g>
          <circle v-if="origin && isNumber(current.prob_up)" class="prob-dot" :cx="origin[0]" :cy="yProb(current.prob_up)" r="4" />
        </template>
        <line v-if="hover" class="crosshair" :x1="hover.left" :x2="hover.left" :y1="PRICE.top" :y2="showProb ? PROB.bottom : PRICE.bottom" />
      </svg>
      <p v-else class="cap">이 기간에 표시할 가격 자료가 없습니다.</p>
      <div v-if="hover" class="tip" :style="{ left: `${Math.max(0, Math.min(hover.left + 16, width - Math.min(280, width)))}px` }">
        <span class="tip-title">{{ hover.date }}</span>
        <template v-if="hover.future">
          <span>오늘의 {{ horizon }}거래일 예측 <b>{{ formatNumber(hover.future.predicted_price) }}¢</b> ({{ formatReturn(hover.future.predicted_return) }})</span>
          <span>80% 범위 {{ formatNumber(hover.future.price_low) }}~{{ formatNumber(hover.future.price_high) }}¢</span>
          <span>상승 확률 {{ formatPercent(hover.future.prob_up, 1) }}, {{ SIGNAL_LABELS[hover.future.signal] }}</span>
          <small>예측 기준 종가 {{ formatNumber(hover.future.origin_close, 2) }}¢, {{ hover.future.kind === 'backfill' ? '소급 계산' : '실시간 예측' }}</small>
        </template>
        <template v-else>
          <span>종가 <b>{{ formatNumber(hover.close) }}¢</b></span>
          <template v-if="hover.past">
            <span>{{ horizon }}거래일 전 예측 {{ formatNumber(hover.past.predicted_price) }}¢
              <template v-if="isNumber(hover.past.predicted_price) && isNumber(hover.close)">(오차 {{ formatSigned(hover.past.predicted_price - hover.close) }})</template></span>
            <span v-if="isNumber(hover.past.price_low) && isNumber(hover.past.price_high)">80% 범위 {{ formatNumber(hover.past.price_low) }}~{{ formatNumber(hover.past.price_high) }}¢
              <template v-if="isNumber(hover.close)">, {{ hover.past.price_low <= hover.close && hover.close <= hover.past.price_high ? '범위 안' : '범위 밖' }}</template></span>
            <small>예측 기준일 {{ hover.past.origin_date }}, {{ hover.past.kind === 'backfill' ? '소급 계산' : '실시간 예측' }}</small>
          </template>
          <span v-if="hover.prob">이날 낸 상승 확률 {{ formatPercent(hover.prob.prob_up, 1) }}</span>
        </template>
      </div>
    </div>
    <template v-if="full">
      <p v-if="all" class="cap">주간 종가입니다. 과거 예측과 상승 확률은 최근 1년 보기에서 자세히 확인할 수 있습니다.</p>
      <p class="cap">실시간 예측은 진하게, 소급 계산은 옅게 표시합니다. 2026-01-02부터의 소급 계산은 동결한 모델로 나중에 계산한 값입니다. 예측 부채꼴은 모델이 사용한 기준 종가에서 시작하므로 가격 표의 종가와 다를 수 있습니다.</p>
      <details>
        <summary>표로 보기: 최근 20개 예측과 모델</summary>
        <p class="cap">예측 가격 {{ modelFor(metadata, 'return', horizon) ?? '확인 불가' }}, 예상 범위 {{ modelFor(metadata, 'volatility', horizon) ?? '확인 불가' }}, 상승 확률 {{ modelFor(metadata, 'direction', horizon) ?? '확인 불가' }}</p>
        <div class="table-wrap"><table>
          <thead><tr><th>기준일</th><th>목표일</th><th>구분</th><th class="num">기준 종가</th><th class="num">예측 가격</th><th class="num">상승 확률</th><th>신호</th><th class="num">80% 범위</th><th class="num">실제 종가</th></tr></thead>
          <tbody><tr v-for="row in rows.slice(-20).reverse()" :key="`${row.origin_date}-${row.kind}`">
            <td>{{ row.origin_date }}</td><td>{{ row.target_date }}</td><td>{{ row.kind === 'backfill' ? '소급' : '실시간' }}</td>
            <td class="num">{{ formatNumber(row.origin_close) }}</td><td class="num">{{ formatNumber(row.predicted_price) }}</td><td class="num">{{ formatPercent(row.prob_up, 1) }}</td>
            <td>{{ SIGNAL_LABELS[row.signal] ?? '-' }}</td><td class="num">{{ formatNumber(row.price_low) }}~{{ formatNumber(row.price_high) }}</td><td class="num">{{ formatNumber(row.actual_close) }}</td>
          </tr></tbody>
        </table></div>
      </details>
    </template>
  </section>
</template>
