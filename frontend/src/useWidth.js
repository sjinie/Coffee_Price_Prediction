import { onBeforeUnmount, onMounted, ref } from 'vue'

// 요소의 실제 크기(px). 차트를 화면에 맞춰 픽셀 좌표로 그리려고 쓴다(글자가 늘어나지 않게).
// 높이는 첫 화면처럼 차트가 남은 공간을 채울 때만 쓴다.
export function useSize(element, initial = { width: 720, height: 480 }) {
  const width = ref(initial.width)
  const height = ref(initial.height)
  let observer
  onMounted(() => {
    observer = new ResizeObserver(([entry]) => {
      width.value = Math.max(280, entry.contentRect.width)
      height.value = entry.contentRect.height
    })
    observer.observe(element.value)
  })
  onBeforeUnmount(() => observer?.disconnect())
  return { width, height }
}

export const useWidth = (element, initial = 720) => useSize(element, { width: initial, height: 0 }).width
