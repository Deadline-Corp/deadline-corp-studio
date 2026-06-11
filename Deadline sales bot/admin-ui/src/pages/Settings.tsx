import { useEffect, useState } from 'react'
import { api } from '../api/client'

/* Настройки: статус каналов/CRM/LLM (санитизировано, read-only) + состав KB. */

export function Settings() {
  const [s, setS] = useState<any>(null)
  const [kb, setKb] = useState<Array<{ source: string; chunks: number }>>([])

  useEffect(() => {
    void (async () => {
      try {
        setS(await api.get('/settings'))
        const r = await api.get<{ sources: Array<{ source: string; chunks: number }> }>('/kb')
        setKb(r.sources)
      } catch { /* ignore */ }
    })()
  }, [])

  if (!s) return <div className="page"><div className="empty"><span className="spin" /> Загрузка…</div></div>

  const Bool = ({ v }: { v: boolean }) => (
    <span className={`chip ${v ? 'ok' : ''}`}>{v ? 'да' : 'нет'}</span>
  )

  return (
    <div className="page">
      <div className="page-head"><h1>Настройки</h1></div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14 }}>
        <div className="card">
          <b>🧠 LLM</b>
          <table className="tbl" style={{ marginTop: 8 }}>
            <tbody>
              <tr><td className="muted">Провайдер</td><td>{s.llm.provider}</td></tr>
              <tr><td className="muted">Модель</td><td className="mono" style={{ fontSize: 12 }}>{s.llm.model}</td></tr>
              <tr><td className="muted">Fallback</td><td className="mono" style={{ fontSize: 12 }}>{s.llm.fallback_model}</td></tr>
            </tbody>
          </table>
        </div>

        <div className="card">
          <b>📡 Каналы</b>
          <table className="tbl" style={{ marginTop: 8 }}>
            <tbody>
              <tr><td className="muted">Telegram</td><td><Bool v={s.channels.telegram_configured} /></td></tr>
              <tr><td className="muted">Meta (IG / Messenger)</td><td><Bool v={s.channels.meta_configured} /></td></tr>
              <tr><td className="muted">Операторская группа</td><td><Bool v={s.channels.operator_group_configured} /></td></tr>
              <tr><td className="muted">Распознавание голоса</td><td><Bool v={s.channels.voice_transcription} /></td></tr>
            </tbody>
          </table>
        </div>

        <div className="card">
          <b>🗂 CRM</b>
          <table className="tbl" style={{ marginTop: 8 }}>
            <tbody>
              <tr><td className="muted">Включена</td><td><Bool v={s.crm.enabled} /></td></tr>
              <tr><td className="muted">Провайдер</td><td>{s.crm.provider}</td></tr>
              <tr><td className="muted">HubSpot portal</td><td><Bool v={s.crm.hubspot_portal_configured} /></td></tr>
            </tbody>
          </table>
        </div>

        <div className="card">
          <b>🏷 Тенант</b>
          <table className="tbl" style={{ marginTop: 8 }}>
            <tbody>
              <tr><td className="muted">Slug</td><td className="mono">{s.tenant.slug}</td></tr>
              <tr><td className="muted">Название</td><td>{s.tenant.display_name}</td></tr>
              <tr><td className="muted">Языки</td><td>{(s.tenant.languages || []).join(', ')}</td></tr>
            </tbody>
          </table>
        </div>

        <div className="card" style={{ gridColumn: '1 / -1' }}>
          <b>📚 База знаний ({kb.length} документов)</b>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            {kb.map(k => (
              <span key={k.source} className="chip">{k.source} · {k.chunks}</span>
            ))}
          </div>
        </div>
      </div>

      <p className="faint" style={{ fontSize: 12, marginTop: 16 }}>
        Значения читаются из env/конфига на сервере — менять их здесь нельзя (секреты живут в Railway).
        Поведенческие настройки бота (тон, логика) — во вкладке «Мозг».
      </p>
    </div>
  )
}
