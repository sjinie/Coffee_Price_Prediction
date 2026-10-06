<script setup>
import { computed } from 'vue'
import { SIGNAL_LABELS, formatNumber, formatPercent, modelFor, riskLevel } from '../lib.js'
import { DECISION_REASONS } from '../research.js'

// 첫 화면 카드의 각 값이 왜 거기 있는지. 값과 모델 이름, 매수 기준은 API와 metadata에서 읽는다.
const props = defineProps({ forecast: { type: Object, default: null }, metadata: { type: Object, default: null } })
// 기준이 null이면 근거 있는 기준을 찾지 못해 그 지평은 신호를 내지 않는다(노트북 07)
const thresholds = computed(() => [5, 20, 60]
  .map(h => [h, props.metadata?.distribution?.horizons?.[h]?.threshold])
  .map(([h, t]) => `${h}일 ${t === null ? '신호 없음' : formatPercent(t)}`).join(', '))
const model = computed(() => modelFor(props.metadata, 'distribution', props.forecast?.horizon) ?? '모델 확인 불가')
const rows = computed(() => {
  const f = props.forecast
  if (!f) return []
  return [
    { key: 'signal', name: SIGNAL_LABELS[f.signal] ?? '신호', value: '', why: `${DECISION_REASONS.signal} 기준: ${thresholds.value}.` },
    { key: 'range', name: '80% 범위', value: `${formatNumber(f.price_low)} ~ ${formatNumber(f.price_high)}`, why: `${model.value}. ${DECISION_REASONS.range}` },
    { key: 'price', name: '가운데 가격', value: formatNumber(f.predicted_price), why: DECISION_REASONS.price },
    { key: 'prob', name: '상승 확률', value: formatPercent(f.prob_up, 1), why: DECISION_REASONS.prob },
    { key: 'risk', name: '위험 수준', value: riskLevel(f.vol_percentile), why: DECISION_REASONS.risk },
  ]
})
</script>

<template>
  <dl v-if="forecast" class="spec">
    <div class="head"><dt>{{ forecast.horizon }}거래일 뒤 카드</dt><dd>이 값이 화면에 있는 이유</dd></div>
    <div v-for="row in rows" :key="row.key"><dt>{{ row.name }}<b v-if="row.value" class="num">{{ row.value }}</b></dt><dd>{{ row.why }}</dd></div>
  </dl>
</template>
