import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { ConvDetail, Msg } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useStages, useStageLabel, useMe } from '../overviewContext'
import { CHANNEL_META, LOST_REASONS, TEMP_META, fmtTime, initials } from '../lib'
import { Help } from './Help'

/* Карточка лида: переписка + ответ + takeover + стадия + пинок + задача.
   Один и тот же компонент из Inbox, Канбана и Канваса. Стадии — динамические
   (кастомная воронка из overview). */

export function ConversationDrawer({ convId, onClose }: { convId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ConvDetail | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)
  const [stagePick, setStagePick] = useState('')
  const [lostReason, setLostReason] = useState('delayed')
  const [nudgeOpen, setNudgeOpen] = useState(false)
  const [taskOpen, setTaskOpen] = useState(false)
  const [taskText, setTaskText] = useState('')
  const [taskDue, setTaskDue] = useState('')
  const [taskExec, setTaskExec] = useState<'human' | 'bot'>('human')
  const [fieldEdits, setFieldEdits] = useState<Record<string, any>>({})
  const [fieldsOpen, setFieldsOpen] = useState(false)
  const [callDt, setCallDt] = useState('')  // ручной перенос/назначение созвона
  const [advice, setAdvice] = useState('')
  const [team, setTeam] = useState<any[]>([])
  const [draftText, setDraftText] = useState('')  // редактируемый предложенный ботом ответ (WhatsApp)
  const [draftOpen, setDraftOpen] = useState(true)  // свернуть блок «система предлагает», чтобы видеть переписку
  const [replyOpen, setReplyOpen] = useState(false) // окно ручного ответа оператора — по умолчанию свёрнуто
  const [actionsOpen, setActionsOpen] = useState(false) // панель действий сверху — по умолчанию свёрнута (видно переписку)
  const [historyOpen, setHistoryOpen] = useState(false) // «почему лид на этой стадии» — история переходов
  const msgsRef = useRef<HTMLDivElement>(null)
  const lastTsRef = useRef<string | null>(null)
  const lastIdRef = useRef<string | null>(null)  // keyset-курсор вниз (новые): вторичный ключ по id
  const firstTsRef = useRef<string | null>(null)  // keyset-курсор вверх (старые): для «показать ранее»
  const firstIdRef = useRef<string | null>(null)
  const [hasMore, setHasMore] = useState(true)
  const [loadingOlder, setLoadingOlder] = useState(false)

  const stages = useStages()
  const stageLabel = useStageLabel()
  const me = useMe()
  const [learned, setLearned] = useState<Set<string>>(new Set())

  const learnFrom = async (messageId: string) => {
    if (busy) return
    setBusy(true)
    try {
      await api.post('/training-rules/from-message', { conversation_id: convId, message_id: messageId })
      setLearned(prev => new Set(prev).add(messageId))
      showToast('🎓 Бот выучил этот ответ — применит в похожих ситуациях')
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 3500)
  }

  const setCall = async (action: 'reschedule' | 'cancel') => {
    if (busy) return
    const body: any = { action }
    if (action === 'reschedule') {
      if (!callDt) { showToast('Выберите дату и время созвона', true); return }
      body.time = new Date(callDt).toISOString()  // datetime-local (локальное) → UTC
    }
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/call`, body)
      showToast(action === 'cancel' ? 'Созвон отменён, напоминания сняты' : '📞 Созвон назначен — напоминания пересозданы')
      setCallDt('')
      await loadDetail()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const suggestReply = async () => {
    if (busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/suggest-reply`, {})
      await loadDetail()  // свежий черновик появится в поле — тост не нужен (закрывал кнопку «Отправить»)
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const loadDetail = async () => {
    try { setDetail(await api.get<ConvDetail>(`/conversations/${convId}`)) } catch { /* drawer закроют по 401 */ }
  }

  const loadMessages = async (initial = false) => {
    try {
      if (initial || !lastTsRef.current) {
        const r = await api.get<{ items: Msg[] }>(`/conversations/${convId}/messages?limit=80`)
        setMsgs(r.items)
        const last = r.items.length ? r.items[r.items.length - 1] : null
        lastTsRef.current = last ? last.created_at : null
        lastIdRef.current = last ? last.id : null
        const first = r.items.length ? r.items[0] : null
        firstTsRef.current = first ? first.created_at : null
        firstIdRef.current = first ? first.id : null
        setHasMore(r.items.length >= 80)  // полная страница → возможно есть ещё ранее
        scrollDown()
      } else {
        // keyset-курсор (created_at + id) — не теряем сообщения с одинаковым timestamp.
        const qs = `after=${encodeURIComponent(lastTsRef.current)}`
          + (lastIdRef.current ? `&after_id=${encodeURIComponent(lastIdRef.current)}` : '')
        const r = await api.get<{ items: Msg[] }>(`/conversations/${convId}/messages?${qs}`)
        if (r.items.length) {
          setMsgs(prev => [...prev, ...r.items])
          const last = r.items[r.items.length - 1]
          lastTsRef.current = last.created_at
          lastIdRef.current = last.id
          scrollDown()
        }
      }
    } catch { /* поллинг переживёт разовый сбой */ }
  }

  const scrollDown = () => {
    requestAnimationFrame(() => {
      msgsRef.current?.scrollTo({ top: msgsRef.current.scrollHeight })
    })
  }

  // «Показать более ранние» — подгрузка истории вверх (keyset before+before_id), чтобы
  // длинная переписка в панели = переписка в мессенджере. Сохраняем позицию прокрутки.
  const loadOlder = async () => {
    if (loadingOlder || !firstTsRef.current) return
    setLoadingOlder(true)
    try {
      const qs = `before=${encodeURIComponent(firstTsRef.current)}`
        + (firstIdRef.current ? `&before_id=${encodeURIComponent(firstIdRef.current)}` : '')
        + '&limit=60'
      const r = await api.get<{ items: Msg[] }>(`/conversations/${convId}/messages?${qs}`)
      if (r.items.length) {
        const box = msgsRef.current
        const prevH = box ? box.scrollHeight : 0
        setMsgs(prev => [...r.items, ...prev])
        firstTsRef.current = r.items[0].created_at
        firstIdRef.current = r.items[0].id
        requestAnimationFrame(() => { if (box) box.scrollTop = box.scrollHeight - prevH })
      }
      if (r.items.length < 60) setHasMore(false)
    } catch { /* разовый сбой переживём */ }
    finally { setLoadingOlder(false) }
  }

  useEffect(() => {
    lastTsRef.current = null
    lastIdRef.current = null
    firstTsRef.current = null
    firstIdRef.current = null
    setHasMore(true)
    setMsgs([])
    setDetail(null)
    void loadDetail()
    void loadMessages(true)
    void api.get<{ items: any[] }>('/team').then(r => setTeam(r.items || [])).catch(() => { /* менеджеру /team закрыт — дропдаун скрыт */ })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId])

  usePolling(() => loadMessages(false), 5000, [convId])
  // Шапка тоже живая: стадия/телефон/такеовер/черновик обновляются, пока карточка
  // открыта (раньше detail грузился один раз → данные протухали; напр. одобрение
  // черновика из Telegram или смена стадии ботом не отражались до переоткрытия).
  usePolling(loadDetail, 8000, [convId])

  // Подставляем предложенный ботом ответ в редактируемое поле, когда он появляется/меняется.
  useEffect(() => {
    setDraftText(detail?.pending_wa_draft?.text || '')
  }, [detail?.pending_wa_draft?.ts])

  const sendWaDraft = async () => {
    if (busy || !draftText.trim()) return
    setBusy(true)
    try {
      const r = await api.post<{ delivered: boolean }>(`/conversations/${convId}/wa-draft`, {
        action: 'send', text: draftText.trim(),
      })
      showToast(r.delivered ? '✅ Отправлено клиенту в WhatsApp' : '⚠️ Сохранено, но не доставлено (см. логи)', !r.delivered)
      await loadDetail(); await loadMessages(false)
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }
  const rejectWaDraft = async () => {
    if (busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/wa-draft`, { action: 'reject' })
      showToast('🚫 Черновик отклонён — клиенту не ушёл')
      await loadDetail()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }
  const setWaAutonomous = async (on: boolean) => {
    if (busy) return
    setBusy(true)
    try {
      const r = await api.post<{ sent: boolean }>(`/conversations/${convId}/wa-autonomous`, { on })
      showToast(on
        ? (r.sent ? '🤖 Бот ведёт диалог сам — предложенный ответ отправлен' : '🤖 Бот ведёт этот диалог сам')
        : 'Возвращено на одобрение')
      await loadDetail(); await loadMessages(false)
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  // ЕДИНОЕ поле ответа: отправляет то, что в поле (предложку бота — отредактированную
  // или свой текст). Если есть черновик WhatsApp — через wa-draft (он же его чистит),
  // иначе обычный operator-reply (работает для всех каналов).
  const sendReply = async () => {
    const t = draftText.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      let delivered = false; let channel = ''
      if (detail?.pending_wa_draft) {
        const r = await api.post<{ delivered: boolean }>(`/conversations/${convId}/wa-draft`, { action: 'send', text: t })
        delivered = r.delivered
      } else {
        const r = await api.post<{ delivered: boolean; channel: string }>(`/conversations/${convId}/reply`, { text: t })
        delivered = r.delivered; channel = r.channel
      }
      if (!delivered) showToast('⚠️ Сохранено, но НЕ доставлено лиду (см. логи)', true)
      else if (channel === 'website') showToast('Сохранено. Website-лид увидит при следующем визите.')
      else showToast('✅ Доставлено лиду')
      setDraftText('')
      await loadDetail(); await loadMessages(false)
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }
  const toggleTakeover = async () => {
    if (!detail || busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/takeover`, { on: !detail.operator_takeover })
      showToast(detail.operator_takeover ? '🤖 Вернули боту' : '👤 Взяли на себя — бот молчит')
      await loadDetail()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const isLostStage = (key: string) =>
    key === 'lost' || stages.find(s => s.stage === key)?.kind === 'lost'

  const applyStage = async () => {
    if (!stagePick || !detail || busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/stage`, {
        to_stage: stagePick,
        lost_reason: isLostStage(stagePick) ? lostReason : undefined,
      })
      showToast(`Стадия → ${stageLabel(stagePick)}`)
      setStagePick('')
      await loadDetail()
      await loadMessages(false)
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const nudgeDraft = async () => {
    setBusy(true)
    try {
      const r = await api.post<{ draft: string }>(`/conversations/${convId}/nudge`, { mode: 'draft' })
      setText(r.draft)
      setNudgeOpen(false)
      showToast('Черновик пинка готов — правьте и отправляйте')
    } catch (e: any) { showToast(`Ошибка черновика: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const advise = async () => {
    setBusy(true)
    setAdvice('')
    try {
      const r = await api.post<{ action: string; draft: string }>(`/conversations/${convId}/advise`, {})
      setAdvice(r.action || 'нет рекомендации')
      if (r.draft) setText(r.draft)
      showToast('🧭 Совет готов — черновик в поле ответа')
    } catch (e: any) { showToast(`Ошибка совета: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const nudgeNow = async () => {
    const t = text.trim()
    if (!t) { showToast('Сначала напишите текст пинка (или возьмите черновик)', true); return }
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/nudge`, { mode: 'now', text: t })
      setText('')
      setNudgeOpen(false)
      showToast('✅ Пинок отправлен от имени бота')
      await loadMessages(false)
    } catch (e: any) { showToast(`${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const saveFields = async () => {
    if (!Object.keys(fieldEdits).length || busy) return
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/fields`, { values: fieldEdits })
      setFieldEdits({})
      showToast('✅ Поля сохранены')
      await loadDetail()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const createTask = async () => {
    if (!taskText.trim() || !taskDue || busy) return
    setBusy(true)
    try {
      await api.post('/tasks', {
        conversation_id: convId,
        text: taskText.trim(),
        due_at: new Date(taskDue).toISOString(),
        executor: taskExec,
      })
      showToast(taskExec === 'bot' ? '🤖 Бот напишет лиду в срок' : '📋 Задача поставлена')
      setTaskOpen(false)
      setTaskText('')
      setTaskDue('')
      await loadDetail()
    } catch (e: any) { showToast(`${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const ch = detail ? CHANNEL_META[detail.channel] : null
  const temp = detail ? TEMP_META[detail.customer.lead_temperature] : null

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <div className="drawer">
        <div className="d-head">
          <div className="d-title">
            <div className="avatar" style={{ width: 38, height: 38, borderRadius: '50%', background: 'var(--panel-2)', display: 'grid', placeItems: 'center', color: 'var(--accent)', fontWeight: 700 }}>
              {initials(detail?.customer.name)}
            </div>
            <h2>{detail?.customer.display_name || detail?.customer.name || 'Без имени'}</h2>
            <button className="btn ghost" onClick={onClose}>✕</button>
          </div>
          {detail && (
            <>
              <div className="d-chips">
                <span className={`chip ${ch?.cls ?? ''}`}>{ch?.icon} {ch?.label ?? detail.channel}</span>
                <span className="chip accent">{stageLabel(detail.lead_stage)}</span>
                {temp && <span className={`chip ${temp.cls}`}>{temp.label}</span>}
                {temp && <Help title="Температура лида — считается сама"
                  text="Насколько лид «горячий», бот определяет АВТОМАТИЧЕСКИ по поведению: 🧊 cold (нет вовлечения) → 🌤 warm (2+ ответа по делу) → 🔥 hot (спросил цену/сроки/портфолио) → 🚀 ready (готов начинать) → 🤝 client (внёс предоплату). ❄️ frozen — молчит 21+ день. Остывание: 14 дней тишины → на уровень ниже, 21 день → frozen; клиент не остывает. Влияет на ПРИОРИТЕТ дожима и скоринг (кого пинать первым), но НЕ на текст ответов бота." />}
                <span className="chip">скор {detail.customer.lead_score}</span>
                {detail.operator_takeover && <span className="chip ok">👤 на операторе</span>}
                {detail.customer.email && <span className="chip mono">{detail.customer.email}</span>}
                {detail.customer.phone && <span className="chip mono">{detail.customer.phone}</span>}
                {!detail.customer.phone && detail.wa_hidden_phone && (
                  <span className="chip mono dim" title="Лид пришёл из рекламы WhatsApp под скрытым ID (@lid). WhatsApp прячет номер таких переходов ради приватности — это не сбой синхронизации. Как только WhatsApp раскроет номер (контакт синкнётся / лид напишет ещё), бот подставит его автоматически.">
                    📵 номер скрыт (реклама)
                  </span>
                )}
                <div style={{ flex: 1 }} />
                {me?.role === 'viewer'
                  ? <span className="chip" title="Роль «наблюдатель» — только просмотр, без изменений">👁 только просмотр</span>
                  : <button className="btn sm ghost" onClick={() => setActionsOpen(v => !v)}
                            title="Действия со сделкой — свернуть/развернуть, чтобы видеть переписку">
                      {actionsOpen ? '▾ Действия' : '⚙️ Действия'}
                    </button>}
              </div>
              {actionsOpen && <div className="d-actions">
                <button className="btn sm" onClick={toggleTakeover} disabled={busy}>
                  {detail.operator_takeover ? '🤖 Вернуть боту' : '👤 Взять на себя'}
                </button>
                <Help title="Взять на себя" text="Бот замолкает в этом диалоге — отвечаете только вы. Лид ничего не заметит. Когда закончите, верните боту — он продолжит сам с того же места." />
                {detail.channel === 'whatsapp' && !detail.pending_wa_draft && (
                  <button className="btn sm" onClick={suggestReply} disabled={busy} title="Система прочитает всю переписку и предложит ответ">🔄 Предложить ответ</button>
                )}
                <Help title="Стадия" text="Где лид в вашей воронке. Бот двигает сделку сам по мере прогресса; вы можете перевести вручную здесь или перетащив карточку в Воронке. Изменение уходит и в CRM." />
                <select value={stagePick} onChange={e => setStagePick(e.target.value)} style={{ padding: '4px 8px', fontSize: 12 }}>
                  <option value="">Сменить стадию…</option>
                  {stages.filter(s => s.stage !== detail.lead_stage).map(s => (
                    <option key={s.stage} value={s.stage}>{s.label}</option>
                  ))}
                </select>
                {stagePick && isLostStage(stagePick) && (
                  <select value={lostReason} onChange={e => setLostReason(e.target.value)} style={{ padding: '4px 8px', fontSize: 12 }}>
                    {LOST_REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                )}
                {stagePick && <button className="btn sm primary" onClick={applyStage} disabled={busy}>OK</button>}
                <button className="btn sm" onClick={() => { setNudgeOpen(v => !v); setTaskOpen(false) }}>⚡ Пинок</button>
                <Help title="Пинок" text="Лид замолчал? Отправьте напоминание от имени бота — диалог продолжится естественно. Кнопка «Черновик от LLM» сама сочинит текст по контексту переписки." />
                <button className="btn sm" onClick={() => { setTaskOpen(v => !v); setNudgeOpen(false) }}>📋 Задача</button>
                <Help title="Задача" text="Напоминалка по этому лиду: «👤 сам» — появится в вашем «Моём дне»; «🤖 бот» — бот сам напишет лиду в указанное время (пока только Telegram)." />
                <button className="btn sm" onClick={advise} disabled={busy}>🧭 Что делать</button>
                <Help title="Что делать (AI-копилот)" text="Агент смотрит стадию, score и переписку → советует лучшее следующее действие и кладёт готовый черновик ответа в поле. Ничего не отправляет — решаете вы." />
                <button className="btn sm" disabled={busy} onClick={async () => {
                  const d = window.prompt('Регулярный клиент: визит каждые N дней (пусто или 0 — снять):', '')
                  if (d === null) return
                  const n = parseInt(d, 10) || 0
                  setBusy(true)
                  try {
                    await api.post(`/conversations/${convId}/recurrence`, { every_days: n > 0 ? n : null, active: n > 0 })
                    showToast(n > 0 ? `🔁 Регулярно каждые ${n} дн. — бот сам напомнит` : 'Регулярность снята')
                  } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
                  finally { setBusy(false) }
                }}>🔁 Регулярный</button>
                <Help title="Регулярный клиент" text="Постоянный клининг / ТО: бот сам шлёт плановое напоминание каждые N дней («подтвердите время — команда приедет»). Снять — введите 0." />
                <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                  {(detail as any).booked_call_at && <span className="chip accent">📞 {fmtTime((detail as any).booked_call_at)}</span>}
                  <input type="datetime-local" value={callDt} onChange={e => setCallDt(e.target.value)} style={{ fontSize: 12, padding: '3px 6px' }} title="Дата и время созвона" />
                  <button className="btn sm" onClick={() => setCall('reschedule')} disabled={busy}>📞 {(detail as any).booked_call_at ? 'Перенести' : 'Назначить'}</button>
                  {(detail as any).booked_call_at && <button className="btn sm ghost" onClick={() => setCall('cancel')} disabled={busy}>Отменить созвон</button>}
                  <Help title="Созвон" text="Назначить или перенести время созвона прямо из карточки. Бот пересоздаст напоминания (лиду в мессенджер и вам в опер-группу за сутки / 3 ч / 1 ч). Раньше это можно было только если лид сам напишет." />
                </span>
                {team.filter((m: any) => m.active).length > 0 && (
                  <select value="" disabled={busy} style={{ fontSize: 12 }}
                          onChange={async e => {
                            const v = e.target.value
                            if (!v) return
                            const mid = v === '__unassign__' ? null : v
                            setBusy(true)
                            try {
                              const r = await api.post<{ assigned: any }>(`/conversations/${convId}/assign`, { member_id: mid })
                              showToast(r.assigned ? `📋 Назначено: ${r.assigned.name}` : 'Назначение снято')
                            } catch (er: any) { showToast(`Ошибка: ${er.detail ?? er.message}`, true) }
                            finally { setBusy(false) }
                          }}>
                    <option value="">📋 Назначить на…</option>
                    {team.filter((m: any) => m.active).map((m: any) => (
                      <option key={m.id} value={m.id}>{m.name}{m.department ? ` · ${m.department}` : ''}</option>
                    ))}
                    <option value="__unassign__">— снять назначение —</option>
                  </select>
                )}
                {detail.hubspot.contact_url && (
                  <a className="btn sm ghost" href={detail.hubspot.contact_url} target="_blank" rel="noreferrer">HubSpot ↗</a>
                )}
              </div>}
              {advice && (
                <div className="d-actions" style={{ background: 'var(--accent-soft)', borderRadius: 8, padding: '8px 10px', alignItems: 'flex-start' }}>
                  <span style={{ fontSize: 12.5, flex: 1 }}><b>🧭 Совет:</b> {advice}</span>
                  <button className="btn sm ghost" onClick={() => setAdvice('')}>✕</button>
                </div>
              )}
              {nudgeOpen && (
                <div className="d-actions" style={{ background: 'var(--panel)', borderRadius: 8, padding: '8px 10px' }}>
                  <span className="muted" style={{ fontSize: 12 }}>Пинок зависшему лиду (уйдёт от имени бота):</span>
                  <button className="btn sm" onClick={nudgeDraft} disabled={busy}>🪄 Черновик от LLM</button>
                  <button className="btn sm primary" onClick={nudgeNow} disabled={busy}>Отправить сейчас</button>
                </div>
              )}
              {taskOpen && (
                <div style={{ background: 'var(--panel)', borderRadius: 8, padding: '10px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <input placeholder="Что сделать (напр. «напомнить про КП»)"
                         value={taskText} onChange={e => setTaskText(e.target.value)} />
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <input type="datetime-local" value={taskDue} onChange={e => setTaskDue(e.target.value)} />
                    <select value={taskExec} onChange={e => setTaskExec(e.target.value as any)} style={{ fontSize: 12 }}>
                      <option value="human">👤 Сделаю сам</option>
                      <option value="bot">🤖 Бот напишет лиду</option>
                    </select>
                    <button className="btn sm primary" onClick={createTask} disabled={busy || !taskText.trim() || !taskDue}>
                      Поставить
                    </button>
                  </div>
                  {taskExec === 'bot' && detail.channel !== 'telegram' && (
                    <span className="faint" style={{ fontSize: 11.5 }}>⚠️ Бот-автоотправка пока только для Telegram-лидов</span>
                  )}
                </div>
              )}
              {detail.fields && detail.fields.length > 0 && (
                <div style={{ background: 'var(--panel)', borderRadius: 8, padding: '8px 10px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', fontSize: 12.5 }}
                       onClick={() => setFieldsOpen(v => !v)}>
                    <b>📇 Поля</b>
                    <span className="faint" style={{ marginLeft: 8 }}>
                      {detail.fields.filter(f => f.value != null && f.value !== '').length}/{detail.fields.length} заполнено
                    </span>
                    <div style={{ flex: 1 }} />
                    <span>{fieldsOpen ? '▾' : '▸'}</span>
                  </div>
                  {fieldsOpen && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                      {detail.fields.map(f => {
                        const cur = fieldEdits[f.key] !== undefined ? fieldEdits[f.key] : (f.value ?? '')
                        return (
                          <div key={f.key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span className="muted" style={{ width: 130, fontSize: 12 }}>{f.label}</span>
                            {f.field_type === 'select' ? (
                              <select value={cur} style={{ flex: 1, fontSize: 12, padding: '4px 8px' }}
                                      onChange={e => setFieldEdits({ ...fieldEdits, [f.key]: e.target.value })}>
                                <option value="">—</option>
                                {(f.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
                              </select>
                            ) : (
                              <input value={cur} type={f.field_type === 'number' ? 'number' : 'text'}
                                     style={{ flex: 1, fontSize: 12, padding: '4px 8px' }}
                                     onChange={e => setFieldEdits({ ...fieldEdits, [f.key]: e.target.value })} />
                            )}
                          </div>
                        )
                      })}
                      {Object.keys(fieldEdits).length > 0 && (
                        <button className="btn sm primary" style={{ alignSelf: 'flex-end' }}
                                onClick={saveFields} disabled={busy}>💾 Сохранить поля</button>
                      )}
                    </div>
                  )}
                </div>
              )}
              {(() => {
                // Показываем ТОЛЬКО актуальные задачи. Авто-плумбинг созвона прячем:
                // сам созвон уже виден как «📞 14:00» в Действиях, а напоминания
                // (call_reminder) идут ПАРАМИ лид+админ на каждый слот -3ч/-1ч →
                // в чипах выглядели как дубли. Они автоматические — не задачи.
                const acts = detail.scheduled_actions.filter(
                  a => a.action_type !== 'call_reminder' && a.action_type !== 'call_booked',
                )
                if (acts.length === 0) return null
                return (
                  <div className="d-chips">
                    {acts.map(a => (
                      <span key={a.id} className="chip info" title={a.payload?.text ?? ''}>
                        ⏳ {a.executor === 'bot' ? 'бот' : 'я'}: {fmtTime(a.due_at)}
                      </span>
                    ))}
                  </div>
                )
              })()}
            </>
          )}
        </div>

        {detail && (detail.stage_history?.length ?? 0) > 0 && (
          <div style={{ padding: '0 14px 8px' }}>
            <button className="btn sm ghost" onClick={() => setHistoryOpen(v => !v)}
                    title="Кто и когда двигал лида по воронке — решения бота прозрачны">
              {historyOpen ? '▾' : '▸'} 📋 Почему лид на этой стадии
            </button>
            {historyOpen && (
              <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4,
                            fontSize: 12, color: 'var(--text-dim)' }}>
                {detail.stage_history!.map((h, i) => {
                  const by = /admin|manual|ui/i.test(h.by) ? '👤 вручную'
                    : /bot/i.test(h.by) ? '🤖 бот'
                    : '⚙️ авто'
                  return (
                    <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
                      <span className="faint" style={{ minWidth: 96 }}>{h.at ? fmtTime(h.at) : ''}</span>
                      <span>{h.from ? `${stageLabel(h.from)} → ` : ''}<b>{stageLabel(h.to)}</b></span>
                      <span className="faint">· {by}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        <div className="d-msgs" ref={msgsRef}>
          {hasMore && msgs.length > 0 && (
            <button className="btn sm ghost load-older" onClick={loadOlder} disabled={loadingOlder}
                    style={{ alignSelf: 'center', marginBottom: 8 }}>
              {loadingOlder ? '…' : '↑ Показать более ранние'}
            </button>
          )}
          {msgs.length === 0 && <div className="empty">Сообщений пока нет</div>}
          {msgs.map(m => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.content}
              <div className="m-meta">
                {m.role === 'operator' && '👤 оператор · '}
                {m.role === 'assistant' && m.extra_meta?.kind === 'manual_nudge' && '⚡ ручной пинок · '}
                {fmtTime(m.created_at)}
                {m.role === 'operator' && (!me || me.role === 'owner') && (
                  learned.has(m.id)
                    ? <span style={{ marginLeft: 8 }}>🎓 выучено</span>
                    : (
                      <a style={{ marginLeft: 8, color: 'var(--accent)', cursor: 'pointer' }}
                         title="Бот запомнит этот ответ и будет отвечать похоже в подобных ситуациях"
                         onClick={() => learnFrom(m.id)}>
                        🎓 научить бота
                      </a>
                    )
                )}
              </div>
            </div>
          ))}
        </div>

        {detail?.pending_call_suggestion?.at && (
          <div style={{ borderTop: '1px solid var(--accent-border)', background: 'var(--accent-soft)', padding: '10px 14px' }}>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <b>📅 Похоже, договорились о созвоне</b>
              <div style={{ marginTop: 3 }}>Когда: <b>{detail.pending_call_suggestion.when_human}</b>{detail.pending_call_suggestion.medium ? ` · ${detail.pending_call_suggestion.medium}` : ''}</div>
              {detail.pending_call_suggestion.reason && <div className="faint" style={{ fontSize: 11.5 }}>{detail.pending_call_suggestion.reason}</div>}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn sm primary" disabled={busy} onClick={async () => {
                setBusy(true)
                try { await api.post(`/conversations/${convId}/call-suggestion`, { action: 'confirm', at: detail.pending_call_suggestion?.at, medium: detail.pending_call_suggestion?.medium }); showToast('📅 Событие создано в календаре'); await loadDetail() }
                catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
                finally { setBusy(false) }
              }}>✅ Создать событие</button>
              <button className="btn sm ghost" disabled={busy} onClick={async () => {
                setBusy(true)
                try { await api.post(`/conversations/${convId}/call-suggestion`, { action: 'dismiss' }); showToast('Предложение отклонено'); await loadDetail() }
                catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
                finally { setBusy(false) }
              }}>🚫 Нет</button>
            </div>
          </div>
        )}

        {detail?.wa_autonomous ? (
          <div style={{ borderTop: '1px solid var(--border)', background: 'var(--panel-2)', padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5 }}>
            <span>🤖 Бот ведёт этот диалог сам</span>
            <div style={{ flex: 1 }} />
            <button className="btn sm ghost" onClick={() => setWaAutonomous(false)} disabled={busy}>Вернуть на одобрение</button>
          </div>
        ) : detail && me?.role === 'viewer' ? (
          <div style={{ borderTop: '1px solid var(--border)', padding: '10px 14px', textAlign: 'center' }}
               className="faint">👁 Режим наблюдателя — только просмотр. Ответы и действия недоступны.</div>
        ) : detail && (
          /* ЕДИНОЕ поле ответа: предложка системы сразу в поле — измените, очистите
             или напишите своё, затем «Отправить». Второго поля ввода нет. */
          <div style={{ borderTop: '1px solid var(--accent-border)', background: detail.pending_wa_draft ? 'var(--accent-soft)' : 'var(--panel-2)', padding: '8px 14px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <b style={{ fontSize: 12.5 }}>✍️ Ответ лиду</b>
              {detail.pending_wa_draft
                ? (detail.pending_wa_draft.stale
                    ? <span className="faint" style={{ fontSize: 11, color: 'var(--warn, #c90)' }}>был ответ вручную — нажмите 🔄 Переформулировать для свежего</span>
                    : <span className="faint" style={{ fontSize: 11 }}>🤖 бот предложил — измените или напишите своё</span>)
                : <span className="faint" style={{ fontSize: 11 }}>напишите ответ или нажмите 🔄 Переформулировать</span>}
            </div>
            <textarea value={draftText} onChange={e => setDraftText(e.target.value)}
                      placeholder="Напишите ответ лиду… (Ctrl+Enter — отправить)"
                      onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) sendReply() }}
                      style={{ width: '100%', minHeight: 60, fontSize: 13 }} />
            <div style={{ display: 'flex', gap: 8, marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <button className="btn sm primary" onClick={sendReply} disabled={busy || !draftText.trim()}>✅ Отправить</button>
              {detail.channel === 'whatsapp' && <button className="btn sm" onClick={suggestReply} disabled={busy} title="Перечитать всю переписку и предложить свежий ответ с учётом контекста">🔄 Переформулировать</button>}
              <div style={{ flex: 1 }} />
              {detail.channel === 'whatsapp' && <button className="btn sm" onClick={() => setWaAutonomous(true)} disabled={busy} title="Дальше бот сам ведёт этот диалог и отвечает без вашего одобрения">🤖 Передать боту</button>}
            </div>
            <span className="faint" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>
              {detail.channel === 'website' ? 'Website: лид увидит при следующем визите' : 'Уйдёт лиду в его канал'}
            </span>
          </div>
        )}

        {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
      </div>
    </>
  )
}
