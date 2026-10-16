import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const width = ref(typeof window !== 'undefined' ? window.innerWidth : 1440)
let listeners = 0
const onResize = () => (width.value = window.innerWidth)

export function useBreakpoint() {
  onMounted(() => {
    if (listeners++ === 0) window.addEventListener('resize', onResize)
  })
  onBeforeUnmount(() => {
    if (--listeners === 0) window.removeEventListener('resize', onResize)
  })
  const isMobile = computed(() => width.value < 768)
  const isTablet = computed(() => width.value >= 768 && width.value < 1280)
  const isDesktop = computed(() => width.value >= 1280)
  return { width, isMobile, isTablet, isDesktop }
}
