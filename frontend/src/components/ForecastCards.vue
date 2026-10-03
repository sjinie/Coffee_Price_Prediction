<script setup>
import { SIGNAL_LABELS, formatNumber, formatPercent, isNumber, riskLevel } from '../lib.js'

defineProps({
  forecasts: { type: Array, required: true },
  thresholds: { type: Object, default: () => ({}) }, // 지평별 신호 기준(모델 metadata)
})
const ICONS = { buy: '▲', wait: '▼', hold: '■' }
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
            <dt>상승 확률</dt>
            <dd>{{ formatPercent(item.prob_up, 1) }} <span class="muted">(기준 {{ formatPercent(thresholds[item.horizon]) }})</span></dd>
          </div>
          <template v-if="isNumber(item.price_low)">
            <div>
              <dt>80% 예상 범위</dt>
              <dd>{{ formatNumber(item.price_low) }} ~ {{ formatNumber(item.price_high) }}</dd>
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
                  <span :style="{ left: `${item.vol_percentile * 100}%` }" />
                </span>
              </dd>
            </div>
          </template>
        </dl>
        <p v-if="!isNumber(item.price_low)" class="muted small">5일 변동성 모델은 검증하지 않아 범위를 표시하지 않습니다.</p>
      </article>
    </div>
    <p class="note">
      중심 전망은 현재가({{ formatNumber(forecasts[0]?.origin_close) }} ¢/lb)입니다. 수익률 크기를 예측한 모델은 현재가 유지보다 정확하지 않았습니다.
      신호는 상승 확률이 기준 이상이면 '지금 구매', (1 − 기준) 이하이면 '미루기'이고, 그 사이는 '보류'입니다.
    </p>
  </section>
</template>
