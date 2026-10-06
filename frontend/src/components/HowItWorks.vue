<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { activeStep, extent, isNumber, linePath, linearScale } from '../lib.js'
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
// 스크롤하면 문장이 하나씩 쌓이고, 오른쪽(좁은 화면은 위) 차트가 단계마다 한 겹씩 바뀐다(PriceChart의 step-n).
const STEPS = [
  { title: '모았다', text: '2005년부터 커피 선물 종가와 환율·금리·유가, 산지 날씨를 매일 모았다. 뉴스는 2022년부터다.' },
  { title: '알려진 날부터만 썼다', text: '어느 하루에 서면 그날 실제로 알 수 있던 값만 쓴다. 날씨는 관측 4일 뒤, 경제지표는 공개 다음 날, 뉴스는 분석이 끝난 뒤 첫 마감부터다.' },
  { title: '예측했다', text: '그 값으로 20거래일 뒤 가격의 분포를 예측한다. 부채꼴이 80% 범위, 주황 점이 분포의 가운데다. 목표일이 지나면 흰 점, 실제 가격과 맞춰 본다.' },
  { title: '한 장의 차트로 그렸다', text: '매일의 예측을 목표일에 맞춰 놓으면 실제 가격 옆에 한 줄로 쌓인다.', legend: true },
  { title: '매일 갱신된다', text: '매일 장 마감(23:00 UTC) 뒤 새 자료로 다시 계산해, 오른쪽 끝에 오늘의 부채꼴을 더한다.' },
]
function sparkPath(values = []) {
  const x = linearScale([0, Math.max(values.length - 1, 1)], [0, 200])
  const y = linearScale(extent(values, 0), [54, 2])
  return linePath(values.map((v, i) => (isNumber(v) ? [x(i), y(v)] : null)))
}

const step = ref(1)
const stepList = ref(null)
const stage = ref(null)
let observer, sizeObserver
function watchSteps() {
  observer?.disconnect()
  const items = [...stepList.value.querySelectorAll('[data-step]')]
  if (matchMedia('(max-width: 860px)').matches) {
    // 좁은 화면: 차트가 위에 고정되므로 차트 바로 아래에서 온전히 보이는 첫 문단이 현재 단계다.
    // 관찰은 문단이 차트 아래 영역에 들어오고 나가는 순간을 알려 주는 용도이고, 판정은 activeStep이 한다.
    const paragraphs = items.map(li => li.firstElementChild)
    const update = () => {
      const found = activeStep(paragraphs.map((p, i) => {
        const r = p.getBoundingClientRect()
        return { step: Number(items[i].dataset.step), top: r.top, bottom: r.bottom }
      }), stage.value.getBoundingClientRect().bottom, innerHeight)
      if (found) step.value = found
    }
    observer = new IntersectionObserver(update, { rootMargin: `-${stage.value.offsetHeight}px 0px 0px 0px`, threshold: [0, 1] })
    paragraphs.forEach(p => observer.observe(p))
  } else {
    // 넓은 화면: 차트가 옆에 고정되므로 화면 가운데 띠를 지나는 문장이 현재 단계다
    observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) step.value = Number(entry.target.dataset.step)
    }), { rootMargin: '-45% 0px -45% 0px' })
    items.forEach(li => observer.observe(li))
  }
}
onMounted(() => {
  // 화면 폭이나 고정 차트 높이가 바뀌면(넓은↔좁은 배치 전환 포함) 관찰을 다시 건다
  sizeObserver = new ResizeObserver(watchSteps)
  sizeObserver.observe(stage.value)
})
onBeforeUnmount(() => { observer?.disconnect(); sizeObserver?.disconnect() })
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
        <div ref="stage" class="stage">
          <PriceChart mode="story" :step="step" :horizon="20" :prices="prices" :history="history" :latest="latest" />
        </div>
        <ol ref="stepList" class="steps">
          <li v-for="(item, i) in STEPS" :key="i" :data-step="i + 1" :class="{ on: step === i + 1, seen: step >= i + 1 }">
            <div>
              <p class="step-no">{{ i + 1 }} · {{ item.title }}</p>
              <p>{{ item.text }}</p>
              <ul v-if="item.legend" class="keys" aria-label="차트 읽는 법">
                <li><i class="key line" />실제 종가</li>
                <li><i class="key pred" />그때 낸 분포의 가운데</li>
                <li><i class="key band" />그때의 80% 범위</li>
              </ul>
            </div>
          </li>
        </ol>
      </div>
    </div>
  </section>
</template>
