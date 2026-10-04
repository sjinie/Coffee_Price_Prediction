<script setup>
import { LIMITS, VERDICTS } from '../research.js'
import StatusPanel from './StatusPanel.vue'
import TrackRecord from './TrackRecord.vue'

defineProps({
  history: { type: Object, required: true }, failedHorizons: { type: Array, default: () => [] },
  model: { type: Object, default: null }, status: { type: Object, default: null },
})
</script>

<template>
  <section class="block wrapup" aria-labelledby="wrap-title">
    <div class="inner">
      <h2 id="wrap-title" class="h2">지금까지 확인한 것</h2>
      <ul class="verdicts">
        <li v-for="v in VERDICTS" :key="v.subject" :class="{ yes: v.yes }"><span>{{ v.subject }}</span><span>{{ v.verdict }}</span></li>
      </ul>
      <TrackRecord :history="history" :failed-horizons="failedHorizons" />
      <div class="cols2">
        <div>
          <h3 class="h4">한계</h3>
          <ul class="dash-list"><li v-for="item in LIMITS" :key="item">{{ item }}</li></ul>
        </div>
        <StatusPanel :model="model" :status="status" />
      </div>
    </div>
  </section>
</template>
