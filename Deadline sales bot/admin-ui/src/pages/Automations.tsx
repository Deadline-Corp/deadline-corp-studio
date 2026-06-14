import { useState } from 'react'
import { api } from '../api/client'
import { AutomationRuleItem } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useStages } from '../overviewContext'
import { CHANNEL_META, LOST_REASONS, TEMP_META } from '../lib'
import { HintBar } from '../components/HintBar'
import { Help } from '../components/Help'

/* Автоматизации: конструктор «Когда → Если → То» без кода (паттерн
   GoHighLevel/Chatwoot). Исполняет крон раз в ~10 минут. */

const ACTION_LABELS: Record<string, string> = {
  bot_message: '🤖 Бот пишет лиду',
  create_task: '📋 Задача менеджеру',
  set_stage: '📊 Сменить стадию',
  notify_admin: '🔔 Уведомить в Telegram',
}

export function Automations() {
  const [items, setItems] = useState<AutomationRuleItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [editing, setEditing] = useState<AutomationRuleItem | 'new' | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)
  const stages = useStages()

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 4000)
  }

  const load = async () => {
    try {
      const r = await api.get<{ items: AutomationRuleItem[] }>('/automations')
      setItems(r.items)
      setLoaded(true)
    } catch { /* ignore */ }
  }
  usePolling(load, 30000)

  const toggle = async (id: string) => {
    setBusy(true)
    try { await api.post(`/automations/${id}/toggle`); await load() }
    catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const remove = async (r: AutomationRuleItem) => {
    if (!confirm(`Удалить правило «${r.name}»?`)) return
    setBusy(true)
    try { await api.post(`/automations/${r.id}/delete`); showToast('Удалено'); await load() }
    catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const describe = (r: AutomationRuleItem): string => {
    const parts: string[] = []
    if (r.trigger.type === 'lead_silent') parts.push(`лид молчит ${r.trigger.hours} ч`)
    if (r.trigger.type === 'new_lead') parts.push('появился новый лид')
    if (r.trigger.type === 'sequence') parts.push(`цепочка из ${((r.trigger as any).steps || []).length} касаний`)
    if (r.trigger.type === 'stage_changed') {
      const ts = (r.trigger as any).to_stage
      parts.push(ts ? `стадия → «${stages.find(x => x.stage === ts)?.label ?? ts}»` : 'стадия изменилась')
    }
    const c = r.conditions || {}
    if (c.channels?.length) parts.push(`канал: ${c.channels.map(x => CHANNEL_META[x]?.label ?? x).join('/')}`)
    if (c.stages?.length) parts.push(`стадия: ${c.stages.map(s => stages.find(x => x.stage === s)?.label ?? s).join(', ')}`)
    if (c.min_score) parts.push(`скор ≥ ${c.min_score}`)
    return parts.join(' · ')
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Автоматизации</h1>
        <span className="sub">бот работает по этим правилам сам, проверка каждые ~10 минут</span>
        <div className="spacer" />
        <button className="btn primary" onClick={() => setEditing('new')}>+ Новое правило</button>
      </div>

      <HintBar id="automations" icon="⚡">
        Глобальные правила для бота: <b>«Когда → Если → То»</b>. Например: «появился новый лид →
        поставить мне задачу позвонить» или «лид молчит сутки → бот мягко напомнит о себе».
        Собирается кнопками, без программиста. Проверка каждые ~10 минут, у каждого правила — счётчик срабатываний.
      </HintBar>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {loaded && items.length === 0 && (
          <div className="empty">
            Правил пока нет. Пример: «Когда лид молчит 24 часа → бот мягко напоминает о себе».<br />
            Готовые наборы под нишу — в Настройках → «Пресеты ниш».
          </div>
        )}
        {items.map(r => (
          <div key={r.id} className="card" style={{ display: 'flex', alignItems: 'center', gap: 12, opacity: r.enabled ? 1 : 0.55 }}>
            <label style={{ cursor: 'pointer' }} title={r.enabled ? 'Выключить' : 'Включить'}>
              <input type="checkbox" checked={r.enabled} onChange={() => toggle(r.id)} disabled={busy} />
            </label>
            <div style={{ flex: 1, minWidth: 0 }}>
              <b style={{ fontSize: 13.5 }}>{r.name}</b>
              <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                Когда {describe(r)} → {r.actions.map(a => ACTION_LABELS[a.type] ?? a.type).join(' + ')}
              </div>
            </div>
            <span className="chip" title="Сколько раз сработало">⚡ {r.fired_count}</span>
            {r.cooldown_hours > 0 && <span className="chip info">повтор через {r.cooldown_hours}ч</span>}
            <button className="btn sm" onClick={() => setEditing(r)}>Изменить</button>
            <button className="btn sm ghost" onClick={() => remove(r)}>✕</button>
          </div>
        ))}
      </div>

      {editing && (
        <RuleEditor
          rule={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); showToast('✅ Сохранено'); void load() }}
        />
      )}

      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
    </div>
  )
}

/* ---------- Редактор правила ---------- */

function RuleEditor({ rule, onClose, onSaved }: {
  rule: AutomationRuleItem | null
  onClose: () => void
  onSaved: () => void
}) {
  const stages = useStages()
  const [name, setName] = useState(rule?.name ?? '')
  const [ttype, setTtype] = useState<string>(rule?.trigger.type ?? 'lead_silent')
  const [hours, setHours] = useState(String(rule?.trigger.hours ?? 24))
  const [toStage, setToStage] = useState<string>((rule?.trigger as any)?.to_stage ?? '')
  const [steps, setSteps] = useState<any[]>(
    (rule?.trigger as any)?.steps ?? [
      { hours: 24, text: '' },
      { hours: 72, text: '' },
      { hours: 168, text: '' },
    ]
  )
  const [channels, setChannels] = useState<string[]>(rule?.conditions?.channels ?? [])
  const [condStages, setCondStages] = useState<string[]>(rule?.conditions?.stages ?? [])
  const [minScore, setMinScore] = useState(String(rule?.conditions?.min_score ?? ''))
  const [cooldown, setCooldown] = useState(String(rule?.cooldown_hours ?? 0))
  const [actions, setActions] = useState<any[]>(rule?.actions ?? [{ type: 'bot_message', text: '' }])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const toggleIn = (arr: string[], v: string, set: (x: string[]) => void) =>
    set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v])

  const updAction = (i: number, patch: any) =>
    setActions(actions.map((a, k) => (k === i ? { ...a, ...patch } : a)))

  const save = async () => {
    setBusy(true)
    setErr('')
    try {
      await api.post('/automations', {
        id: rule?.id,
        name: name.trim() || 'Без названия',
        enabled: rule?.enabled ?? true,
        trigger: ttype === 'new_lead'
          ? { type: 'new_lead' }
          : ttype === 'sequence'
            ? { type: 'sequence', steps: steps.map(s => ({ hours: parseFloat(s.hours), text: (s.text || '').trim() })) }
            : ttype === 'stage_changed'
              ? { type: 'stage_changed', to_stage: toStage || undefined }
              : { type: 'lead_silent', hours: parseFloat(hours) },
        conditions: {
          channels: channels.length ? channels : undefined,
          stages: condStages.length ? condStages : undefined,
          min_score: minScore ? parseInt(minScore, 10) : undefined,
        },
        actions: ttype === 'sequence' ? [] : actions,
        cooldown_hours: ttype === 'sequence' ? 0 : (parseInt(cooldown, 10) || 0),
      })
      onSaved()
    } catch (e: any) {
      const probs = e.detail?.problems
      setErr(probs ? probs.join('; ') : (typeof e.detail === 'string' ? e.detail : e.message))
    } finally { setBusy(false) }
  }

  const block: React.CSSProperties = { background: 'var(--bg-soft)', borderRadius: 8, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 8 }

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div className="card" style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        zIndex: 50, width: 620, maxHeight: '88vh', overflowY: 'auto',
        display: 'flex', flexDirection: 'column', gap: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <b>{rule ? 'Правило' : 'Новое правило'}</b>
          <div style={{ flex: 1 }} />
          <button className="btn ghost" onClick={onClose}>✕</button>
        </div>

        <input placeholder="Название (для себя, напр. «Дожим молчунов»)"
               value={name} onChange={e => setName(e.target.value)} />

        <div style={block}>
          <b style={{ fontSize: 13 }}>⏰ КОГДА <Help title="Триггер" text="Событие, запускающее правило. «Молчит N часов» — для прогрева и дожима. «Появился новый лид» — для мгновенной реакции: уведомить вас, поставить задачу." /></b>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, flexWrap: 'wrap' }}>
            <select value={ttype} onChange={e => setTtype(e.target.value)} style={{ fontSize: 13 }}>
              <option value="lead_silent">Лид молчит N часов</option>
              <option value="new_lead">Появился новый лид</option>
              <option value="sequence">Цепочка касаний (день 1 → 3 → 7)</option>
              <option value="stage_changed">Стадия изменилась</option>
            </select>
            {ttype === 'lead_silent' && (
              <>
                <input type="number" min={0.5} step={0.5} value={hours}
                       onChange={e => setHours(e.target.value)} style={{ width: 80 }} />
                часов
              </>
            )}
            {ttype === 'new_lead' && (
              <span className="faint" style={{ fontSize: 12 }}>сработает в течение ~10 минут после появления, один раз</span>
            )}
            {ttype === 'stage_changed' && (
              <>
                <select value={toStage} onChange={e => setToStage(e.target.value)} style={{ fontSize: 13 }}>
                  <option value="">на любую стадию</option>
                  {stages.map(s => <option key={s.stage} value={s.stage}>{s.label}</option>)}
                </select>
                <span className="faint" style={{ fontSize: 12 }}>сработает сразу при переходе</span>
              </>
            )}
          </div>
          {ttype === 'sequence' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span className="faint" style={{ fontSize: 12 }}>
                Серия напоминаний молчащему лиду. Часы — от его последнего сообщения; ответил —
                цепочка останавливается. Telegram-лиду пишет бот сам; остальным — задача вам с готовым текстом.
              </span>
              {steps.map((st, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <span className="muted" style={{ fontSize: 12, paddingTop: 8, whiteSpace: 'nowrap' }}>Касание {i + 1}: через</span>
                  <input type="number" min={1} value={st.hours}
                         onChange={e => setSteps(steps.map((x, k) => k === i ? { ...x, hours: e.target.value } : x))}
                         style={{ width: 70 }} />
                  <span className="muted" style={{ fontSize: 12, paddingTop: 8 }}>ч</span>
                  <textarea placeholder="Текст касания…" value={st.text}
                            onChange={e => setSteps(steps.map((x, k) => k === i ? { ...x, text: e.target.value } : x))}
                            style={{ flex: 1, minHeight: 40, fontSize: 12.5 }} />
                  {steps.length > 1 && (
                    <button className="btn sm ghost" onClick={() => setSteps(steps.filter((_, k) => k !== i))}>✕</button>
                  )}
                </div>
              ))}
              {steps.length < 5 && (
                <button className="btn sm" style={{ alignSelf: 'flex-start' }}
                        onClick={() => setSteps([...steps, { hours: (parseFloat(steps[steps.length - 1]?.hours) || 24) * 2, text: '' }])}>
                  + касание
                </button>
              )}
            </div>
          )}
        </div>

        <div style={block}>
          <b style={{ fontSize: 13 }}>🔍 ЕСЛИ (пусто = любой) <Help title="Условия" text="Фильтр: правило сработает только для лидов, подходящих под выбранные канал/стадию/скор. Ничего не выбрано — для всех. Скор — это «теплота» лида от 0 до 100+, бот считает его сам." /></b>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', fontSize: 12.5 }}>
            <span className="muted">Канал:</span>
            {Object.entries(CHANNEL_META).map(([id, m]) => (
              <button key={id} className={`btn sm ${channels.includes(id) ? 'primary' : ''}`}
                      onClick={() => toggleIn(channels, id, setChannels)}>{m.icon} {m.label}</button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', fontSize: 12.5 }}>
            <span className="muted">Стадия:</span>
            {stages.map(s => (
              <button key={s.stage} className={`btn sm ${condStages.includes(s.stage) ? 'primary' : ''}`}
                      onClick={() => toggleIn(condStages, s.stage, setCondStages)}>{s.label}</button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5 }}>
            <span className="muted">Мин. скор лида:</span>
            <input type="number" min={0} value={minScore} placeholder="—"
                   onChange={e => setMinScore(e.target.value)} style={{ width: 80 }} />
          </div>
        </div>

        {ttype !== 'sequence' && (
        <div style={block}>
          <b style={{ fontSize: 13 }}>⚡ ТО <Help title="Действия" text="Что сделать: бот напишет лиду (только Telegram) · задача вам в «Мой день» · перевести по воронке · уведомить вас в Telegram. Можно несколько действий сразу." /></b>
          {actions.map((a, i) => (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 6, borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <select value={a.type} onChange={e => updAction(i, { type: e.target.value, text: a.text })} style={{ fontSize: 12.5 }}>
                  {Object.entries(ACTION_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                {a.type === 'create_task' && (
                  <span style={{ fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 5 }}>
                    через <input type="number" min={0} value={a.due_in_hours ?? 0}
                                 onChange={e => updAction(i, { due_in_hours: parseFloat(e.target.value) })}
                                 style={{ width: 60 }} /> ч
                  </span>
                )}
                {a.type === 'set_stage' && (
                  <>
                    <select value={a.stage ?? ''} onChange={e => updAction(i, { stage: e.target.value })} style={{ fontSize: 12.5 }}>
                      <option value="">стадия…</option>
                      {stages.map(s => <option key={s.stage} value={s.stage}>{s.label}</option>)}
                    </select>
                    {(a.stage === 'lost' || stages.find(s => s.stage === a.stage)?.kind === 'lost') && (
                      <select value={a.lost_reason ?? 'delayed'} onChange={e => updAction(i, { lost_reason: e.target.value })} style={{ fontSize: 12.5 }}>
                        {LOST_REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                      </select>
                    )}
                  </>
                )}
                {actions.length > 1 && (
                  <button className="btn sm ghost" style={{ marginLeft: 'auto' }}
                          onClick={() => setActions(actions.filter((_, k) => k !== i))}>✕</button>
                )}
              </div>
              {a.type !== 'set_stage' && (
                <textarea placeholder={a.type === 'bot_message' ? 'Текст сообщения лиду…' : 'Текст задачи/уведомления…'}
                          value={a.text ?? ''} onChange={e => updAction(i, { text: e.target.value })}
                          style={{ minHeight: 44, fontSize: 12.5 }} />
              )}
              {a.type === 'bot_message' && (
                <span className="faint" style={{ fontSize: 11 }}>⚠️ Автоотправка от бота пока только Telegram-лидам; для прочих каналов действие пропустится</span>
              )}
            </div>
          ))}
          <button className="btn sm" style={{ alignSelf: 'flex-start' }}
                  onClick={() => setActions([...actions, { type: 'create_task', text: '', due_in_hours: 0 }])}>
            + ещё действие
          </button>
        </div>
        )}

        {ttype !== 'sequence' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5 }}>
          <span className="muted">Повторять не чаще, чем раз в</span>
          <input type="number" min={0} value={cooldown} onChange={e => setCooldown(e.target.value)} style={{ width: 70 }} />
          <span className="muted">часов (0 = один раз на лида, максимум 5 повторов)</span>
          <Help title="Защита от спама" text="Правило не долбит одного лида: 0 — сработает один раз и всё; больше нуля — может повториться, но не чаще указанного и максимум 5 раз." />
        </div>
        )}

        {err && <div style={{ color: 'var(--danger)', fontSize: 13 }}>{err}</div>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>Отмена</button>
          <button className="btn primary" onClick={save} disabled={busy}>
            {busy ? <span className="spin" /> : 'Сохранить'}
          </button>
        </div>
      </div>
    </>
  )
}
