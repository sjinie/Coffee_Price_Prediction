import { onBeforeUnmount, onMounted, ref } from 'vue'

// 요소의 실제 폭(px). 차트를 화면 폭에 맞춰 픽셀 좌표로 그리려고 쓴다(글자가 늘어나지 않게).
export function useWidth(element, initial = 720) {
  const width = ref(initial)
  let observer
  onMounted(() => {
    observer = new ResizeObserver(([entry]) => { width.value = Math.max(280, entry.contentRect.width) })
    observer.observe(element.value)
  })
  onBeforeUnmount(() => observer?.disconnect())
  return width
}
