<script setup>
import { SIGNAL_LABELS, formatNumber, formatPercent, formatReturn, formatSigned, isNumber, riskLevel } from '../lib.js'

const props = defineProps({
  forecasts: { type: Array, required: true },
  metadata: { type: Object, default: null }, // 활성 모델의 metadata: 지평별 신호 기준과 수익률 모델의 검증 성적
})
const ICONS = { buy: '▲', wait: '▼', hold: '■' }
const threshold = h => props.metadata?.direction?.horizons?.[h]?.threshold
const checks = h => props.metadata?.return?.horizons?.[h]
const signedPercent = value => (isNumber(value) ? `${formatSigned(value)}%` : '-')
</script>

<template>
  <section aria-labelledby="forecast-title">
    <h2 id="forecast-title">지평별 전망</h2>
    <div class="cards">
      <article v-for="item in forecasts" :key="item.horizon" class="card">
        <header>
          <h3>{{ item.horizon }}거래일 뒤</h3>
          <span class="muted">목표일 {{ item.target_date }}</span>
        </header>
        <p class="signal" :class="item.signal">
          <span aria-hidden="true">{{ ICONS[item.signal] }}</span> {{ SIGNAL_LABELS[item.signal] }}
        </p>
        <dl>
          <div>
            <dt>예측 가격</dt>
            <dd>{{ formatNumber(item.predicted_price) }} <span class="muted">({{ formatReturn(item.predicted_return) }})</span></dd>
          </div>
          <div>
            <dt>80% 예상 범위</dt>
            <dd>{{ formatNumber(item.price_low) }} ~ {{ formatNumber(item.price_high) }}</dd>
          </div>
          <div>
            <dt>상승 확률</dt>
            <dd>{{ formatPercent(item.prob_up, 1) }} <span class="muted">(기준 {{ formatPercent(threshold(item.horizon)) }})</span></dd>
          </div>
          <div>
            <dt>예측 변동성(연율)</dt>
            <dd>{{ formatPercent(item.predicted_vol) }}</dd>
          </div>
          <div class="risk">
            <dt>위험 수준</dt>
            <dd>
              {{ riskLevel(item.vol_percentile) }} <span class="muted">(최근 3년 중 {{ formatPercent(item.vol_percentile) }})</span>
              <span class="meter" role="img" :aria-label="`최근 3년 대비 변동성 위치 ${formatPercent(item.vol_percentile)}`">
                <span v-if="isNumber(item.vol_percentile)" :style="{ left: `${item.vol_percentile * 100}%` }" />
              </span>
            </dd>
          </div>
        </dl>
        <p v-if="checks(item.horizon)" class="muted small">
          예측 가격의 과거 검증: 현재가 유지 대비 오차(RMSE) {{ signedPercent(checks(item.horizon).dev?.rmse_vs_naive_pct) }} (개발)
          · {{ signedPercent(checks(item.horizon).holdout?.rmse_vs_naive_pct) }} (보류), 방향 정확도
          {{ formatPercent(checks(item.horizon).holdout?.direction_acc) }} (보류)
        </p>
      </article>
    </div>
    <p class="note">
      기준일 종가는 {{ formatNumber(forecasts[0]?.origin_close) }} ¢/lb입니다. 예측 가격은 지평별 수익률 모델의 값이고, 80% 범위는 예측 가격을 가운데에 둔 구간입니다.
      수익률 모델은 과거 검증에서 현재가를 그대로 쓰는 것보다 오차가 컸습니다(카드 아래 숫자). 실제 성적은 아래 적중 기록에 쌓입니다.
      신호는 상승 확률이 기준 이상이면 '지금 구매', (1 − 기준) 이하이면 '미루기'이고, 그 사이는 '보류'입니다.
    </p>
  </section>
</template>
