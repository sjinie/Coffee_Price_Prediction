<script setup>
import { computed } from 'vue'
import { HORIZONS, formatNumber, formatPercent, trackRecordByKind } from '../lib.js'

const props = defineProps({ history: { type: Object, required: true } })
const byHorizon = computed(() => HORIZONS.map(h => ({ horizon: h, ...trackRecordByKind(props.history[h] || []) })))
const sections = [
  { kind: 'live', title: '실시간 예측', note: '모델을 동결한 뒤 매일 저장한 예측입니다. 처음 보는 자료에 대한 성적은 이 표뿐입니다.' },
  { kind: 'backfill', title: '소급 계산 (2026년 1월~)', note: '동결한 모델로 나중에 계산한 값입니다. 이 기간은 개발 중에 이미 본 적이 있어 참고용입니다.' },
]
const hasLive = computed(() => byHorizon.value.some(row => row.live.evaluated > 0))
</script>

<template>
  <section aria-labelledby="record-title">
    <h2 id="record-title">지금까지의 적중 기록</h2>
    <div v-for="section in sections" :key="section.kind" class="record">
      <h3>{{ section.title }}</h3>
      <p class="note">{{ section.note }}</p>
      <p v-if="section.kind === 'live' && !hasLive" class="muted small">아직 목표일이 지난 실시간 예측이 없습니다.</p>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>지평</th><th class="num">채점한 예측</th>
              <th class="num">수익률 방향 적중률</th>
              <th class="num">가격 오차<small>(모델 / 현재가 유지, ¢/lb)</small></th>
              <th class="num">80% 범위 적중률</th>
              <th class="num">신호 빈도</th><th class="num">신호 적중률</th>
              <th class="num">실제 상승 비율<small>(늘 '구매'일 때의 적중률)</small></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in byHorizon" :key="row.horizon">
              <td>{{ row.horizon }}거래일</td>
              <td class="num">{{ row[section.kind].evaluated }}</td>
              <td class="num">{{ formatPercent(row[section.kind].returnHit) }}</td>
              <td class="num">{{ formatNumber(row[section.kind].maeModel) }} / {{ formatNumber(row[section.kind].maeNaive) }}</td>
              <td class="num">{{ formatPercent(row[section.kind].rangeHit) }}</td>
              <td class="num">{{ formatPercent(row[section.kind].signalRate) }}</td>
              <td class="num">{{ formatPercent(row[section.kind].signalHit) }}</td>
              <td class="num">{{ formatPercent(row[section.kind].upRate) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
