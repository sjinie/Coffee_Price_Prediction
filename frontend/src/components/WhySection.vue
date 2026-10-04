<script setup>
import { computed } from 'vue'
import { isNumber, seasonality, weeklyRainGrid, weeklyReturnGrid } from '../lib.js'
import {
  DIRECTION_STATS, FINDINGS, FIXES, MODEL_VERSION, NEWS_DECISION, NEWS_FACTS, RESEARCH, VOLATILITY_NOTE,
} from '../research.js'
import DecisionSpec from './DecisionSpec.vue'
import FeatureUnits from './FeatureUnits.vue'
import LongPrice from './LongPrice.vue'
import NewsLag from './NewsLag.vue'
import ReturnErrors from './ReturnErrors.vue'
import VolatilityDots from './VolatilityDots.vue'
import WalkForward from './WalkForward.vue'
import YearClock from './YearClock.vue'

const props = defineProps({
  allPrices: { type: Array, default: null },
  weather: { type: Object, default: null },
  weatherFailed: Boolean,
  fullError: { type: String, default: '' },
  model: { type: Object, default: null },
  forecast: { type: Object, default: null },
})
const emit = defineEmits(['retry-prices', 'retry-weather'])
const metadata = computed(() => props.model?.metadata ?? null)
// 연간 시계는 끝까지 관측한 해만 쓴다(올해는 아직 52주가 차지 않았다)
const years = computed(() => {
  const last = Number((props.allPrices?.at(-1)?.date ?? props.forecast?.origin_date ?? '2026').slice(0, 4)) - 1
  return Array.from({ length: last - 2005 + 1 }, (_, i) => 2005 + i)
})
const returnGrid = computed(() => (props.allPrices ? weeklyReturnGrid(props.allPrices, years.value) : null))
const rainGrid = computed(() => (props.weather ? weeklyRainGrid(props.weather, years.value) : null))
// 자료마다 불러오는 중, 실패(다시 시도), 빈 응답을 구별한다. 실패를 '불러오는 중'으로 남겨 두지 않는다.
const hasValues = grid => Boolean(grid?.some(row => row.some(isNumber)))
const priceState = computed(() => (props.allPrices?.length ? 'ready' : props.fullError ? 'error' : props.allPrices ? 'empty' : 'loading'))
const weatherState = computed(() => (hasValues(rainGrid.value) ? 'ready' : props.weatherFailed ? 'error' : props.weather ? 'empty' : 'loading'))
const PRICE_TEXT = { loading: '전체 가격을 불러오는 중입니다.', error: '전체 가격을 불러오지 못했습니다.', empty: '표시할 가격 자료가 없습니다.' }
const WEATHER_TEXT = { loading: '산지 기상 자료를 불러오는 중입니다.', error: '산지 기상 자료를 불러오지 못했습니다.', empty: '표시할 기상 자료가 없습니다.' }
const consistency = grid => { const value = grid && seasonality(grid); return value === null || value === undefined ? '-' : value.toFixed(2) }
const stale = computed(() => props.model?.model_version && props.model.model_version !== MODEL_VERSION)
</script>

<template>
  <section class="block why" aria-labelledby="why-title">
    <div class="inner">
      <h2 id="why-title" class="h2">왜 이렇게 만들었을까</h2>
      <p v-if="stale" class="notice" role="status">아래 연구 결과는 모델 {{ MODEL_VERSION }} 기준입니다. 지금 서비스 중인 모델은 {{ model.model_version }}입니다.</p>

      <article class="chapter">
        <p class="side">문제 정의</p>
        <div class="body">
          <h3 class="h3">{{ FINDINGS.problem }}</h3>
          <LongPrice v-if="priceState === 'ready'" :prices="allPrices" />
          <p v-else class="cap" role="status">{{ PRICE_TEXT[priceState] }} <button v-if="priceState === 'error'" class="text-button" type="button" @click="emit('retry-prices')">다시 시도</button></p>
          <p class="prose">그래서 거래일 t의 종가를 기준으로 h거래일 뒤의 로그수익률 ln(P<sub>t+h</sub> / P<sub>t</sub>)을 예측 대상으로 정했다. 가격 수준이 아니라 지금 가격에서 얼마나 움직이는지를 배우게 하려는 것이다.</p>
          <p class="cap">대상은 아라비카 커피 선물(KC=F) 근월물 종가, 센트/파운드. 카페의 실제 원두 납품 가격과는 다르다.</p>
        </div>
      </article>

      <article class="chapter">
        <p class="side">데이터 탐색</p>
        <div class="body">
          <h3 class="h3">{{ FINDINGS.seasons }}</h3>
          <div class="clocks">
            <figure class="clock-figure">
              <YearClock v-if="weatherState === 'ready'" :grid="rainGrid" :years="years" kind="rain" label="브라질 남미나스 산지의 주간 강수 연간 시계" />
              <p v-else class="clock-empty cap" role="status"><span>{{ WEATHER_TEXT[weatherState] }} <button v-if="weatherState === 'error'" class="text-button" type="button" @click="emit('retry-weather')">다시 시도</button></span></p>
              <div class="ramp"><span>적음</span><i class="ramp-rain" /><span>많음</span></div>
              <p v-if="weatherState === 'ready'" class="big">{{ consistency(rainGrid) }}</p>
              <figcaption class="cap">브라질 남미나스 산지의 주간 강수(7일 기준). 숫자는 한 해의 모양이 나머지 해들의 평균과 얼마나 닮았는지(상관의 중앙값)다.</figcaption>
            </figure>
            <figure class="clock-figure">
              <YearClock v-if="priceState === 'ready' && hasValues(returnGrid)" :grid="returnGrid" :years="years" kind="return" label="KC=F 주간 로그수익률 연간 시계" />
              <p v-else class="clock-empty cap" role="status"><span>{{ PRICE_TEXT[priceState === 'ready' ? 'empty' : priceState] }} <button v-if="priceState === 'error'" class="text-button" type="button" @click="emit('retry-prices')">다시 시도</button></span></p>
              <div class="ramp"><span>하락</span><i class="ramp-return" /><span>상승</span></div>
              <p v-if="priceState === 'ready' && hasValues(returnGrid)" class="big">{{ consistency(returnGrid) }}</p>
              <figcaption class="cap">KC=F 주간 로그수익률. 같은 방법으로 잰 해마다의 닮은 정도다.</figcaption>
            </figure>
          </div>
          <p class="cap">고리 하나가 한 해다. 안쪽이 {{ years[0] }}년, 바깥이 {{ years.at(-1) }}년이고 12시 방향에서 1월이 시작한다.</p>
          <p class="prose">기후 피처는 계절값 그대로가 아니라 직전 10년의 같은 달과 비교한 편차로 만들었다. 각 값은 실제로 알려진 날부터만 붙였다. 기상은 관측일 + 4일, 경제지표는 최초 공개일 다음 날부터다.</p>
        </div>
      </article>

      <article class="chapter">
        <p class="side">피처 선택</p>
        <div class="body">
          <h3 class="h3">{{ FINDINGS.features }}</h3>
          <FeatureUnits :groups="RESEARCH.feature_groups" :metadata="metadata" />
          <p class="cap">칸 하나가 피처 하나다. 칠한 칸이 그 모델이 실제로 쓰는 피처이고, 모델 metadata에서 읽는다.</p>
        </div>
      </article>

      <article class="chapter">
        <p class="side">평가</p>
        <div class="body">
          <h3 class="h3">{{ FINDINGS.evaluation }}</h3>
          <ul class="keys"><li><i class="key sq train" />학습</li><li><i class="key sq dev" />개발 평가</li><li><i class="key sq holdout" />보류 평가, 한 번만</li><li><i class="key sq forward" />사후 확인</li></ul>
          <WalkForward :periods="RESEARCH.periods" />
          <div class="split">
            <p class="prose">{{ RESEARCH.periods.train_start.slice(0, 4) }}년부터 학습하고 {{ RESEARCH.periods.development[0] }}년부터 {{ RESEARCH.periods.development[1] }}년까지 한 해씩 예측하며 모델을 골랐다. 고른 설정은 {{ RESEARCH.periods.holdout[0] }}년부터 {{ RESEARCH.periods.holdout[1] }}년에 한 번만 적용했다. {{ RESEARCH.periods.forward_start.slice(0, 4) }}년은 동결한 모델의 사후 확인 구간이다. {{ RESEARCH.periods.holdout[0] }}년 이후는 이전 연구에서 본 적이 있어 미사용 평가셋이라고 부르지 않는다.</p>
            <dl class="facts-list">
              <div><dt>수익률의 기준선</dt><dd>현재가 유지(Naive)</dd></div>
              <div><dt>방향의 기준선</dt><dd>학습 구간의 상승 비율</dd></div>
              <div><dt>변동성의 기준선</dt><dd>직전 변동성</dd></div>
            </dl>
          </div>
        </div>
      </article>

      <article class="chapter">
        <p class="side">결과</p>
        <div class="body">
          <section class="finding">
            <h3 class="h3">{{ FINDINGS.returns }}</h3>
            <ul class="keys"><li><i class="key sq dev" />개발 {{ RESEARCH.periods.development.join('-') }}</li><li><i class="key sq holdout" />보류 {{ RESEARCH.periods.holdout.join('-') }}</li></ul>
            <ReturnErrors :metadata="metadata" />
            <p class="cap">현재가 유지 대비 RMSE. 0보다 오른쪽이면 그대로 두는 것보다 오차가 크다. 점선은 학부 캡스톤 방식의 단순 LSTM이다.</p>
          </section>
          <section class="finding">
            <h3 class="h3">{{ FINDINGS.volatility }}</h3>
            <ul class="keys"><li><i class="key dot dev" />개발</li><li><i class="key dot holdout" />보류</li><li><i class="key dot forward" />2026년</li></ul>
            <VolatilityDots />
            <p class="cap">직전 변동성 대비 RMSE 감소율. {{ VOLATILITY_NOTE }}</p>
          </section>
          <section class="finding">
            <h3 class="h3">{{ FINDINGS.direction }}</h3>
            <div class="stats">
              <div v-for="s in DIRECTION_STATS" :key="s.value"><p class="big">{{ s.value }}</p><p>{{ s.text }}</p></div>
            </div>
          </section>
        </div>
      </article>

      <article class="chapter">
        <p class="side">시행착오</p>
        <div class="body">
          <h3 class="h3">{{ FINDINGS.fixes }}</h3>
          <div class="fixes">
            <div v-for="fix in FIXES" :key="fix.title" class="fix"><h4>{{ fix.title }}</h4><p>{{ fix.text }}</p></div>
          </div>
        </div>
      </article>

      <article class="chapter">
        <p class="side">뉴스 감성</p>
        <div class="body">
          <h3 class="h3">{{ FINDINGS.news }}</h3>
          <div class="split">
            <NewsLag :lag="RESEARCH.news_lag" />
            <dl class="facts-list"><div v-for="f in NEWS_FACTS" :key="f.term"><dt>{{ f.term }}</dt><dd>{{ f.text }}</dd></div></dl>
          </div>
          <p class="cap">거래일 t의 뉴스 점수와 t+k일 수익률의 상관. 옅은 띠는 상관이 0일 때의 ±2 표준오차다.</p>
          <p class="prose">{{ NEWS_DECISION }}</p>
        </div>
      </article>

      <article class="chapter">
        <p class="side">의사결정</p>
        <div class="body">
          <h3 class="h3">{{ FINDINGS.decision }}</h3>
          <DecisionSpec :forecast="forecast" :metadata="metadata" />
        </div>
      </article>
    </div>
  </section>
</template>
