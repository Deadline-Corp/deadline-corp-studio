import { useEffect, useState } from 'react'
import { api } from '../api/client'

/* Настройки: редактируемое поведение бота (прогрев/нудж — применяется без
   деплоя за ~минуту) + статус каналов/CRM/LLM (read-only) + состав KB. */

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

      <BehaviorCard />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14, marginTop: 14 }}>
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
          <b>🗂 CRM (зеркало)</b>
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
        Серые карточки читаются из env/конфига на сервере — секреты живут в Railway.
        Тон и правила бота — во вкладке «Мозг»; стадии воронки — в «Воронке» (⚙ Настроить стадии).
      </p>
    </div>
  )
}

/* ---------- Поведение бота (редактируемое) ---------- */

function BehaviorCard() {
  const [overrides, setOverrides] = useState<any>(null)
  const [defaults, setDefaults] = useState<any>({})
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)
  const [dirty, setDirty] = useState(false)

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 4000)
  }

  useEffect(() => {
    void api.get<any>('/behavior').then(r => {
      setDefaults(r.defaults)
      setOverrides({ ...r.defaults, ...r.overrides })
    }).catch(() => { /* ignore */ })
  }, [])

  if (!overrides) return null

  const upd = (k: string, v: any) => { setOverrides({ ...overrides, [k]: v }); setDirty(true) }

  const save = async () => {
    setBusy(true)
    try {
      const values: any = {}
      for (const k of Object.keys(defaults)) {
        values[k] = overrides[k] === defaults[k] || overrides[k] === '' ? null : overrides[k]
      }
      // числовые поля приводим
      for (const k of ['nudge_after_hours', 'nudge_max_hours']) {
        if (values[k] != null) values[k] = parseFloat(values[k])
      }
      if (values.silence_lost_days != null) values.silence_lost_days = parseInt(values.silence_lost_days, 10)
      await api.post('/behavior', { values })
      setDirty(false)
      showToast('✅ Сохранено — бот подхватит в течение минуты (следующий крон-проход)')
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const row: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }

  return (
    <div className="card">
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <b>⚡ Поведение бота — прогрев молчунов</b>
        {dirty && <span className="chip warn" style={{ marginLeft: 10 }}>не сохранено</span>}
        <div style={{ flex: 1 }} />
        <button className="btn sm primary" onClick={save} disabled={busy || !dirty}>
          {busy ? <span className="spin" /> : '💾 Сохранить'}
        </button>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 12 }}>
        <label style={row}>
          <input type="checkbox" checked={!!overrides.nudge_enabled}
                 onChange={e => upd('nudge_enabled', e.target.checked)} />
          Пинговать вовлечённого лида, если он замолчал (один раз за диалог)
        </label>
        <div style={row}>
          <span className="muted" style={{ width: 280 }}>Пинговать через (часов тишины):</span>
          <input type="number" min={0.5} step={0.5} value={overrides.nudge_after_hours}
                 onChange={e => upd('nudge_after_hours', e.target.value)} style={{ width: 90 }} />
        </div>
        <div style={row}>
          <span className="muted" style={{ width: 280 }}>Уже не пинговать после (часов):</span>
          <input type="number" min={1} step={1} value={overrides.nudge_max_hours}
                 onChange={e => upd('nudge_max_hours', e.target.value)} style={{ width: 90 }} />
        </div>
        <div style={{ ...row, alignItems: 'flex-start' }}>
          <span className="muted" style={{ width: 280, paddingTop: 6 }}>Текст пинка (пусто = стандартный):</span>
          <textarea value={overrides.nudge_text ?? ''} placeholder="Здравствуйте! Вы недавно интересовались — актуально ещё?.."
                    onChange={e => upd('nudge_text', e.target.value || null)}
                    style={{ flex: 1, minHeight: 50 }} />
        </div>
        <div style={row}>
          <span className="muted" style={{ width: 280 }}>Считать лида потерянным после (дней тишины):</span>
          <input type="number" min={1} step={1} value={overrides.silence_lost_days}
                 onChange={e => upd('silence_lost_days', e.target.value)} style={{ width: 90 }} />
        </div>
      </div>
      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
    </div>
  )
}
