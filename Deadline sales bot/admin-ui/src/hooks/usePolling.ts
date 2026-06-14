import { useEffect, useRef } from 'react'

/* Интервальный поллинг с паузой на скрытой вкладке (websockets в MVP нет).
   fn зовётся сразу и затем каждые intervalMs, пока вкладка видима. */
export function usePolling(fn: () => void | Promise<void>, intervalMs: number, deps: any[] = []) {
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    let timer: number | undefined
    let cancelled = false

    const tick = () => { if (!cancelled && !document.hidden) void fnRef.current() }

    tick()
    timer = window.setInterval(tick, intervalMs)

    const onVis = () => { if (!document.hidden) tick() }
    document.addEventListener('visibilitychange', onVis)

    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
      document.removeEventListener('visibilitychange', onVis)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps])
}
