import { useState } from 'react'
import { api } from '../api/client'
import { AnalyticsView } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { CHANNEL_META, TEMP_META } from '../lib'
import { HintBar } from '../components/HintBar'

/* Аналитика: цифры воронки/каналов на CSS-барах, без чарт-библиотек. */

const LOST_LABELS: Record<string, string> = {
  price: 'Цена', not_our_format: 'Не наш формат', competitor: 'Конкурент',
  delayed: 'Пропал/отложил', no_budget: 'Нет бюджета', hard_stop: 'Жёсткий отказ',
}

function Bar({ label, value, max, color }: { label: string; value: number; max: number; color?: string }) {
  const w = max > 0 ? Math.max(2, (value / max) * 100) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5 }}>
      <span className="muted" style={{ width: 170, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      <div style={{ flex: 1, height: 18, background: 'var(--bg-soft)', borderRadius: 5, overflow: 'hidden' }}>
        <div style={{ width: `${w}%`, height: '100%', background: color ?? 'var(--accent)', borderRadius: 5, transition: 'width 0.4s ease' }} />
      </div>
      <b style={{ width: 36, textAlign: 'right' }}>{value}</b>
    </div>
  )
}

export function Analytics() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<AnalyticsView | null>(null)

  usePolling(async () => {
    try { setData(await api.get<AnalyticsView>(`/analytics?days=${days}`)) } catch { /* ignore */ }
  }, 60000, [days])

  if (!data) return <div className="page"><div className="empty"><span className="spin" /> Загрузка…</div></div>

  const maxFunnel = Math.max(1, ...data.funnel.map(f => f.count))
  const maxDay = Math.max(1, ...data.leads_by_day.map(d => d.count))
  const chMax = Math.max(1, ...Object.values(data.leads_by_channel))
  const lostMax = Math.max(1, ...Object.values(data.lost_reasons))
  const tempMax = Math.max(1, ...Object.values(data.temperatures))

  const kpi: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'center', padding: '14px 10px' }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Аналитика</h1>
        <div className="spacer" />
        <select value={days} onChange={e => setDays(parseInt(e.target.value, 10))}>
          <option value={7}>7 дней</option>
          <option value={30}>30 дней</option>
          <option value={90}>90 дней</option>
        </select>
      </div>

      <HintBar id="analytics" icon="📈">
        Цифры ваших продаж: сколько лидов пришло и откуда, где они застревают в воронке,
        почему теряются. Заглядывайте раз в день — сразу видно, где затык.
      </HintBar>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <div className="card" style={kpi}>
          <span style={{ fontSize: 26, fontWeight: 700 }}>{data.totals.new_leads}</span>
          <span className="muted" style={{ fontSize: 12 }}>новых лидов</span>
        </div>
        <div className="card" style={kpi}>
          <span style={{ fontSize: 26, fontWeight: 700 }}>{data.totals.handoffs}</span>
          <span className="muted" style={{ fontSize: 12 }}>передано команде</span>
        </div>
        <div className="card" style={kpi}>
          <span style={{ fontSize: 26, fontWeight: 700 }}>{data.totals.booked_calls}</span>
          <span className="muted" style={{ fontSize: 12 }}>созвонов назначено</span>
        </div>
        <div className="card" style={kpi}>
          <span style={{ fontSize: 26, fontWeight: 700 }}>{data.totals.automation_fires}</span>
          <span className="muted" style={{ fontSize: 12 }}>автоматизаций сработало</span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: 14 }}>
        <div className="card">
          <b>📊 Воронка сейчас</b>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 12 }}>
            {data.funnel.map(f => (
              <Bar key={f.stage} label={f.label} value={f.count} max={maxFunnel}
                   color={f.stage === 'lost' ? 'var(--danger)' : undefined} />
            ))}
          </div>
        </div>

        <div className="card">
          <b>📈 Новые лиды по дням</b>
          {data.leads_by_day.length === 0
            ? <div className="empty" style={{ padding: '20px 0' }}>За период лидов не было</div>
            : (
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 120, marginTop: 14 }}>
                {data.leads_by_day.map(d => (
                  <div key={d.day} title={`${d.day}: ${d.count}`}
                       style={{
                         flex: 1, minWidth: 4,
                         height: `${Math.max(4, (d.count / maxDay) * 100)}%`,
                         background: 'var(--accent)', borderRadius: '3px 3px 0 0', opacity: 0.85,
                       }} />
                ))}
              </div>
            )}
        </div>

        <div className="card">
          <b>📡 Лиды по каналам</b>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 12 }}>
            {Object.entries(data.leads_by_channel).length === 0 && <div className="empty" style={{ padding: '14px 0' }}>—</div>}
            {Object.entries(data.leads_by_channel).map(([ch, n]) => (
              <Bar key={ch} label={`${CHANNEL_META[ch]?.icon ?? ''} ${CHANNEL_META[ch]?.label ?? ch}`} value={n} max={chMax} color="var(--info)" />
            ))}
          </div>
        </div>

        <div className="card">
          <b>🌡 Температура базы</b>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 12 }}>
            {Object.entries(data.temperatures).map(([t, n]) => (
              <Bar key={t} label={TEMP_META[t]?.label ?? t} value={n} max={tempMax} color="var(--warn)" />
            ))}
          </div>
        </div>

        <div className="card">
          <b>❌ Причины проигрыша</b>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 12 }}>
            {Object.entries(data.lost_reasons).length === 0 && <div className="empty" style={{ padding: '14px 0' }}>Проигранных нет 🎉</div>}
            {Object.entries(data.lost_reasons).map(([r, n]) => (
              <Bar key={r} label={LOST_LABELS[r] ?? r} value={n} max={lostMax} color="var(--danger)" />
            ))}
          </div>
        </div>

        <div className="card">
          <b>💬 Сообщения за период</b>
          <table className="tbl" style={{ marginTop: 8 }}>
            <tbody>
              <tr><td className="muted">От лидов</td><td><b>{data.messages_by_role.user ?? 0}</b></td></tr>
              <tr><td className="muted">От бота</td><td><b>{data.messages_by_role.assistant ?? 0}</b></td></tr>
              <tr><td className="muted">От операторов</td><td><b>{data.messages_by_role.operator ?? 0}</b></td></tr>
            </tbody>
          </table>
          <p className="faint" style={{ fontSize: 11.5, marginBottom: 0 }}>
            Конверсия между стадиями появится по мере накопления истории переходов
            (она начала записываться с этого обновления).
          </p>
        </div>
      </div>
    </div>
  )
}
