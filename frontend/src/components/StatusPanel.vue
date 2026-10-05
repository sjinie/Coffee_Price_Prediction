<script setup>
import { HORIZONS, formatTime, modelFor } from '../lib.js'

defineProps({ model: { type: Object, default: null }, status: { type: Object, default: null } })
const STATUS = { success: '성공', warning: '경고', failed: '실패', running: '실행 중' }
</script>

<template>
  <div aria-labelledby="status-title">
    <h3 id="status-title" class="h4">만든 방법</h3>
    <p class="flow"><b>Yahoo, FRED, NOAA, NASA, Google News</b><i>→</i><b>GitHub Actions</b><span>매일 수집, 피처, 예측, 뉴스 분류</span><i>→</i><b>PostgreSQL</b><i>→</i><b>FastAPI</b><i>→</i><b>Vue</b></p>
    <dl class="facts-list compact">
      <div><dt>모델 버전</dt><dd>{{ model?.model_version ?? '-' }}, 학습 구간 끝 {{ model?.train_end ?? '-' }}</dd></div>
      <div><dt>예측 모델</dt><dd>{{ model?.metadata?.distribution?.algorithm ?? '-' }}. 범위·가운데 가격·상승 확률·신호·위험 수준이 모두 같은 분포에서 나온다.</dd></div>
      <div><dt>지평별 설정</dt><dd>{{ HORIZONS.map(h => `${h}일 ${modelFor(model?.metadata, 'distribution', h) ?? '-'}`).join(', ') }}</dd></div>
      <div><dt>최신 가격</dt><dd>{{ status?.latest_price_date ?? '-' }}</dd></div>
    </dl>
    <details v-if="status?.runs?.length">
      <summary>최근 실행 기록</summary>
      <div class="table-wrap">
        <table>
          <thead><tr><th>실행</th><th>상태</th><th>시작(KST)</th><th>메시지</th></tr></thead>
          <tbody>
            <tr v-for="run in status.runs" :key="run.run_id">
              <td>{{ run.command }}</td>
              <td><span class="run" :class="run.status">{{ STATUS[run.status] ?? run.status }}</span></td>
              <td>{{ formatTime(run.started_at) }}</td>
              <td class="cap">{{ run.message ?? '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </div>
</template>
