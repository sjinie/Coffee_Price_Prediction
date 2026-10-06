<script setup>
import { SIGNAL_LABELS, formatNumber, formatPercent, formatReturn, riskLevel } from '../lib.js'

// 첫 화면의 지평 카드. 카드를 누르면 위 차트가 그 지평으로 바뀐다. 기준 설명은 Why 섹션에 둔다.
defineProps({ forecasts: { type: Array, required: true }, horizon: { type: Number, required: true } })
const emit = defineEmits(['update:horizon'])
</script>

<template>
  <div class="hz-row" role="group" aria-label="전망 지평 선택">
    <button v-for="item in forecasts" :key="item.horizon" class="hz" :aria-pressed="horizon === item.horizon"
      @click="emit('update:horizon', item.horizon)">
      <span class="hz-top">
        <span class="hz-h">{{ item.horizon }}거래일 뒤</span>
        <span class="call" :class="item.signal"><i class="glyph" aria-hidden="true" />{{ SIGNAL_LABELS[item.signal] ?? '확인 불가' }}</span>
      </span>
      <!-- 맞히려는 것은 가격 하나가 아니라 범위다. 그래서 80% 범위를 가장 크게, 분포의 가운데는 그 아래에 둔다 -->
      <span class="hz-price">{{ formatNumber(item.price_low, 0) }}–{{ formatNumber(item.price_high, 0) }}<small>80% 범위</small></span>
      <span class="hz-rows">
        <span><span>가운데 가격</span><span class="num">{{ formatNumber(item.predicted_price) }} ({{ formatReturn(item.predicted_return) }})</span></span>
        <span><span>상승 확률</span><span class="num">{{ formatPercent(item.prob_up, 1) }}</span></span>
        <span><span>예측 변동성(연율)</span><span class="num">{{ formatPercent(item.predicted_vol) }}</span></span>
        <span><span>위험 수준</span><span class="risk"><span class="scale" aria-hidden="true"><i :style="{ '--p': item.vol_percentile ?? 0.5 }" /></span>{{ riskLevel(item.vol_percentile) }}</span></span>
      </span>
    </button>
  </div>
</template>
