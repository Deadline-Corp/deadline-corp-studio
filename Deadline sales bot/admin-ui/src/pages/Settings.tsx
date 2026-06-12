import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { HintBar, hintsEnabled, setHintsEnabled } from '../components/HintBar'
import { Help } from '../components/Help'

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

      <HintBar id="settings" icon="⚙️">
        Всё «под себя»: пресет ниши (перестроит систему в 1 клик), поля лида, поведение бота
        (когда напоминать молчунам), демо-данные для тренировки. Подсказки и обучение
        включаются/выключаются здесь же.
      </HintBar>
      <WorkspaceCard />
      <div style={{ height: 14 }} />
      <PresetsCard />
      <div style={{ height: 14 }} />
      <BehaviorCard />
      <div style={{ height: 14 }} />
      <FieldsCard />

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

/* ---------- Рабочее пространство: имя, обучение, демо-песочница ---------- */

function WorkspaceCard() {
  const [name, setName] = useState('')
  const [demoLeads, setDemoLeads] = useState(0)
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 4500)
  }

  const load = () => api.get<any>('/workspace').then(w => {
    setName(w.business_name || '')
    setDemoLeads(w.demo_leads || 0)
  }).catch(() => { /* */ })

  useEffect(() => { void load() }, [])

  const saveName = async () => {
    setBusy(true)
    try {
      await api.post('/workspace', { business_name: name })
      setDirty(false)
      showToast('✅ Сохранено — имя обновится в шапке после перезагрузки страницы')
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const seed = async () => {
    setBusy(true)
    try {
      const r = await api.post<any>('/demo/seed')
      showToast(`🧪 Добавлено демо: ${r.created.customers} лидов, ${r.created.messages} сообщений, ${r.created.tasks} задачи`)
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const clear = async () => {
    if (!confirm(`Удалить ${demoLeads} демо-лидов? Реальные клиенты не затронутся.`)) return
    setBusy(true)
    try {
      await api.post('/demo/clear')
      showToast('🧹 Демо-данные удалены')
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  return (
    <div className="card">
      <b>🏢 Рабочее пространство</b>
      <div style={{ display: 'flex', gap: 18, marginTop: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 280 }}>
          <span className="muted" style={{ fontSize: 12.5 }}>Название бизнеса (в шапке панели):</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <input value={name} onChange={e => { setName(e.target.value); setDirty(true) }} style={{ flex: 1 }} />
            <button className="btn sm primary" onClick={saveName} disabled={busy || !dirty}>💾</button>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn sm" onClick={() => { import('../components/Tour').then(m => m.startTour()) }}>
              🎓 Показать обучение
            </button>
            <button className="btn sm ghost" onClick={() => { location.hash = '#/onboarding' }}>
              ↻ Мастер настройки заново
            </button>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, cursor: 'pointer' }}>
            <input type="checkbox" defaultChecked={hintsEnabled()}
                   onChange={e => setHintsEnabled(e.target.checked)} />
            💡 Подсказки на страницах (что это за окно и как с ним работать)
          </label>
        </div>
        <div style={{ flex: 1, minWidth: 280, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="muted" style={{ fontSize: 12.5 }}>
            🧪 Демо-песочница: учебные лиды с перепиской и задачами — тренируйтесь без риска.
            {demoLeads > 0 && <b style={{ color: 'var(--text)' }}> Сейчас в системе: {demoLeads} демо-лидов.</b>}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn sm" onClick={seed} disabled={busy}>
              {busy ? <span className="spin" /> : (demoLeads > 0 ? '↻ Пересоздать демо' : '+ Добавить демо-данные')}
            </button>
            {demoLeads > 0 && (
              <button className="btn sm danger" onClick={clear} disabled={busy}>🧹 Удалить демо</button>
            )}
          </div>
        </div>
      </div>
      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
    </div>
  )
}

/* ---------- Пресеты ниш (паттерн GHL Snapshots) ---------- */

function PresetsCard() {
  const [items, setItems] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [confirmKey, setConfirmKey] = useState<string | null>(null)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 5000)
  }

  useEffect(() => {
    void api.get<{ items: any[] }>('/presets').then(r => setItems(r.items)).catch(() => { /* ignore */ })
  }, [])

  const apply = async (key: string) => {
    setBusy(true)
    try {
      const r = await api.post<{ applied: any; preset: string }>('/presets/apply', { key })
      showToast(`✅ «${r.preset}»: стадии ${r.applied.stages}, поля ${r.applied.fields}, правила ${r.applied.automations}. Обнови вкладки Воронка/Автоматизации.`)
      setConfirmKey(null)
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  return (
    <div className="card">
      <b>📦 Пресеты ниш — настроить систему под бизнес в 1 клик
        <Help title="Пресет" text="Готовый набор под нишу: стадии воронки + поля лида + правила автоматизаций + текст напоминаний. Применяется мгновенно, данные лидов не трогает. Потом всё можно подправить руками." />
      </b>
      <p className="muted" style={{ margin: '4px 0 10px', fontSize: 12.5 }}>
        Пресет заменит стадии воронки, поля лида и пресет-правила автоматизаций (📦) под выбранную нишу.
        Ваши ручные правила и данные лидов не трогаются. Тон бота настраивается отдельно во вкладке «Мозг».
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: 10 }}>
        {items.map(p => (
          <div key={p.key} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <b style={{ fontSize: 13.5 }}>{p.emoji} {p.title}</b>
            <span className="muted" style={{ fontSize: 12, flex: 1 }}>{p.desc}</span>
            <span className="faint" style={{ fontSize: 11 }}>
              {p.stages_count} стадий · {p.fields_count} полей · {p.automations_count} правил
            </span>
            {confirmKey === p.key ? (
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn sm primary" onClick={() => apply(p.key)} disabled={busy}>
                  {busy ? <span className="spin" /> : 'Точно применить'}
                </button>
                <button className="btn sm" onClick={() => setConfirmKey(null)}>Отмена</button>
              </div>
            ) : (
              <button className="btn sm" onClick={() => setConfirmKey(p.key)}>Применить</button>
            )}
          </div>
        ))}
      </div>
      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
    </div>
  )
}

/* ---------- Поля лида (редактор определений) ---------- */

function FieldsCard() {
  const [items, setItems] = useState<any[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 4000)
  }

  const load = () => api.get<{ items: any[] }>('/custom-fields').then(r => setItems(r.items)).catch(() => { /* ignore */ })
  useEffect(() => { void load() }, [])

  if (!items) return null

  const upd = (i: number, patch: any) => {
    setItems(items.map((it, k) => (k === i ? { ...it, ...patch } : it)))
    setDirty(true)
  }
  const remove = (i: number) => { setItems(items.filter((_, k) => k !== i)); setDirty(true) }
  const add = () => { setItems([...items, { key: '', label: 'Новое поле', field_type: 'text', options: null, active: true }]); setDirty(true) }

  const save = async () => {
    setBusy(true)
    try {
      await api.post('/custom-fields', {
        items: items.map(it => ({
          key: it.key || undefined, label: it.label, field_type: it.field_type,
          options: it.field_type === 'select'
            ? (typeof it.options === 'string' ? it.options.split(',').map((s: string) => s.trim()).filter(Boolean) : it.options)
            : null,
          active: it.active,
        })),
      })
      setDirty(false)
      showToast('✅ Поля сохранены — появятся в карточках лидов')
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  return (
    <div className="card">
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <b>📇 Поля лида (под вашу нишу)</b>
        {dirty && <span className="chip warn" style={{ marginLeft: 10 }}>не сохранено</span>}
        <div style={{ flex: 1 }} />
        <button className="btn sm" onClick={add}>+ Поле</button>
        <button className="btn sm primary" style={{ marginLeft: 8 }} onClick={save} disabled={busy || !dirty}>
          {busy ? <span className="spin" /> : '💾 Сохранить'}
        </button>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
        {items.length === 0 && <span className="faint" style={{ fontSize: 12.5 }}>Полей нет — добавьте («Бюджет», «Тип проекта»…) или примените пресет ниши выше</span>}
        {items.map((it, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input value={it.label} onChange={e => upd(i, { label: e.target.value })} style={{ width: 220 }} />
            <select value={it.field_type} onChange={e => upd(i, { field_type: e.target.value })} style={{ fontSize: 12.5 }}>
              <option value="text">текст</option>
              <option value="number">число</option>
              <option value="select">список</option>
            </select>
            {it.field_type === 'select' && (
              <input placeholder="варианты через запятую"
                     value={Array.isArray(it.options) ? it.options.join(', ') : (it.options ?? '')}
                     onChange={e => upd(i, { options: e.target.value })} style={{ flex: 1 }} />
            )}
            <button className="btn sm ghost" style={{ marginLeft: 'auto' }} onClick={() => remove(i)}>✕</button>
          </div>
        ))}
      </div>
      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
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
      for (const k of ['digest_hour', 'digest_tz_offset']) {
        if (values[k] != null) values[k] = parseInt(values[k], 10)
      }
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
        <div style={{ ...row, paddingBottom: 10, borderBottom: '1px solid var(--border)' }}>
          <span className="muted" style={{ width: 280 }}>🎯 Цель бота (к чему ведёт диалог):
            <Help title="Цель бота" text="Главная настройка поведения. «Созвон» — текущий стиль: квалифицирует и зовёт на звонок. «Сбор заявок» — без созвонов, берёт контакты и бриф. «Консультация» — помогает, не давит. «Продажа» — называет цены и ведёт к предоплате. Применяется к новым ответам сразу." />
          </span>
          <select value={overrides.bot_goal ?? 'call'} onChange={e => upd('bot_goal', e.target.value)}>
            <option value="call">📞 Вести на созвон (по умолчанию)</option>
            <option value="collect_lead">📥 Собирать заявки (контакт + бриф)</option>
            <option value="consult">💬 Консультировать, мягко передавать</option>
            <option value="sale">💰 Вести к оплате/предоплате</option>
          </select>
        </div>
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
        <div style={{ ...row, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={!!overrides.digest_enabled}
                   onChange={e => upd('digest_enabled', e.target.checked)} />
            ☀️ Утренний дайджест в Telegram
            <Help title="Дайджест" text="Каждое утро система сама присылает сводку: новые лиды, кого дожать сегодня, просроченные задачи + совет. Идёт в тот же Telegram-чат, куда падают уведомления о лидах." />
          </label>
          <span className="muted">в</span>
          <input type="number" min={0} max={23} value={overrides.digest_hour}
                 onChange={e => upd('digest_hour', e.target.value)} style={{ width: 64 }} />
          <span className="muted">ч (UTC+{overrides.digest_tz_offset})</span>
          <button className="btn sm" onClick={async () => {
            try {
              const r = await api.post<any>('/digest/test')
              showToast(r.sent ? '📨 Дайджест отправлен в Telegram' : `Не отправлен: ${r.error}`, !r.sent)
            } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
          }}>📨 Прислать сейчас</button>
        </div>
      </div>
      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
    </div>
  )
}
