<script setup>
import { formatTime } from '../lib.js'

defineProps({ model: { type: Object, default: null }, status: { type: Object, default: null } })
const STATUS = { success: '성공', warning: '경고', failed: '실패', running: '실행 중' }
</script>

<template>
  <section aria-labelledby="status-title">
    <h2 id="status-title">모델과 실행 상태</h2>
    <dl class="facts">
      <div><dt>모델 버전</dt><dd>{{ model?.model_version ?? '-' }}</dd></div>
      <div><dt>학습 구간 끝</dt><dd>{{ model?.train_end ?? '-' }}</dd></div>
      <div><dt>최신 가격</dt><dd>{{ status?.latest_price_date ?? '-' }}</dd></div>
    </dl>
    <div class="table-wrap">
      <table>
        <thead><tr><th>실행</th><th>상태</th><th>시작(KST)</th><th>메시지</th></tr></thead>
        <tbody>
          <tr v-for="run in status?.runs ?? []" :key="run.run_id">
            <td>{{ run.command }}</td>
            <td><span class="run" :class="run.status">{{ STATUS[run.status] ?? run.status }}</span></td>
            <td>{{ formatTime(run.started_at) }}</td>
            <td class="muted">{{ run.message ?? '' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
