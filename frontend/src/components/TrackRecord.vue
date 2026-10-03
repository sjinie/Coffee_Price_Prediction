<script setup>
import { computed } from 'vue'
import { HORIZONS, formatPercent, trackRecord } from '../lib.js'

const props = defineProps({ history: { type: Object, required: true } })
const records = computed(() => HORIZONS.map(h => ({ horizon: h, ...trackRecord(props.history[h] || []) })))
</script>

<template>
  <section aria-labelledby="record-title">
    <h2 id="record-title">지금까지의 적중 기록</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>지평</th><th class="num">채점한 예측<small>(실시간)</small></th><th class="num">신호 빈도</th>
            <th class="num">신호 적중률</th><th class="num">실제 상승 비율<small>(늘 '구매'일 때의 적중률)</small></th>
            <th class="num">80% 범위 적중률</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in records" :key="row.horizon">
            <td>{{ row.horizon }}거래일</td>
            <td class="num">{{ row.evaluated }} <span class="muted">({{ row.live }})</span></td>
            <td class="num">{{ formatPercent(row.signalRate) }}</td>
            <td class="num">{{ formatPercent(row.signalHit) }}</td>
            <td class="num">{{ formatPercent(row.upRate) }}</td>
            <td class="num">{{ row.horizon === 5 ? '-' : formatPercent(row.rangeHit) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="note">
      2026년 1월부터의 예측은 모델을 동결(2026-10-03)한 뒤 소급 계산한 값입니다. 이 기간은 개발 중에 이미 본 적이 있어
      처음 보는 자료에 대한 평가가 아닙니다. 괄호 안의 실시간 예측이 쌓여야 진짜 성적을 알 수 있습니다.
    </p>
  </section>
</template>
