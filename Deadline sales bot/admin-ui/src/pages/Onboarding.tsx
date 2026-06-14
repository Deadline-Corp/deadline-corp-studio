import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { PresetInfo } from '../api/types'
import { startTour } from '../components/Tour'

/* Онбординг при первом входе: 5 шагов без айтишных слов.
   Название бизнеса → ниша (пресет) → 🪄 авто-настройка из инфо о компании →
   каналы → демо-данные → обучение (spotlight-тур). */

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
  // Шаг авто-настройки (конфиг-агент P4)
  const [dump, setDump] = useState('')
  const [url, setUrl] = useState('')
  const [draft, setDraft] = useState<any>(null)
  const [cfgApplied, setCfgApplied] = useState(false)
  const navigate = useNavigate()

  const STEPS = 5

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

  // Авто-настройка: дамп инфо (+опц. сайт) → AI собирает черновик мозга/KB/ниши.
  const cfgGenerate = async () => {
    if (!dump.trim() && !url.trim()) { setErr('Вставьте текст о компании или ссылку на сайт'); return }
    setBusy(true)
    setErr('')
    try {
      const r = await api.post<{ draft: any }>('/onboarding/generate', { dump, url: url || undefined })
      setDraft(r.draft || {})
    } catch (e: any) { setErr(`Не получилось собрать: ${e.detail ?? e.message}`) }
    finally { setBusy(false) }
  }
  const cfgApplyAndNext = async () => {
    if (!draft) { setStep(3); return }
    setBusy(true)
    setErr('')
    try {
      await api.post('/onboarding/apply', {
        system_prompt: draft.system_prompt, kb_md: draft.kb_md,
        preset_key: draft.preset_key || undefined, bot_goal: draft.bot_goal || undefined,
      })
      setCfgApplied(true)
      setStep(3)
    } catch (e: any) { setErr(`Не получилось применить: ${e.detail ?? e.message}`) }
    finally { setBusy(false) }
  }
  const updDraft = (k: string, v: any) => setDraft({ ...draft, [k]: v })

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
      width: 22, height: 4, borderRadius: 2,
      background: i <= step ? 'var(--accent)' : 'var(--panel-2)',
      transition: 'background 0.3s',
    }} />
  )

  const ta: React.CSSProperties = { width: '100%', minHeight: 96, fontSize: 13 }

  return (
    <div className="login-wrap">
      <div className="card" style={{ width: 640, maxHeight: '90vh', overflowY: 'auto', padding: 28, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 34, height: 34, borderRadius: 9, background: 'linear-gradient(135deg, var(--accent), #4938d8)', display: 'grid', placeItems: 'center', fontWeight: 800, color: '#fff' }}>D</div>
          <b style={{ fontSize: 16 }}>Настройка за 5 минут</b>
          <div style={{ flex: 1 }} />
          <div style={{ display: 'flex', gap: 5 }}>{Array.from({ length: STEPS }, (_, i) => <Dot key={i} i={i} />)}</div>
        </div>

        {step === 0 && (
          <>
            <h2 style={{ margin: 0, fontSize: 18 }}>Как называется ваш бизнес?</h2>
            <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
              Это имя будет в шапке панели — система настраивается под вас.
            </p>
            <input autoFocus placeholder="Например: Студия Deadline / Клининг «Чистый дом»"
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
            <h2 style={{ margin: 0, fontSize: 18 }}>Расскажите боту о вашем бизнесе</h2>
            {applied && <span className="chip ok" style={{ alignSelf: 'flex-start' }}>✅ Ниша применена</span>}
            <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
              «Вывалите» всё, что есть: услуги, цены, как работаете, частые вопросы, регламенты —
              можно копипастом с сайта. И/или ссылку на сайт. AI сам соберёт «мозг» бота
              и базу знаний, проверите и поправите. Это и есть главная настройка — займёт минуту.
            </p>
            <textarea value={dump} onChange={e => setDump(e.target.value)} style={ta}
                      placeholder="Например: убираем квартиры и офисы, генеральная от 4000₽, регулярная от 2500₽, выезд по городу бесплатно, работаем 8:00–20:00, частый вопрос «а свои моющие?» — да, всё своё…" />
            <div style={{ display: 'flex', gap: 8 }}>
              <input value={url} onChange={e => setUrl(e.target.value)} style={{ flex: 1 }}
                     placeholder="Ссылка на сайт (необязательно) — агент скачает" />
              <button className="btn primary" onClick={cfgGenerate} disabled={busy}>
                {busy && !draft ? <span className="spin" /> : (draft ? '↻ Пересобрать' : '🪄 Собрать настройку')}
              </button>
            </div>

            {draft && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {draft.summary && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>🧩 {draft.summary}</p>}
                {draft._parse_failed && <span className="chip warn">агент вернул текст не в формате — поправьте вручную ниже</span>}
                <span className="muted" style={{ fontSize: 12 }}>Тон / мозг бота (как он говорит и что знает о вас):</span>
                <textarea value={draft.system_prompt || ''} onChange={e => updDraft('system_prompt', e.target.value)} style={ta} />
                <span className="muted" style={{ fontSize: 12 }}>База знаний (услуги / цены / частые вопросы):</span>
                <textarea value={draft.kb_md || ''} onChange={e => updDraft('kb_md', e.target.value)} style={{ ...ta, minHeight: 120 }} />
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <button className="btn ghost" onClick={() => setStep(1)}>← Назад</button>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn ghost" onClick={() => setStep(3)}>Пропустить</button>
                <button className="btn primary" onClick={cfgApplyAndNext} disabled={busy}>
                  {busy && draft ? <span className="spin" /> : (draft ? '✅ Применить и дальше →' : 'Дальше →')}
                </button>
              </div>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <h2 style={{ margin: 0, fontSize: 18 }}>Откуда приходят клиенты?</h2>
            {cfgApplied && <span className="chip ok" style={{ alignSelf: 'flex-start' }}>✅ Бот настроен под вашу компанию</span>}
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
              <button className="btn ghost" onClick={() => setStep(2)}>← Назад</button>
              <button className="btn primary" onClick={() => setStep(4)}>Дальше →</button>
            </div>
          </>
        )}

        {step === 4 && (
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
