// 화면 계산 함수. 컴포넌트에서 분리해 node --test로 검사한다.

export const HORIZONS = [5, 20, 60]
export const SIGNAL_LABELS = { buy: '지금 구매', wait: '미루기', hold: '보류' }
export const NEWS_LABELS = { bullish: '상승 압력', bearish: '하락 압력', neutral: '중립', uncertain: '불확실' }

export const isNumber = value => typeof value === 'number' && Number.isFinite(value)

export function formatNumber(value, digits = 1) {
  if (!isNumber(value)) return '-'
  return value.toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export const formatPercent = (value, digits = 0) => (isNumber(value) ? `${(value * 100).toFixed(digits)}%` : '-')

export const formatSigned = (value, digits = 1) =>
  (isNumber(value) ? `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(digits)}` : '-')

// 로그수익률을 '+2.1%' 같은 가격 변화율로 바꾼다.
export const formatReturn = (logReturn, digits = 1) =>
  (isNumber(logReturn) ? `${formatSigned(Math.expm1(logReturn) * 100, digits)}%` : '-')

export function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', dateStyle: 'short', timeStyle: 'short' })
}

// 'YYYY-MM-DD'를 UTC 자정의 밀리초로 바꾼다. 날짜끼리만 비교하므로 시간대 영향이 없다.
export const toTime = date => Date.parse(`${date}T00:00:00Z`)

export function linearScale([d0, d1], [r0, r1]) {
  const span = d1 - d0 || 1
  const scale = value => r0 + ((value - d0) / span) * (r1 - r0)
  scale.invert = position => d0 + ((position - r0) / (r1 - r0 || 1)) * span
  return scale
}

export function extent(values, padding = 0.05) {
  const finite = values.filter(isNumber)
  if (!finite.length) return [0, 1]
  const min = Math.min(...finite)
  const max = Math.max(...finite)
  const pad = (max - min) * padding || 1
  return [min - pad, max + pad]
}

// 1·2·5×10^k 간격의 '읽기 쉬운' 눈금. 범위 안에 대략 count개가 들어간다.
export function ticks([min, max], count = 5) {
  const raw = (max - min) / Math.max(count - 1, 1)
  const power = 10 ** Math.floor(Math.log10(raw || 1))
  const gaps = step => Math.abs((max - min) / step - (count - 1))
  const step = [1, 2, 5, 10].map(m => m * power).reduce((best, s) => (gaps(s) < gaps(best) ? s : best))
  const result = []
  for (let value = Math.ceil(min / step) * step; value <= max + 1e-9; value += step) result.push(+value.toFixed(10))
  return result
}

// [x, y] 점을 잇는 SVG 경로. null을 만나면 선을 끊는다(가격이 없는 날).
export function linePath(points) {
  let drawing = false
  return points
    .map(point => {
      if (!point) {
        drawing = false
        return ''
      }
      const command = drawing ? 'L' : 'M'
      drawing = true
      return `${command}${point[0].toFixed(1)},${point[1].toFixed(1)}`
    })
    .filter(Boolean)
    .join(' ')
}

// 위쪽 선을 따라가고 아래쪽 선을 거꾸로 돌아오는 닫힌 영역(예측 범위 띠).
// null(범위가 없는 날)에서 끊어 조각마다 따로 닫는다. 없는 범위를 이어 그리지 않기 위해서다.
export function bandPath(upper, lower) {
  const pieces = []
  let start = 0
  for (let i = 0; i <= upper.length; i += 1) {
    if (i < upper.length && upper[i] && lower[i]) continue
    if (i > start) {
      const top = linePath(upper.slice(start, i))
      const bottom = linePath(lower.slice(start, i).reverse()).replace(/^M/, 'L')
      pieces.push(`${top} ${bottom} Z`)
    }
    start = i + 1
  }
  return pieces.join(' ')
}

// 기준선(base)에서 end까지 가는 막대. 데이터 끝만 둥글게, 기준선 쪽은 각지게 그린다.
export function barPath(x, base, end, width, radius = 4) {
  const r = Math.min(radius, width / 2, Math.abs(end - base))
  const left = x - width / 2
  const right = x + width / 2
  const dir = end < base ? 1 : -1 // SVG는 아래로 갈수록 y가 커진다
  return [
    `M${left},${base}`, `L${left},${end + dir * r}`, `Q${left},${end} ${left + r},${end}`,
    `L${right - r},${end}`, `Q${right},${end} ${right},${end + dir * r}`, `L${right},${base}`, 'Z',
  ].join(' ')
}

export function nearestIndex(values, target) {
  let best = 0
  values.forEach((value, index) => {
    if (Math.abs(value - target) < Math.abs(values[best] - target)) best = index
  })
  return best
}

// 기사 링크는 외부 자료다. http(s)가 아니면(javascript: 등) 링크로 만들지 않는다.
export const safeUrl = url => (/^https?:\/\//i.test(url || '') ? url : null)

// 지평마다 다른 모델을 쓸 수 있다. 지평 항목의 algorithm이 묶음(direction·volatility)의 것보다 우선한다.
// 그 지평의 모델이 없으면(5일 변동성처럼) null.
export function modelFor(metadata, kind, horizon) {
  const bundle = metadata?.[kind]
  const item = bundle?.horizons?.[horizon]
  return item ? item.algorithm ?? bundle.algorithm ?? null : null
}

export function riskLevel(percentile) {
  if (!isNumber(percentile)) return '-'
  return percentile < 1 / 3 ? '낮음' : percentile > 2 / 3 ? '높음' : '보통'
}

// 목표일 종가가 확인된 예측만 채점한다.
export function trackRecord(rows) {
  const done = rows.filter(row => isNumber(row.actual_close))
  const signals = done.filter(row => row.signal !== 'hold')
  const correct = signals.filter(row =>
    row.signal === 'buy' ? row.actual_close > row.origin_close : row.actual_close < row.origin_close)
  const ranged = done.filter(row => isNumber(row.price_low))
  const inside = ranged.filter(row => row.price_low <= row.actual_close && row.actual_close <= row.price_high)
  // 수익률 예측: 예측 가격이 기준 종가보다 실제와 같은 쪽에 있었는지, 그리고 현재가 유지보다 가격 오차가 작았는지
  const priced = done.filter(row => isNumber(row.predicted_price))
  const moved = priced.filter(row => row.actual_close !== row.origin_close)
  const sameSide = moved.filter(row => (row.predicted_price > row.origin_close) === (row.actual_close > row.origin_close))
  const share = (part, whole) => (whole.length ? part.length / whole.length : null)
  const mean = values => (values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null)
  return {
    evaluated: done.length,
    pending: rows.length - done.length,
    signalRate: share(signals, done),
    signalHit: share(correct, signals),
    // 신호 적중률의 기준선: 신호를 낸 그날들에 늘 '구매'라고 했을 때의 적중률(그날들의 상승 비율)
    signalUpRate: share(signals.filter(row => row.actual_close > row.origin_close), signals),
    rangeHit: share(inside, ranged),
    returnHit: share(sameSide, moved),
    maeModel: mean(priced.map(row => Math.abs(row.predicted_price - row.actual_close))),
    maeNaive: mean(priced.map(row => Math.abs(row.origin_close - row.actual_close))),
  }
}

// 동결 이후 매일 저장한 예측(live)과 나중에 소급 계산한 예측(backfill)은 섞지 않고 따로 채점한다.
export function trackRecordByKind(rows) {
  return {
    live: trackRecord(rows.filter(row => row.kind === 'live')),
    backfill: trackRecord(rows.filter(row => row.kind === 'backfill')),
  }
}
