import { onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import * as echarts from 'echarts'
import { registerTheme } from '@/utils/echartsTheme'

/**
 * 统一管理 ECharts 实例：容器出现时初始化、尺寸变化时 resize、卸载时销毁。
 * 容器常在 v-if 之后才渲染，所以依赖变化和容器出现都在 DOM 更新后（flush: 'post'）重绘。
 * build 返回 null 时清空图表。
 */
export function useChart(build: () => echarts.EChartsCoreOption | null, deps: () => unknown) {
  const el: Ref<HTMLDivElement | undefined> = ref()
  const chart = shallowRef<echarts.ECharts | null>(null)
  const observer = new ResizeObserver(() => chart.value?.resize())

  function render() {
    if (!el.value) return
    if (!chart.value || chart.value.getDom() !== el.value) {
      chart.value?.dispose()
      registerTheme()
      chart.value = echarts.init(el.value, 'chanLight')
      observer.observe(el.value)
    }
    const option = build()
    if (option) chart.value.setOption(option, true)
    else chart.value.clear()
  }

  watch(el, (node, old) => {
    if (old) observer.unobserve(old)
    if (node) render()
    else {
      chart.value?.dispose()
      chart.value = null
    }
  }, { flush: 'post' })

  watch(deps, render, { deep: true, flush: 'post' })

  onBeforeUnmount(() => {
    observer.disconnect()
    chart.value?.dispose()
    chart.value = null
  })

  return { el, chart, render }
}
