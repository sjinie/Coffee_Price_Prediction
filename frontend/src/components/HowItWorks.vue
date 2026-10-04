<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { extent, isNumber, linePath, linearScale } from '../lib.js'
import { RESEARCH } from '../research.js'
import PriceChart from './PriceChart.vue'

defineProps({ prices: { type: Array, required: true }, history: { type: Object, required: true }, latest: { type: Array, required: true } })

// 각 값을 언제부터 쓰는지는 docs/architecture.md '시점 계약'과 같아야 한다
const SPARKS = [
  { key: 'price', name: '커피 선물 종가', when: '당일부터 사용' },
  { key: 'brl', name: '브라질 헤알', when: '최초 공개 다음 날부터' },
  { key: 'rate', name: '미국 금리', when: '최초 공개 다음 날부터' },
  { key: 'rain', name: '브라질 산지 강수', when: '관측 4일 뒤부터' },
  { key: 'news', name: '뉴스 점수, 2022년부터', when: '분석이 끝난 뒤 첫 마감부터' },
]
const STEPS = [
  '2005년부터 매일의 종가가 쌓인다.',
  '어느 하루에 서서, 그날 실제로 알 수 있던 값만 본다.',
  '그 값으로 20거래일 뒤의 가격과 80% 범위를 예측하고, 나중에 실제 가격과 맞춰 본다.',
  '이 일을 매일 반복하면 예측이 실제 가격 옆에 한 줄로 쌓인다.',
  '맨 오른쪽 부채꼴이 오늘의 예측이다. 매일 장 마감(23:00 UTC) 뒤 다시 계산한다.',
]
function sparkPath(values = []) {
  const x = linearScale([0, Math.max(values.length - 1, 1)], [0, 200])
  const y = linearScale(extent(values, 0), [54, 2])
  return linePath(values.map((v, i) => (isNumber(v) ? [x(i), y(v)] : null)))
}

const step = ref(1)
const stepList = ref(null)
let observer
onMounted(() => {
  // 화면 가운데를 지나는 문장이 현재 단계다
  observer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) step.value = Number(entry.target.dataset.step)
  }), { rootMargin: '-45% 0px -45% 0px' })
  stepList.value.querySelectorAll('[data-step]').forEach(node => observer.observe(node))
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <section class="block how" aria-labelledby="how-title">
    <div class="inner">
      <h2 id="how-title" class="h2">하루치 예측은 이렇게 만들어진다</h2>
      <div class="sparks">
        <figure v-for="item in SPARKS" :key="item.key" class="spark">
          <svg viewBox="0 0 200 56" preserveAspectRatio="none" aria-hidden="true"><path :d="sparkPath(RESEARCH.sparks[item.key])" /></svg>
          <figcaption><span class="spark-name">{{ item.name }}</span><span class="cap">{{ item.when }}</span></figcaption>
        </figure>
      </div>
      <div class="scrolly">
        <div class="stage">
          <PriceChart mode="story" :step="step" :horizon="20" :prices="prices" :history="history" :latest="latest" />
        </div>
        <ol ref="stepList" class="steps">
          <li v-for="(text, i) in STEPS" :key="i" :data-step="i + 1" :class="{ on: step === i + 1 }"><p>{{ text }}</p></li>
        </ol>
      </div>
    </div>
  </section>
</template>
