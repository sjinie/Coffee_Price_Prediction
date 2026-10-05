import assert from 'node:assert/strict'
import * as lib from '../src/lib.js'
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
  assert.equal(formatReturn(Math.log(0.9999)), '0.0%')  // 반올림해서 0이면 부호가 없다
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
test('연 시계와 주간 가격은 1월 1일부터 같은 52주를 쓴다', () => {
  for (const [date, expected] of [
    ['2025-01-01', 0], ['2025-01-07', 0], ['2025-01-08', 1],
    ['2025-12-24', 51], ['2025-12-31', 51], ['2024-12-31', 51],
    ['2024-02-29', 8], ['2026-01-01', 0],
  ]) assert.equal(lib.weekIndex(date), expected, date)
})

test('주간 종가는 마지막 관측을 선택하고 결측과 원본 값을 보존한다', () => {
  const rows = [
    { date: '2026-01-08', close: 120 }, { date: '2025-12-31', close: 100.125 },
    { date: '2026-01-02', close: 105 }, { date: '2026-01-07', close: null },
    { date: '2025-12-24', close: 99 }, { date: '2026-01-09', close: 121 },
  ]
  const before = structuredClone(rows)
  assert.deepEqual(lib.weeklyLast(rows), [
    { date: '2025-12-31', close: 100.125 }, { date: '2026-01-07', close: null },
    { date: '2026-01-09', close: 121 },
  ])
  assert.deepEqual(rows, before)
  assert.deepEqual(lib.weeklyLast([]), [])
})

test('연간 시계: 주간 수익률은 앞 종가가 있을 때만 더하고, 강수는 7일 기준으로 바꾼다', () => {
  const prices = [
    { date: '2024-01-01', close: 100 }, { date: '2024-01-02', close: 110 },
    { date: '2024-01-03', close: null }, { date: '2024-01-09', close: 121 },
  ]
  const grid = lib.weeklyReturnGrid(prices, [2024])
  assert.ok(Math.abs(grid[0][0] - Math.log(1.1)) < 1e-12)   // 1월 1~7일
  assert.ok(Math.abs(grid[0][1] - Math.log(1.1)) < 1e-12)   // 결측을 건너뛴 다음 종가와 비교
  assert.equal(grid[0][2], null)

  const weather = { years: [2024], days: [[7, ...Array(50).fill(7), 9]], precip: [[14, ...Array(50).fill(null), 18]] }
  const rain = lib.weeklyRainGrid(weather, [2023, 2024])
  assert.deepEqual(rain[0], Array(52).fill(null))           // API에 없는 해는 비워 둔다
  assert.equal(rain[1][0], 14)
  assert.equal(rain[1][1], null)
  assert.equal(rain[1][51], 14)                             // 9일 18mm → 7일 14mm
})

test('계절 일관성: 해마다 같은 모양이면 1, 모양이 뒤집히면 낮다', () => {
  const wave = Array.from({ length: 52 }, (_, w) => Math.sin(w / 52 * 2 * Math.PI))
  assert.ok(lib.seasonality([wave, wave, wave]) > 0.999)
  assert.ok(lib.seasonality([wave, wave.map(v => -v), wave, wave.map(v => -v)]) < 0)
  assert.equal(lib.seasonality([Array(52).fill(null)]), null)
})

test('고리 조각은 12시 방향에서 시작한다', () => {
  assert.equal(lib.arcPath(10, 20, 0, Math.PI / 2), 'M0.00,-20.00 A20,20 0 0 1 20.00,-0.00 L10.00,-0.00 A10,10 0 0 0 0.00,-10.00 Z')
})

test('강수: 관측일이 7일보다 적은 주는 늘려 그리지 않는다(기상 API 계약)', () => {
  const weather = { years: [2024], days: [[3, 7, ...Array(50).fill(0)]], precip: [[30, 21, ...Array(50).fill(null)]] }
  const rain = lib.weeklyRainGrid(weather, [2024])
  assert.equal(rain[0][0], null)   // 3일 30mm를 70mm로 부풀리지 않는다
  assert.equal(rain[0][1], 21)
})

test('좁은 화면의 현재 단계는 고정 차트 바로 아래에서 온전히 보이는 첫 문단이다', () => {
  const paragraphs = [
    { step: 2, top: 300, bottom: 380 },   // 일부가 차트(아래 끝 341) 밑에 있다
    { step: 3, top: 450, bottom: 530 },   // 온전히 보인다
    { step: 4, top: 780, bottom: 860 },   // 화면 아래로 잘린다
  ]
  assert.equal(lib.activeStep(paragraphs, 341, 812), 3)
  assert.equal(lib.activeStep([{ step: 5, top: 360, bottom: 440 }, { step: 4, top: 100, bottom: 180 }], 341, 812), 5)
  assert.equal(lib.activeStep([{ step: 2, top: 200, bottom: 400 }], 341, 812), null)   // 온전한 문단이 없으면 단계를 바꾸지 않는다
})
