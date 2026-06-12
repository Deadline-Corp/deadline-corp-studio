import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { PresetInfo } from '../api/types'
import { startTour } from '../components/Tour'

/* Онбординг при первом входе: 4 шага без айтишных слов.
   Название бизнеса → ниша (пресет) → каналы → демо-данные → обучение. */

export function Onboarding() {
  const [step, setStep] = useState(0)
  const [name, setName] = useState('')
  const [presets, setPresets] = useState<PresetInfo[]>([])
  const [niche, setNiche] = useState<string | null>(null)
  const [applied, setApplied] = useState(false)
  const [channels, setChannels] = useState<any>(null)
  const [demoDone, setDemoDone] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    void api.get<{ items: PresetInfo[] }>('/presets').then(r => setPresets(r.items)).catch(() => { /* */ })
    void api.get<any>('/settings').then(s => setChannels(s.channels)).catch(() => { /* */ })
    void api.get<any>('/workspace').then(w => {
      if (w.business_name) setName(w.business_name)
      if (w.niche_key) setNiche(w.niche_key)
      if (w.demo_leads > 0) setDemoDone(true)
    }).catch(() => { /* */ })
  }, [])

  const saveName = async () => {
    setBusy(true)
    setErr('')
    try {
      await api.post('/workspace', { business_name: name.trim() })
      setStep(1)
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const applyNiche = async () => {
    if (!niche) { setStep(2); return }
    setBusy(true)
    setErr('')
    try {
      await api.post('/presets/apply', { key: niche })
      await api.post('/workspace', { niche_key: niche })
      setApplied(true)
      setStep(2)
    } catch (e: any) { setErr(typeof e.detail === 'string' ? e.detail : e.message) }
    finally { setBusy(false) }
  }

  const seedDemo = async () => {
    setBusy(true)
    setErr('')
    try {
      await api.post('/demo/seed')
      setDemoDone(true)
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const finish = async (withTour: boolean) => {
    setBusy(true)
    try {
      await api.post('/workspace', { onboarding_done: true })
      navigate('/')
      if (withTour) setTimeout(startTour, 400)
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const Dot = ({ i }: { i: number }) => (
    <div style={{
      width: 26, height: 4, borderRadius: 2,
      background: i <= step ? 'var(--accent)' : 'var(--panel-2)',
      transition: 'background 0.3s',
    }} />
  )

  return (
    <div className="login-wrap">
      <div className="card" style={{ width: 620, maxHeight: '90vh', overflowY: 'auto', padding: 28, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 34, height: 34, borderRadius: 9, background: 'linear-gradient(135deg, var(--accent), #4938d8)', display: 'grid', placeItems: 'center', fontWeight: 800, color: '#fff' }}>D</div>
          <b style={{ fontSize: 16 }}>Настройка за 3 минуты</b>
          <div style={{ flex: 1 }} />
          <div style={{ display: 'flex', gap: 5 }}>{[0, 1, 2, 3].map(i => <Dot key={i} i={i} />)}</div>
        </div>

        {step === 0 && (
          <>
            <h2 style={{ margin: 0, fontSize: 18 }}>Как называется ваш бизнес?</h2>
            <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
              Это имя будет в шапке панели — система настраивается под вас.
            </p>
            <input autoFocus placeholder="Например: Студия Deadline / Стоматология «Улыбка»"
                   value={name} onChange={e => setName(e.target.value)}
                   onKeyDown={e => { if (e.key === 'Enter' && name.trim()) saveName() }} />
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn primary" onClick={saveName} disabled={busy || !name.trim()}>
                {busy ? <span className="spin" /> : 'Дальше →'}
              </button>
            </div>
          </>
        )}

        {step === 1 && (
          <>
            <h2 style={{ margin: 0, fontSize: 18 }}>Чем занимаетесь?</h2>
            <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
              Выберите нишу — воронка, поля лида и автоматизации настроятся под неё сами.
              Потом всё можно поменять.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {presets.map(p => (
                <div key={p.key}
                     onClick={() => setNiche(p.key)}
                     style={{
                       border: `1px solid ${niche === p.key ? 'var(--accent)' : 'var(--border)'}`,
                       background: niche === p.key ? 'var(--accent-soft)' : 'transparent',
                       borderRadius: 10, padding: '12px 14px', cursor: 'pointer',
                     }}>
                  <b style={{ fontSize: 13.5 }}>{p.emoji} {p.title}</b>
                  <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>{p.desc}</div>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <button className="btn ghost" onClick={() => setStep(2)}>Пропустить</button>
              <button className="btn primary" onClick={applyNiche} disabled={busy || !niche}>
                {busy ? <span className="spin" /> : 'Применить и дальше →'}
              </button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <h2 style={{ margin: 0, fontSize: 18 }}>Откуда приходят клиенты?</h2>
            {applied && <span className="chip ok" style={{ alignSelf: 'flex-start' }}>✅ Ниша применена</span>}
            <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
              Сейчас подключено вот что. Новые каналы (Telegram, Instagram, WhatsApp)
              подключаются во вкладке «Каналы» — там пошаговые инструкции для каждого.
            </p>
            {channels && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[
                  ['🌐 Сайт-виджет', true],
                  ['✈️ Telegram', channels.telegram_configured],
                  ['📸 Instagram + Messenger', channels.meta_configured],
                  ['🟢 WhatsApp', false],
                ].map(([label, ok], i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13.5 }}>
                    <span style={{ flex: 1 }}>{label as string}</span>
                    <span className={`chip ${ok ? 'ok' : ''}`}>{ok ? 'подключён' : 'подключим позже'}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <button className="btn ghost" onClick={() => setStep(1)}>← Назад</button>
              <button className="btn primary" onClick={() => setStep(3)}>Дальше →</button>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <h2 style={{ margin: 0, fontSize: 18 }}>Потренируйтесь на демо-данных</h2>
            <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
              Добавим 7 учебных лидов с перепиской, задачами и сделками на разных этапах —
              сможете безопасно потыкать всё: воронку, ответы, автоматизации. Они помечены
              как демо и удаляются одной кнопкой в Настройках, реальных клиентов не затронут.
            </p>
            {demoDone
              ? <span className="chip ok" style={{ alignSelf: 'flex-start' }}>✅ Демо-лиды добавлены</span>
              : (
                <button className="btn" style={{ alignSelf: 'flex-start' }} onClick={seedDemo} disabled={busy}>
                  {busy ? <span className="spin" /> : '🧪 Добавить демо-данные'}
                </button>
              )}
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <button className="btn ghost" onClick={() => finish(false)} disabled={busy}>Я разберусь сам</button>
              <button className="btn primary" onClick={() => finish(true)} disabled={busy}>
                🎓 Запустить с обучением
              </button>
            </div>
          </>
        )}

        {err && <div style={{ color: 'var(--danger)', fontSize: 13 }}>{err}</div>}
      </div>
    </div>
  )
}
