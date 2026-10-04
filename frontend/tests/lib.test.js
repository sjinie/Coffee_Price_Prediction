import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  bandPath, barPath, extent, formatReturn, linePath, linearScale, modelFor, nearestIndex, riskLevel, safeUrl, ticks,
  trackRecord, trackRecordByKind,
} from '../src/lib.js'

test('지평별 모델 이름은 지평 항목이 묶음보다 우선하고, 그 지평의 모델이 없으면 null이다', () => {
  const metadata = {
    direction: { algorithm: 'LightGBM', horizons: { 5: {}, 20: { algorithm: 'Logistic' } } },
    volatility: { algorithm: 'HAR', horizons: { 20: {} } },
  }
  assert.equal(modelFor(metadata, 'direction', 5), 'LightGBM')
  assert.equal(modelFor(metadata, 'direction', 20), 'Logistic')
  assert.equal(modelFor(metadata, 'volatility', 20), 'HAR')
  assert.equal(modelFor(metadata, 'volatility', 5), null)  // 5일 변동성 모델은 없다
  assert.equal(modelFor(null, 'direction', 5), null)
})

test('선은 결측에서 끊기고 범위 띠는 닫힌 영역이 된다', () => {
  assert.equal(linePath([[0, 1], [1, 2], null, [3, 4]]), 'M0.0,1.0 L1.0,2.0 M3.0,4.0')
  assert.equal(bandPath([[0, 1], [1, 1]], [[0, 3], [1, 3]]), 'M0.0,1.0 L1.0,1.0 L1.0,3.0 L0.0,3.0 Z')
  assert.equal(bandPath([], []), '')
  // 범위가 없는 날(null)에서 끊는다: 없는 범위를 이어 그리지 않는다
  assert.equal(bandPath([[0, 1], null, [2, 1], [3, 1]], [[0, 3], null, [2, 3], [3, 3]]),
    'M0.0,1.0 L0.0,3.0 Z M2.0,1.0 L3.0,1.0 L3.0,3.0 L2.0,3.0 Z')
})

test('척도와 역변환, 범위 여백', () => {
  const x = linearScale([0, 10], [100, 200])
  assert.equal(x(5), 150)
  assert.equal(x.invert(150), 5)
  assert.deepEqual(extent([10, 20, NaN, null]), [9.5, 20.5])
  assert.deepEqual(extent([]), [0, 1])
  assert.equal(nearestIndex([0, 10, 20], 13), 1)
  assert.deepEqual(ticks([209, 450]), [250, 300, 350, 400, 450])
  assert.deepEqual(ticks([-1, 1], 3), [-1, 0, 1])
})

test('막대는 기준선에서 시작한다', () => {
  assert.match(barPath(10, 100, 50, 8), /^M6,100 /)  // 위로 가는 막대
  assert.match(barPath(10, 100, 150, 8), /L14,100 Z$/)  // 아래로 가는 막대
})

test('외부 링크는 http(s)만 허용한다', () => {
  assert.equal(safeUrl('https://news.google.com/a'), 'https://news.google.com/a')
  assert.equal(safeUrl('javascript:alert(1)'), null)
  assert.equal(safeUrl(null), null)
})

test('적중 기록은 목표일 가격이 확인된 예측만 센다', () => {
  const rows = [
    { signal: 'buy', origin_close: 100, actual_close: 110, predicted_price: 104, price_low: 90, price_high: 105, kind: 'backfill' },
    { signal: 'wait', origin_close: 100, actual_close: 105, predicted_price: 98, price_low: 95, price_high: 110, kind: 'live' },
    { signal: 'hold', origin_close: 100, actual_close: 95, predicted_price: null, price_low: null, price_high: null, kind: 'live' },
    { signal: 'buy', origin_close: 100, actual_close: null, predicted_price: 101, price_low: 90, price_high: 110, kind: 'live' },
  ]
  assert.deepEqual(trackRecord(rows), {
    evaluated: 3, pending: 1, signalRate: 2 / 3, signalHit: 0.5, rangeHit: 0.5,
    signalUpRate: 1,  // 신호를 낸 두 날 모두 올랐다: 같은 날 늘 '구매'면 2/2
    returnHit: 0.5, maeModel: 6.5, maeNaive: 7.5,  // 예측 가격 104·98 대 실제 110·105, 현재가 유지는 100
  })
  assert.equal(trackRecord([]).signalHit, null)
  assert.equal(trackRecord([]).maeModel, null)
  assert.equal(formatReturn(Math.log(1.021)), '+2.1%')
  assert.equal(formatReturn(Math.log(0.996)), '−0.4%')
  assert.equal(formatReturn(null), '-')
  assert.equal(riskLevel(0.2), '낮음')
  assert.equal(riskLevel(0.5), '보통')
  assert.equal(riskLevel(0.9), '높음')
})

test('실시간 성적은 소급 계산과 섞지 않는다', () => {
  const hit = { signal: 'buy', origin_close: 100, actual_close: 110, price_low: 90, price_high: 120 }
  const miss = { signal: 'buy', origin_close: 100, actual_close: 90, price_low: 95, price_high: 120 }
  const rows = [...Array(100).fill({ ...hit, kind: 'backfill' }), ...Array(10).fill({ ...miss, kind: 'live' })]
  assert.equal(trackRecord(rows).signalHit, 100 / 110)  // 합치면 90.9%로 보인다
  const byKind = trackRecordByKind(rows)
  assert.equal(byKind.live.signalHit, 0)
  assert.equal(byKind.live.rangeHit, 0)
  assert.equal(byKind.backfill.signalHit, 1)
  assert.equal(byKind.live.evaluated, 10)
})
