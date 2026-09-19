import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { computed, ref } from 'vue'

// 실제 SFC의 조회·계산 로직을 실행한다. 화면 배치는 브라우저에서 별도로 확인한다.
const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  .split('<script setup>')[1].split('</script>')[0]
  .replace(/import .* from 'vue'\n/, '')

test('조회 → 실패 → 빈 응답 복구와 지평 선택을 보존한다', async () => {
  let mode = 'data'
  const requests = []
  const fetch = async path => {
    requests.push(path)
    if (mode === 'fail') return { ok: false, status: 503 }
    const payload = path.includes('/prices') ? [{ date: '2025-12-31', close: 350 }]
      : path.includes('/predictions') ? [
        { horizon: 5, origin_date: '2025-12-30', target_date: '2026-01-07', actual_price: null, predicted_price: 349 },
        { horizon: 5, origin_date: '2025-12-31', target_date: '2026-01-08', actual_price: null, predicted_price: 350 },
        { horizon: 20, origin_date: '2025-01-01', target_date: '2025-02-01', actual_price: 300, predicted_price: 290 },
      ] : path.includes('/pipeline') ? { status: 'success' } : []
    return { ok: true, json: async () => mode === 'empty' ? (path.includes('/pipeline') ? null : []) : payload }
  }
  const app = new Function('computed', 'onMounted', 'ref', 'fetch', `${source}
    return { loadData, retry, refreshButton, loading, error, prices, selectedHorizon, selectedPredictions, futurePredictions, formatNumber, pricePoints }
  `)(computed, () => {}, ref, fetch)
  const pending = app.loadData()
  assert.equal(app.loading.value, true)
  await pending
  assert.equal(requests.length, 5)
  assert.equal(app.loading.value, false)
  assert.equal(app.futurePredictions.value.length, 1)
  assert.equal(app.futurePredictions.value[0].origin_date, '2025-12-31')
  app.selectedHorizon.value = 20
  assert.equal(app.selectedPredictions.value.length, 1)
  mode = 'fail'
  await app.loadData()
  assert.match(app.error.value, /503/)
  assert.equal(app.loading.value, false)
  mode = 'empty'
  let focused = false
  app.refreshButton.value = { focus: () => { focused = true } }
  await app.retry()
  assert.equal(focused, true)
  assert.equal(app.error.value, '')
  assert.equal(app.prices.value.length, 0)
  assert.equal(app.pricePoints.value, '')
  assert.equal(app.futurePredictions.value.length, 0)
  assert.equal(app.formatNumber(null), '—')
})

test('방향은 예측 기준일 가격과 비교하고 결측·미성숙은 평가에서 제외한다', () => {
  const app = new Function('computed', 'onMounted', 'ref', `${source}
    return { prices, predictions, selectedHorizon, evaluation, evaluationSummary, directionPaths, errorBound, selectedEvaluation }
  `)(computed, () => {}, ref)
  app.prices.value = [{ date: '2025-01-01', close: 100 }, { date: '2025-01-02', close: 200 }]
  app.predictions.value = [
    { origin_date: '2025-01-01', target_date: '2025-04-01', horizon: 60, predicted_price: 110, actual_price: 120 },
    { origin_date: '2025-01-02', target_date: '2025-04-02', horizon: 60, predicted_price: 190, actual_price: 210 },
    { origin_date: 'missing', target_date: '2025-04-03', horizon: 60, predicted_price: 100, actual_price: 100 },
    { origin_date: '2025-01-01', target_date: '2025-04-04', horizon: 60, predicted_price: 100, actual_price: 100 },
    { origin_date: '2025-01-01', target_date: '2025-04-05', horizon: 60, predicted_price: 100, actual_price: 105 },
    { origin_date: '2025-01-01', target_date: '2025-04-06', horizon: 60, predicted_price: 110, actual_price: null },
  ]
  assert.deepEqual(app.evaluation.value.map(item => item.matched), [true, false, null, true, false])
  assert.deepEqual(app.evaluation.value.map(item => item.priceError), [-10, -20, 0, 0, -5])
  assert.deepEqual(app.evaluationSummary.value, { count: 4, matched: 2, missing: 1, accuracy: 50, mae: 7 })
  assert.ok(Math.abs(app.evaluation.value[1].predictedChange + 5) < 1e-10)
  assert.equal((app.directionPaths('actualChange').match(/M/g) || []).length, 2)
  assert.doesNotMatch(app.directionPaths('predictedChange'), /NaN|Infinity/)
  app.selectedHorizon.value = 5
  assert.equal(app.evaluationSummary.value.accuracy, null)
  assert.equal(app.evaluationSummary.value.mae, null)
  assert.equal(app.directionPaths('actualChange'), '')
  assert.equal(app.selectedEvaluation.value, undefined)
  assert.ok(Number.isFinite(app.errorBound.value))
})

test('신호는 확률 결측을 꾸미지 않고 보합·실험 상태·뉴스 값을 표시한다', () => {
  const app = new Function('computed', 'onMounted', 'ref', `${source}
    return { formatPercent, signalDirection, signalStatusLabel, newsImpactLabel, models, predictions, futurePredictions }
  `)(computed, () => {}, ref)
  assert.equal(app.formatPercent(null), '이용 불가')
  assert.equal(app.formatPercent(0), '+0%')
  assert.equal(app.signalDirection({ probability_up: .5, final_direction: 'UP' }), 'UP')
  assert.equal(app.signalDirection({ predicted_return: 0, final_direction: 'DOWN' }), 'DOWN')
  assert.equal(app.signalDirection({ probability_up: .7 }), '이용 불가')
  assert.equal(app.signalDirection({ probability_up: .7, final_direction: 'FLAT' }), 'FLAT')
  assert.equal(app.signalStatusLabel('experimental'), '실험적 확률')
  assert.equal(app.newsImpactLabel(-.1), '약세')
  assert.equal(app.newsImpactLabel(0), '중립')
  app.models.value = [{ model_id: 'current-60', horizons: [60] }]
  app.predictions.value = [
    { model_id: 'old-60', horizon: 60, origin_date: '2025-12-31', target_date: '2026-01-08', actual_price: null },
    { model_id: 'current-60', horizon: 60, origin_date: '2025-12-31', target_date: '2026-01-08', actual_price: null },
    { horizon: 5, origin_date: '2025-12-31', target_date: '2026-01-08', actual_price: null },
  ]
  assert.deepEqual(app.futurePredictions.value.map(item => item.model_id), [undefined, 'current-60'])
})
