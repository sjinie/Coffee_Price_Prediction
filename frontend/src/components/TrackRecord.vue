<script setup>
import { computed } from 'vue'
import { HORIZONS, formatNumber, formatPercent, trackRecordByKind } from '../lib.js'

const props = defineProps({ history: { type: Object, required: true }, failedHorizons: { type: Array, default: () => [] } })
const byHorizon = computed(() => HORIZONS.map(h => ({ horizon: h, ...trackRecordByKind(props.history[h] || []) })))
const sections = [
  { kind: 'live', title: '실시간 예측', note: '모델을 동결한 뒤 매일 저장한 예측입니다. 처음 보는 자료에 대한 성적은 이 표뿐입니다.' },
  { kind: 'backfill', title: '소급 계산 (2026년 1월~)', note: '동결한 모델로 나중에 계산한 값입니다. 이 기간은 개발 중에 이미 본 적이 있어 참고용입니다.' },
]
const hasLive = computed(() => byHorizon.value.some(row => row.live.evaluated > 0))
</script>

<template>
  <div class="record-block" aria-labelledby="record-title">
    <h3 id="record-title" class="h3">진짜 평가는 이제부터다.</h3>
    <p class="prose">모델은 동결한 날 이후 매일 쌓이는 실시간 예측으로 처음 평가받습니다. 소급 계산은 같은 모델을 지난 기간에 적용한 참고 성적입니다.</p>
    <div v-for="section in sections" :key="section.kind" class="record">
      <h4>{{ section.title }}</h4>
      <p class="cap">{{ section.note }}</p>
      <p v-if="section.kind === 'live' && !hasLive && !failedHorizons.length" class="cap">아직 목표일이 지난 실시간 예측이 없습니다.</p>
      <div v-else class="table-wrap">
        <table class="score">
          <thead>
            <tr>
              <th>지평</th><th class="num">채점한 예측</th>
              <th class="num">방향 적중률<small>(가운데 가격 기준)</small></th>
              <th class="num">가격 오차<small>(가운데 가격 / 현재가 유지, ¢/lb)</small></th>
              <th class="num">80% 범위 적중률</th>
              <th class="num">신호 빈도</th><th class="num">신호 적중률</th>
              <th class="num">같은 날 늘 '구매'<small>(신호를 낸 날의 상승 비율)</small></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in byHorizon" :key="row.horizon">
              <td>{{ row.horizon }}거래일</td>
              <td v-if="failedHorizons.includes(row.horizon)" colspan="7">기록 조회 실패 · 성적을 확인할 수 없습니다.</td>
              <template v-else>
              <td class="num">{{ row[section.kind].evaluated }}</td>
              <td class="num">{{ formatPercent(row[section.kind].returnHit) }}</td>
              <td class="num">{{ formatNumber(row[section.kind].maeModel) }} / {{ formatNumber(row[section.kind].maeNaive) }}</td>
              <td class="num">{{ formatPercent(row[section.kind].rangeHit) }}</td>
              <td class="num">{{ formatPercent(row[section.kind].signalRate) }}</td>
              <td class="num">{{ formatPercent(row[section.kind].signalHit) }}</td>
              <td class="num">{{ formatPercent(row[section.kind].signalUpRate) }}</td>
              </template>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
