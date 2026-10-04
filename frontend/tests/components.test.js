import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'

// 실제 Vue 파일을 기존 Vite 설정으로 읽는다. 브라우저 폭만 useWidth의 초기값(720px)을 쓴다.
const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
  server: { middlewareMode: true }, appType: 'custom',
})
after(() => server.close())
const { default: PriceChart } = await server.ssrLoadModule('/src/components/PriceChart.vue')
const { default: TrackRecord } = await server.ssrLoadModule('/src/components/TrackRecord.vue')
const forecast = { horizon: 20, origin_date: '2026-10-02', target_date: '2026-10-30', origin_close: 100,
  predicted_price: 105, price_low: 90, price_high: 110, prob_up: .6, kind: 'live', signal: 'buy' }
const props = { horizon: 20, prices: [{ date: '2026-10-02', close: 100 }, { date: '2026-10-05', close: 103 }], latest: [forecast], history: {20:[]} }

test('이력 조회 실패를 정상적인 성적 0건으로 표시하지 않는다', async () => {
  const html = await renderToString(createSSRApp(TrackRecord, { history: {}, failedHorizons: [60] }))
  assert.match(html, /60거래일<\/td><td[^>]*>기록 조회 실패/)
  assert.doesNotMatch(html, /60거래일<\/td><td[^>]*>0<\/td>/)
  assert.doesNotMatch(html, /아직 목표일이 지난 실시간 예측이 없습니다/)
})

test('첫 실시간 가격·범위·확률 관측 하나도 화면에 표식을 남긴다', async () => {
  const row = {...forecast, origin_date:'2026-09-04', target_date:'2026-10-02'}
  const history = [
    {...row, kind:'backfill', origin_date:'2026-09-01', target_date:'2026-09-29'},
    {...row, kind:'backfill', origin_date:'2026-09-02', target_date:'2026-09-30'}, row,
  ]
  const html = await renderToString(createSSRApp(PriceChart, {...props, prices:[{date:'2026-09-01',close:98},...props.prices],history:{20:history}}))
  assert.match(html, /<circle[^>]*class="[^"]*isolated-pred[^"]*live[^"]*"[^>]*r="3"/)
  assert.match(html, /<circle[^>]*class="[^"]*isolated-prob[^"]*live[^"]*"[^>]*r="3"/)
  assert.match(html, /<rect[^>]*class="[^"]*isolated-band[^"]*live[^"]*"[^>]*width="2"/)
})

test('예측이 오래돼도 기준일 이후 실제 종가를 툴팁으로 읽는다', async () => {
  let state
  const Capture = {...PriceChart, setup(p, context) { state = PriceChart.setup(p, context); return state }}
  await renderToString(createSSRApp(Capture, props))
  const date = Date.parse('2026-10-05T00:00:00Z')
  state.onMove({clientX:state.x.value(date),currentTarget:{getBoundingClientRect:()=>({left:0,width:720})}})
  assert.equal(state.hover.value.date, '2026-10-05')
  assert.equal(state.hover.value.close, 103)
  assert.equal(state.hover.value.future, undefined)
})

test('전체 가격이 빈 배열이면 2005년부터의 축을 꾸며 그리지 않는다', async () => {
  const AllPeriod = {...PriceChart, setup(p, context) {
    const state = PriceChart.setup(p, context)
    state.period.value = 'all'
    return state
  }}
  const html = await renderToString(createSSRApp(AllPeriod, {...props, allPrices:[]}))
  assert.ok(html.includes('이 기간에 표시할 가격 자료가 없습니다.'))
  assert.equal(html.includes('<svg'), false)
})

test('Why 섹션: 전체 가격 요청이 실패하면 계속 불러오는 중이라고 하지 않고 다시 시도를 준다', async () => {
  const { default: WhySection } = await server.ssrLoadModule('/src/components/WhySection.vue')
  const failed = await renderToString(createSSRApp(WhySection, {
    allPrices: null, fullError: '전체 가격을 불러오지 못했습니다.', weather: null, weatherFailed: true,
  }))
  assert.doesNotMatch(failed, /전체 가격을 불러오는 중입니다/)
  assert.match(failed, /전체 가격을 불러오지 못했습니다\.\s*<button[^>]*>다시 시도<\/button>/)

  const empty = await renderToString(createSSRApp(WhySection, { allPrices: [], weather: null }))
  assert.doesNotMatch(empty, /전체 가격을 불러오는 중입니다/)   // 빈 응답은 로딩이 아니다
  assert.match(empty, /표시할 가격 자료가 없습니다/)
})
