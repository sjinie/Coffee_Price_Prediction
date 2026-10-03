import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  bandPath, barPath, extent, linePath, linearScale, nearestIndex, riskLevel, safeUrl, ticks, trackRecord, trackRecordByKind,
} from '../src/lib.js'

test('선은 결측에서 끊기고 범위 띠는 닫힌 영역이 된다', () => {
  assert.equal(linePath([[0, 1], [1, 2], null, [3, 4]]), 'M0.0,1.0 L1.0,2.0 M3.0,4.0')
  assert.equal(bandPath([[0, 1], [1, 1]], [[0, 3], [1, 3]]), 'M0.0,1.0 L1.0,1.0 L1.0,3.0 L0.0,3.0 Z')
  assert.equal(bandPath([], []), '')
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
    { signal: 'buy', origin_close: 100, actual_close: 110, price_low: 90, price_high: 105, kind: 'backfill' },
    { signal: 'wait', origin_close: 100, actual_close: 105, price_low: 95, price_high: 110, kind: 'live' },
    { signal: 'hold', origin_close: 100, actual_close: 95, price_low: null, price_high: null, kind: 'live' },
    { signal: 'buy', origin_close: 100, actual_close: null, price_low: 90, price_high: 110, kind: 'live' },
  ]
  assert.deepEqual(trackRecord(rows), {
    evaluated: 3, pending: 1, upRate: 2 / 3, signalRate: 2 / 3, signalHit: 0.5, rangeHit: 0.5,
  })
  assert.equal(trackRecord([]).signalHit, null)
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
