<script setup>
import { computed, ref } from 'vue'
import { NEWS_LABELS, barPath, formatNumber, linearScale, safeUrl, toTime } from '../lib.js'
import { useWidth } from '../useWidth.js'

const props = defineProps({ news: { type: Object, required: true } }) // {articles, daily}
const HEIGHT = 160
const MARGIN = { top: 12, right: 12, bottom: 24, left: 40 }
const box = ref(null)
const width = useWidth(box)

const days = computed(() => props.news.daily)
const x = computed(() => {
  const times = days.value.map(row => toTime(row.day))
  return linearScale([Math.min(...times), Math.max(...times)], [MARGIN.left + 12, width.value - MARGIN.right - 12])
})
const y = linearScale([-1, 1], [HEIGHT - MARGIN.bottom, MARGIN.top])
const barWidth = computed(() => Math.min(24, ((width.value - MARGIN.left - MARGIN.right) / Math.max(days.value.length, 1)) * 0.6))
const bars = computed(() => days.value.map(row => ({
  ...row, d: barPath(x.value(toTime(row.day)), y(0), y(row.score), barWidth.value),
  side: row.score >= 0 ? 'up' : 'down',
})))
const articles = computed(() => props.news.articles.slice(0, 12))
</script>

<template>
  <section aria-labelledby="news-title">
    <p class="eyebrow">뉴스 압력</p>
    <h2 id="news-title">뉴스는 무엇을 말할까 <span class="badge">참고 정보 · 모델 입력 아님</span></h2>
    <p class="note">
      매일 커피 시장 기사를 골라 TypeSafe Jev로 가격 상승·하락 압력을 분류합니다. 과거 기사를 분석한 결과, 점수는 앞으로의 가격보다
      이미 일어난 움직임과 더 관련되어 예측에 넣지 않았습니다(노트북 05). 실시간 점수가 쌓이면 다시 검증합니다.
      막대와 목록은 기사가 나온 날(뉴욕 기준)로 묶었습니다.
    </p>
    <ul class="legend">
      <li><i class="key up" />상승 압력</li>
      <li><i class="key down" />하락 압력</li>
    </ul>
    <div ref="box" class="chart-box">
      <svg v-if="days.length" :width="width" :height="HEIGHT" role="img" aria-label="최근 30일 날짜별 뉴스 압력 점수. -1은 하락, +1은 상승 압력.">
        <g class="grid">
          <line v-for="v in [-1, 0, 1]" :key="v" :class="{ zero: v === 0 }" :x1="MARGIN.left" :x2="width - MARGIN.right" :y1="y(v)" :y2="y(v)" />
        </g>
        <g class="axis-label">
          <text v-for="v in [-1, 0, 1]" :key="`t${v}`" :x="MARGIN.left - 8" :y="y(v) + 4" text-anchor="end">{{ v > 0 ? '+1' : v }}</text>
          <text :x="x(toTime(days[0].day))" :y="HEIGHT - 6" text-anchor="middle">{{ days[0].day.slice(5) }}</text>
          <text :x="x(toTime(days.at(-1).day))" :y="HEIGHT - 6" text-anchor="middle">{{ days.at(-1).day.slice(5) }}</text>
        </g>
        <path v-for="bar in bars" :key="bar.day" :d="bar.d" :class="['bar', bar.side]" tabindex="0">
          <title>{{ bar.day }} · 점수 {{ formatNumber(bar.score, 2) }} · 기사 {{ bar.articles }}건</title>
        </path>
      </svg>
      <p v-else class="muted">최근 30일 기사가 없습니다.</p>
    </div>
    <ol class="articles">
      <li v-for="item in articles" :key="item.url">
        <span class="label" :class="item.label">{{ NEWS_LABELS[item.label] }}</span>
        <a v-if="safeUrl(item.url)" :href="safeUrl(item.url)" target="_blank" rel="noopener noreferrer">{{ item.title }}</a>
        <span v-else>{{ item.title }}</span>
        <small class="muted">{{ item.day }} · 관련성 {{ formatNumber(item.relevance, 2) }}</small>
      </li>
    </ol>
  </section>
</template>
