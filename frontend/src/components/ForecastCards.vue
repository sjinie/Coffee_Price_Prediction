<script setup>
import { SIGNAL_LABELS, formatNumber, formatPercent, formatReturn, formatSigned, isNumber, riskLevel } from '../lib.js'
const props = defineProps({
  forecasts: { type: Array, required: true },
  metadata: { type: Object, default: null },
  horizon: { type: Number, required: true },
})
const emit = defineEmits(['update:horizon'])
const ICONS = { buy: '▲', wait: '▼', hold: '■' }
const checks = h => props.metadata?.return?.horizons?.[h]
const signedPercent = value => isNumber(value) ? `${formatSigned(value)}%` : '확인 불가'
</script>

<template>
  <section class="forecast-section" aria-labelledby="forecast-title">
    <p class="eyebrow">세 가지 시간</p>
    <h2 id="forecast-title">얼마나 멀리 내다볼까</h2>
    <div class="forecast-columns" role="group" aria-label="전망 지평 선택">
      <button v-for="item in forecasts" :key="item.horizon" class="forecast-column"
        :aria-pressed="horizon === item.horizon" @click="emit('update:horizon', item.horizon)">
        <span class="column-label">{{ item.horizon }}거래일 뒤 · {{ item.target_date }}</span>
        <span class="forecast-price">{{ formatNumber(item.predicted_price) }}<span class="unit">¢/lb</span></span>
        <span>{{ formatReturn(item.predicted_return) }} · 범위 {{ formatNumber(item.price_low, 0) }}–{{ formatNumber(item.price_high, 0) }}¢</span>
        <span class="signal" :class="item.signal"><span aria-hidden="true">{{ ICONS[item.signal] }}</span> {{ SIGNAL_LABELS[item.signal] }} · 상승 {{ formatPercent(item.prob_up, 1) }}</span>
        <span>위험 {{ riskLevel(item.vol_percentile) }} · 최근 3년 중 {{ formatPercent(item.vol_percentile) }}</span>
        <span class="validation">보류 구간 검증: 현재가 유지 대비 오차 {{ signedPercent(checks(item.horizon)?.holdout?.rmse_vs_naive_pct) }}, 방향 정확도 {{ formatPercent(checks(item.horizon)?.holdout?.direction_acc) }}</span>
      </button>
    </div>
    <p class="honesty">
      예측 가격은 과거 검증에서 현재가를 그대로 쓰는 것보다 오차가 컸습니다. 기준선을 분명히 넘은 것은 5·20일 변동성 예측, 곧 범위의 폭뿐입니다.
      <template v-if="forecasts.every(item => item.kind === 'backfill')">현재 표시된 예측은 동결 모델로 소급 계산한 값입니다. 실시간 성적과 구분해서 보아야 합니다.</template>
      <template v-else>동결 모델의 소급 계산과 이후 저장한 실시간 예측의 성적은 아래에서 따로 확인합니다.</template>
    </p>
    <details class="model-details">
      <summary>변동성과 검증 숫자 자세히 보기</summary>
      <div class="table-wrap"><table>
        <thead><tr><th>지평</th><th>예측 변동성(연율)</th><th>현재가 유지 대비 오차(개발)</th><th>구매 / 미루기 기준</th></tr></thead>
        <tbody><tr v-for="item in forecasts" :key="item.horizon">
          <td>{{ item.horizon }}거래일</td><td>{{ formatPercent(item.predicted_vol) }}</td>
          <td>{{ signedPercent(checks(item.horizon)?.dev?.rmse_vs_naive_pct) }}</td>
          <td>{{ formatPercent(metadata?.direction?.horizons?.[item.horizon]?.threshold) }} /
            {{ isNumber(metadata?.direction?.horizons?.[item.horizon]?.threshold) ? formatPercent(1 - metadata.direction.horizons[item.horizon].threshold) : '-' }}</td>
        </tr></tbody>
      </table></div>
    </details>
  </section>
</template>
