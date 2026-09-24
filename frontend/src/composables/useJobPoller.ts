import { onBeforeUnmount, ref } from 'vue'
import { api } from '@/api'

/** 触发后台任务后轮询 job_log，直到该任务不再运行。 */
export function useJobPoller(onFinish?: (job: any) => void) {
  const running = ref<Record<string, any>>({})
  const since: Record<string, number> = {}
  let timer: number | undefined

  async function tick() {
    const jobs: any[] = await api.jobs(30)
    const latest: Record<string, any> = {}
    for (const j of jobs) if (!latest[j.job_name]) latest[j.job_name] = j
    const next: Record<string, any> = {}
    for (const name of Object.keys(running.value)) {
      const j = latest[name]
      const startedAt = j?.started_at ? new Date(j.started_at).getTime() : 0
      // 刚触发时最新记录可能还是上一次的，等新记录出现（最多 15 秒）
      if (since[name] && startedAt < since[name] - 5000) {
        if (Date.now() - since[name] < 15000) next[name] = running.value[name]
        continue
      }
      if (j && j.status === 'running') next[name] = j
      else if (j) {
        delete since[name]
        onFinish?.(j)
      }
    }
    for (const [name, j] of Object.entries(latest)) if (j.status === 'running') next[name] = j
    running.value = next
    if (!Object.keys(next).length) stop()
  }

  function start(name?: string) {
    if (name) {
      since[name] = Date.now()
      running.value = { ...running.value, [name]: { job_name: name, status: 'running', progress: 0, total: 0 } }
    }
    if (timer) return
    timer = window.setInterval(() => tick().catch(() => undefined), 2000)
    tick().catch(() => undefined)
  }

  function stop() {
    if (timer) window.clearInterval(timer)
    timer = undefined
  }

  onBeforeUnmount(stop)
  return { running, start, stop, tick }
}
