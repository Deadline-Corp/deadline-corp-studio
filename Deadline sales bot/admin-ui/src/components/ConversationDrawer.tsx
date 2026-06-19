import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { ConvDetail, Msg } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useStages, useStageLabel, useMe, useOverview } from '../overviewContext'
import { CHANNEL_META, LOST_REASONS, TEMP_META, fmtTime, initials, emitLeadDismissed } from '../lib'
import { dismissLead } from '../api/leads'
import { Help } from './Help'

/* Карточка лида: переписка + ответ + takeover + стадия + пинок + задача.
   Один и тот же компонент из Inbox, Канбана и Канваса. Стадии — динамические
   (кастомная воронка из overview). */

// Иконки/подписи для журнала решений бота (что бот решил и почему).
export const DECISION_META: Record<string, { icon: string; label: string }> = {
  stage_change: { icon: '📊', label: 'смена стадии' },
  call_suggested: { icon: '📅', label: 'предложил созвон' },
  call_booked: { icon: '📞', label: 'забронировал созвон' },
  call_rescheduled: { icon: '🔁', label: 'перенёс созвон' },
  call_cancelled: { icon: '✖️', label: 'отменил созвон' },
  nudge_sent: { icon: '👋', label: 'дожал молчуна' },
  winback_task: { icon: '♻️', label: 'возврат проигранного' },
  handoff: { icon: '🤝', label: 'передал оператору' },
  classification: { icon: '🏷', label: 'классификация' },
  recall_greeting: { icon: '🔔', label: 'узнал вернувшегося' },
  field_filled: { icon: '📇', label: 'заполнил поля' },
  alt_channel: { icon: '↪️', label: 'другой канал' },
  silence_lost: { icon: '💤', label: 'молчание → проигран' },
  reply_sent: { icon: '✍️', label: 'ответил' },
}

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
  const [dealInput, setDealInput] = useState('')  // ввод суммы сделки (revenue-аналитика)
  const [advice, setAdvice] = useState('')
  const [team, setTeam] = useState<any[]>([])
  const [draftText, setDraftText] = useState('')  // редактируемый предложенный ботом ответ (WhatsApp)
  const [draftOpen, setDraftOpen] = useState(true)  // свернуть блок «система предлагает», чтобы видеть переписку
  const [replyOpen, setReplyOpen] = useState(false) // окно ручного ответа оператора — по умолчанию свёрнуто
  const [callOpen, setCallOpen] = useState(false) // инлайн-панель назначения/переноса созвона
  const [moreOpen, setMoreOpen] = useState(false) // «⋯» — редкие действия (пинок / пауза дожима / регулярный / HubSpot)
  const [historyOpen, setHistoryOpen] = useState(false) // «почему лид на этой стадии» — история переходов
  const [decisionsOpen, setDecisionsOpen] = useState(false) // «🤖 Решения бота» — журнал решений с причинами
  const msgsRef = useRef<HTMLDivElement>(null)
  const lastTsRef = useRef<string | null>(null)
  const lastIdRef = useRef<string | null>(null)  // keyset-курсор вниз (новые): вторичный ключ по id
  const firstTsRef = useRef<string | null>(null)  // keyset-курсор вверх (старые): для «показать ранее»
  const firstIdRef = useRef<string | null>(null)
  const [hasMore, setHasMore] = useState(true)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const resyncedRef = useRef<string | null>(null)  // фон-сверка с WhatsApp один раз на открытие
  const [resyncing, setResyncing] = useState(false)

  const stages = useStages()
  const stageLabel = useStageLabel()
  const me = useMe()
  const ov = useOverview()
  const hiddenActions = ov?.hidden_actions || []  // действия, скрытые под нишу
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

  // Сверка с WhatsApp (источник правды): подтянуть пропущенные живым вебхуком сообщения,
  // чтобы окно панели = окно WhatsApp. Авто в фоне при открытии + кнопка-форс.
  const resyncWa = async (silent = false) => {
    if (resyncing) return
    setResyncing(true)
    try {
      const r = await api.post<{ added: number; restamped?: number; deduped?: number; removed?: number; restored?: number; reason?: string }>(`/conversations/${convId}/wa-resync`, {})
      const restamped = r.restamped ?? 0
      const deduped = r.deduped ?? 0
      const removed = r.removed ?? 0
      const restored = r.restored ?? 0
      if (r.added > 0 || restamped > 0 || deduped > 0 || removed > 0 || restored > 0) {
        await loadMessages(true)  // перезагрузить — подтянулись недостающие / порядок / дубли / скрыты удалённые
        if (!silent) {
          const bits: string[] = []
          if (r.added > 0) bits.push(`подтянуто ${r.added}`)
          if (restamped > 0) bits.push(`выровнен порядок ${restamped}`)
          if (deduped > 0) bits.push(`убрано дублей ${deduped}`)
          if (removed > 0) bits.push(`скрыто удалённых ${removed}`)
          if (restored > 0) bits.push(`восстановлено ${restored}`)
          showToast(`🔄 Сверено с WhatsApp: ${bits.join(', ')}`)
        }
      } else if (!silent) {
        showToast(r.reason ? `WhatsApp: ${r.reason}` : '✅ Уже совпадает с WhatsApp')
      }
    } catch (e: any) { if (!silent) showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setResyncing(false) }
  }

  // Ручной ввод номера для рекламной @lid-карточки, где WhatsApp прячет номер,
  // а владелец видит его в приложении. Проставляет phone + сливает дубль @lid/@c.us.
  const setPhoneManual = async () => {
    const cur = window.prompt('Впишите реальный номер (как в WhatsApp), напр. +374 93 096577:')
    if (!cur || !cur.trim()) return
    try {
      const r = await api.post<{ phone: string }>(`/conversations/${convId}/set-phone`, { phone: cur.trim() })
      showToast(`✅ Номер сохранён: ${r.phone}`)
      await loadDetail()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
  }

  // Перепроверить скрытый @lid-номер через WAHA (вдруг WhatsApp уже раскрыл).
  // silent=true — авто-попытка при открытии карточки (без тостов).
  const recheckPhone = async (silent = false) => {
    try {
      const r = await api.post<{ resolved: boolean; phone?: string }>(`/conversations/${convId}/resolve-phone`, {})
      if (r.resolved && r.phone) {
        if (!silent) showToast(`✅ Номер определился: ${r.phone}`)
        await loadDetail()
      } else if (!silent) {
        showToast('WhatsApp пока прячет номер — впишите вручную или подождите, пока лид напишет ещё раз')
      }
    } catch (e: any) { if (!silent) showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
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
  // Фоновая сверка с WhatsApp при открытии WhatsApp-карточки (один раз на открытие):
  // окно панели сразу подтягивает реальные сообщения чата (пропущенные вебхуком).
  useEffect(() => {
    if (detail?.channel === 'whatsapp' && resyncedRef.current !== convId) {
      resyncedRef.current = convId
      void resyncWa(true)
      // Скрытый @lid-номер — тихо перепроверяем через WAHA (вдруг WhatsApp уже раскрыл,
      // напр. лид написал ещё раз). Резолвнётся — номер появится сам, без ручного ввода.
      if (detail?.wa_hidden_phone && !detail?.customer.phone) void recheckPhone(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.channel, convId])

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
      showToast(r.delivered ? '✅ Отправлено клиенту в WhatsApp' : '⚠️ НЕ доставлено лиду (см. логи) — черновик сохранён, попробуйте ещё раз', !r.delivered)
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
      if (!delivered) {
        // НЕ доставлено — оставляем текст в поле для повторной попытки (без дубля),
        // НЕ делаем вид, что отправлено.
        showToast('⚠️ НЕ доставлено лиду (см. логи) — текст сохранён, попробуйте ещё раз', true)
      } else {
        if (channel === 'website') showToast('Сохранено. Website-лид увидит при следующем визите.')
        else showToast('✅ Доставлено лиду')
        setDraftText('')
      }
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

  // Быстро «убрать» лид → в архив (не сложилось / спам). Карточка закрывается,
  // списки обновляются сами, глобальный тост «Вернуть». Обратимо (never-delete).
  const dismissLeadAction = async () => {
    if (busy || !detail) return
    setBusy(true)
    try {
      await dismissLead(convId)
      emitLeadDismissed({
        id: convId, stage: detail.lead_stage,
        label: detail.customer.display_name || detail.customer.name || 'Лид',
      })
      onClose()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true); setBusy(false) }
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

  // Бэкфилл полей по истории (для старых карточек, где авто-заполнение ещё не сработало).
  const extractFields = async () => {
    if (busy) return
    setBusy(true)
    try {
      const r = await api.post<{ filled: boolean; reason?: string }>(`/conversations/${convId}/extract-fields`, {})
      showToast(r.filled ? '✅ Поля заполнены по переписке' : `Нечего заполнять${r.reason ? ` (${r.reason})` : ' — в переписке нет явных данных'}`)
      if (r.filled) await loadDetail()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  // Пауза/возобновление авто-дожима для этого лида (ручной стоп/продолжить).
  const toggleNudgePause = async () => {
    if (busy) return
    setBusy(true)
    try {
      const next = !detail?.nudge_paused
      await api.post(`/conversations/${convId}/nudge-pause`, { paused: next })
      showToast(next ? '⏸ Дожим на паузе для этого лида' : '▶️ Дожим возобновлён')
      await loadDetail()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  // Сумма сделки (revenue-аналитика «сколько денег принёс бот»). Пусто → очистить.
  const saveDeal = async () => {
    if (busy) return
    const raw = dealInput.replace(/[^\d.,]/g, '').replace(',', '.')
    const val = raw ? parseFloat(raw) : 0
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/deal-value`, {
        value: val, currency: detail?.deal_currency || 'RUB',
      })
      showToast(val > 0 ? `💰 Сумма сделки: ${val}` : 'Сумма очищена')
      setDealInput('')
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

  // Сумму сделки в карточке показываем только в расширенном режиме (тот же флаг,
  // что и «расширенные настройки») — чтобы не громоздить интерфейс по умолчанию.
  const advMode = localStorage.getItem('deadline_adv_settings') === '1'
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
                {/* Телефон-чип показываем ТОЛЬКО если его ещё нет в заголовке (иначе дубль). */}
                {(() => {
                  const t = (detail.customer.display_name || detail.customer.name || '')
                  const pd = (detail.customer.phone || '').replace(/\D/g, '')
                  const inTitle = pd.length >= 6 && t.replace(/\D/g, '').includes(pd)
                  return detail.customer.phone && !inTitle
                    ? <span className="chip mono">{detail.customer.phone}</span> : null
                })()}
                {!detail.customer.phone && detail.wa_hidden_phone && (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <span className="chip mono dim" onClick={me?.role === 'viewer' ? undefined : setPhoneManual}
                          style={me?.role === 'viewer' ? undefined : { cursor: 'pointer' }}
                          title={me?.role === 'viewer' ? undefined : 'Нажмите, чтобы вписать номер вручную (как видите в WhatsApp)'}>
                      📵 номер скрыт{me?.role === 'viewer' ? ' (реклама)' : ' — указать'}
                    </span>
                    <Help title="Почему номер скрыт" text="WhatsApp намеренно прячет номер рекламных лидов (пришедших по объявлению «Написать в WhatsApp») от внешнего доступа — ради приватности. В самом приложении WhatsApp вы номер видите, а боту он приходит скрытым: это ограничение WhatsApp, НЕ сбой системы. Система перепроверяет номер автоматически — при открытии карточки и периодически в фоне; часто он раскрывается, когда лид пишет ещё раз. Видите номер в WhatsApp — нажмите чип «указать» и впишите вручную: карточка сразу свяжется по номеру и склеит дубли." />
                    {me?.role !== 'viewer' && (
                      <button className="btn sm ghost" style={{ fontSize: 11, padding: '1px 6px' }}
                              title="Перепроверить номер через WhatsApp сейчас" onClick={() => recheckPhone(false)}>🔄</button>
                    )}
                  </span>
                )}
                <div style={{ flex: 1 }} />
                {/* «Из WhatsApp» — наверху (а не в Действиях): выровнять карточку под реальный чат. */}
                {detail.channel === 'whatsapp' && me?.role !== 'viewer' && (
                  <button className="btn sm ghost" onClick={() => resyncWa(false)} disabled={resyncing}
                          title="Подтянуть сообщения прямо из WhatsApp и выровнять карточку под реальный чат — порядок и пропуски (WhatsApp = источник правды)">
                    {resyncing ? <span className="spin" /> : '🔄 Из WhatsApp'}
                  </button>
                )}
                {me?.role === 'viewer' && (
                  <span className="chip" title="Роль «наблюдатель» — только просмотр, без изменений">👁 только просмотр</span>
                )}
              </div>
              {me?.role !== 'viewer' && (
                <div className="d-bar">
                  <select className="ab" value={stagePick} onChange={e => setStagePick(e.target.value)} title="Сменить стадию сделки">
                    <option value="">📊 Стадия…</option>
                    {stages.filter(s => s.stage !== detail.lead_stage).map(s => (
                      <option key={s.stage} value={s.stage}>{s.label}</option>
                    ))}
                  </select>
                  {stagePick && isLostStage(stagePick) && (
                    <select className="ab" value={lostReason} onChange={e => setLostReason(e.target.value)}>
                      {LOST_REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  )}
                  {stagePick && <button className="ab" onClick={applyStage} disabled={busy}>OK</button>}
                  <button className="ab" onClick={() => { setTaskOpen(v => !v); setNudgeOpen(false) }}
                          title="Поставить напоминание / задачу по лиду">📋 Задача</button>
                  {!hiddenActions.includes('call_schedule') && (
                    <button className="ab" onClick={() => setCallOpen(v => !v)}
                            title="Назначить или перенести созвон">📞 Созвон{detail.booked_call_at ? ' ✓' : ''}</button>
                  )}
                  {!hiddenActions.includes('assign') && team.filter((m: any) => m.active).length > 0 && (
                    <select className="ab" value="" disabled={busy} title="Передать лид ответственному менеджеру"
                            onChange={async e => {
                              const v = e.target.value
                              if (!v) return
                              const mid = v === '__unassign__' ? null : v
                              setBusy(true)
                              try {
                                const r = await api.post<{ assigned: any }>(`/conversations/${convId}/assign`, { member_id: mid })
                                showToast(r.assigned ? `👥 Ответственный: ${r.assigned.name}` : 'Назначение снято')
                              } catch (er: any) { showToast(`Ошибка: ${er.detail ?? er.message}`, true) }
                              finally { setBusy(false) }
                            }}>
                      <option value="">👥 Ответственный…</option>
                      {team.filter((m: any) => m.active).map((m: any) => (
                        <option key={m.id} value={m.id}>{m.name}{m.department ? ` · ${m.department}` : ''}</option>
                      ))}
                      <option value="__unassign__">— снять —</option>
                    </select>
                  )}
                  <button className="ab" onClick={advise} disabled={busy}
                          title="AI-совет: что делать дальше + черновик ответа в поле. Сам ничего не отправляет.">🧭 Совет</button>
                  <div style={{ flex: 1 }} />
                  <button className="ab danger" onClick={dismissLeadAction} disabled={busy}
                          title="Убрать лид в архив (не сложилось / спам). Обратимо — «Вернуть».">🗑 Убрать</button>
                  <button className="ab" onClick={() => setMoreOpen(v => !v)}
                          title="Ещё: пинок, пауза дожима, регулярный, HubSpot">⋯</button>
                </div>
              )}
              {callOpen && me?.role !== 'viewer' && !hiddenActions.includes('call_schedule') && (
                <div className="d-panel">
                  {detail.booked_call_at && <span className="chip accent">📞 {fmtTime(detail.booked_call_at)}</span>}
                  <input type="datetime-local" value={callDt} onChange={e => setCallDt(e.target.value)} style={{ fontSize: 12, padding: '3px 6px' }} title="Дата и время созвона" />
                  <button className="btn sm" onClick={() => setCall('reschedule')} disabled={busy}>📞 {detail.booked_call_at ? 'Перенести' : 'Назначить'}</button>
                  {detail.booked_call_at && <button className="btn sm ghost" onClick={() => setCall('cancel')} disabled={busy}>Отменить</button>}
                </div>
              )}
              {moreOpen && me?.role !== 'viewer' && (
                <div className="d-panel">
                  <button className="btn sm" onClick={() => { setNudgeOpen(v => !v); setTaskOpen(false); setMoreOpen(false) }}
                          title="Дожать молчащего лида — напоминание от имени бота">⚡ Пинок</button>
                  <button className="btn sm" onClick={toggleNudgePause} disabled={busy}
                          title="Авто-дожим on/off для этого лида">
                    {detail.nudge_paused ? '▶️ Возобновить дожим' : '⏸ Пауза дожима'}
                  </button>
                  {!hiddenActions.includes('recurring') && (
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
                  )}
                  {detail.hubspot.contact_url && (
                    <a className="btn sm ghost" href={detail.hubspot.contact_url} target="_blank" rel="noreferrer">HubSpot ↗</a>
                  )}
                </div>
              )}
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
              {me?.role !== 'viewer' && advMode && (
                <div style={{ background: 'var(--panel)', borderRadius: 8, padding: '8px 10px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 12.5 }}>💰 Сумма сделки</b>
                  {detail.deal_value != null && (
                    <span className="chip ok">{detail.deal_value} {detail.deal_currency || ''}</span>
                  )}
                  <div style={{ flex: 1, minWidth: 8 }} />
                  <input value={dealInput} onChange={e => setDealInput(e.target.value)}
                         onKeyDown={e => { if (e.key === 'Enter') saveDeal() }}
                         placeholder={detail.deal_value != null ? 'изменить…' : 'напр. 50000'}
                         style={{ width: 110, fontSize: 12, padding: '4px 8px' }} />
                  <button className="btn sm primary" onClick={saveDeal}
                          disabled={busy || (!dealInput.trim() && detail.deal_value == null)}
                          title="Сумма закрытой/потенциальной сделки — для отчёта «сколько денег принёс бот»">💾</button>
                </div>
              )}
              {!hiddenActions.includes('fields') && detail.fields && detail.fields.length > 0 && (
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
                      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 2 }}>
                        {me?.role !== 'viewer' && (
                          <button className="btn sm ghost" onClick={extractFields} disabled={busy}
                                  title="Заполнить поля автоматически по истории переписки (бот прочитает диалог и подставит явно названное)">
                            🪄 Заполнить по переписке
                          </button>
                        )}
                        {Object.keys(fieldEdits).length > 0 && (
                          <button className="btn sm primary" onClick={saveFields} disabled={busy}>💾 Сохранить поля</button>
                        )}
                      </div>
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

        {detail && (detail.bot_decisions?.length ?? 0) > 0 && (
          <div style={{ padding: '0 14px 8px' }}>
            <button className="btn sm ghost" onClick={() => setDecisionsOpen(v => !v)}
                    title="Что бот решил и ПОЧЕМУ — прозрачная логика, можно подстроить правила в «Мозге»">
              {decisionsOpen ? '▾' : '▸'} 🤖 Решения бота ({detail.bot_decisions!.length})
            </button>
            {decisionsOpen && (
              <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 7,
                            fontSize: 12 }}>
                {detail.bot_decisions!.map(d => {
                  const m = DECISION_META[d.decision_type] || { icon: '•', label: d.decision_type }
                  return (
                    <div key={d.id} style={{ display: 'flex', gap: 7, alignItems: 'baseline' }}>
                      <span className="faint" style={{ minWidth: 92, flexShrink: 0 }}>{d.at ? fmtTime(d.at) : ''}</span>
                      <span title={m.label} style={{ flexShrink: 0 }}>{m.icon}</span>
                      <span style={{ color: 'var(--text)' }}>{d.reason}</span>
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
          {/* Маркер начала: видно, что переписка полная (выше ничего не обрезано). */}
          {!hasMore && msgs.length > 0 && (
            <div className="faint" style={{ alignSelf: 'center', fontSize: 10.5, opacity: 0.6,
                                            marginBottom: 10, textAlign: 'center', letterSpacing: 0.2 }}>
              ⌃ начало переписки · {msgs.length} сообщ. · ✓ как в WhatsApp
            </div>
          )}
          {msgs.length === 0 && <div className="empty">Сообщений пока нет</div>}
          {msgs.map(m => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.extra_meta?.wa_deleted === true
                ? <span style={{ opacity: 0.55, fontStyle: 'italic', textDecoration: 'line-through' }}>{m.content}</span>
                : m.content}
              <div className="m-meta">
                {m.extra_meta?.wa_deleted === true && <span style={{ color: 'var(--warn, #c90)' }}>🚫 удалено в WhatsApp · </span>}
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
          <CallSuggestionBlock
            convId={convId}
            suggestion={detail.pending_call_suggestion}
            showToast={showToast}
            reload={loadDetail}
          />
        )}

        {/* «План бота» — ТОЛЬКО когда диалог ПОЛНОСТЬЮ ведёт бот (автопилот wa_autonomous).
            У ручных лидов (даже если бот подготовил черновик на одобрение) и при
            operator_takeover блок скрыт — план/дожим относятся к автоведению бота,
            а не к ручной работе (черновик у ручных одобряется в «✍ Ответ лиду» ниже). */}
        {detail && me?.role !== 'viewer'
          && detail.wa_autonomous
          && !detail.operator_takeover && (
          <BotPlanBlock detail={detail} convId={convId} showToast={showToast} />
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
              <b style={{ fontSize: 12.5 }}>{detail.pending_wa_draft?.kind === 'nudge' ? '✍️ Дожать молчащего лида' : '✍️ Ответ лиду'}</b>
              {detail.pending_wa_draft
                ? (detail.pending_wa_draft.stale
                    ? <span className="faint" style={{ fontSize: 11, color: 'var(--warn, #c90)' }}>был ответ вручную — нажмите 🔄 Переформулировать для свежего</span>
                    : <span className="faint" style={{ fontSize: 11 }}>{detail.pending_wa_draft.kind === 'nudge' ? '🤖 бот предлагает дожать молчащего — проверьте и отправьте' : '🤖 бот предложил — измените или напишите своё'}</span>)
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
              {detail.channel !== 'whatsapp' && (
                <button className="btn sm" onClick={toggleTakeover} disabled={busy}
                        title="Кто ведёт диалог: «Взять на себя» — бот молчит, отвечаете вы; «Вернуть боту» — бот продолжит сам.">
                  {detail.operator_takeover ? '🤖 Вернуть боту' : '👤 Взять на себя'}
                </button>
              )}
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

// Блок «договорились о созвоне»: дуальное время + ПРАВКА времени/канала перед
// подтверждением (просьба владельца — «человек мог подправить вручную и подтвердить»).
function CallSuggestionBlock({ convId, suggestion, showToast, reload }: {
  convId: string
  suggestion: { at?: string; when_human?: string; medium?: string | null; reason?: string }
  showToast: (t: string, err?: boolean) => void
  reload: () => Promise<void> | void
}) {
  const [busy, setBusy] = useState(false)
  const [edit, setEdit] = useState(false)
  const toLocal = (iso?: string) => {
    if (!iso) return ''
    const d = new Date(iso)
    const p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
  }
  const [at, setAt] = useState(toLocal(suggestion.at))
  const [medium, setMedium] = useState(suggestion.medium || '')

  const confirm = async () => {
    setBusy(true)
    try {
      const iso = edit && at ? new Date(at).toISOString() : suggestion.at
      await api.post(`/conversations/${convId}/call-suggestion`, { action: 'confirm', at: iso, medium: medium || null })
      showToast('📅 Событие создано в календаре')
      await reload()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }
  const dismiss = async () => {
    setBusy(true)
    try {
      await api.post(`/conversations/${convId}/call-suggestion`, { action: 'dismiss' })
      showToast('Предложение отклонено')
      await reload()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  return (
    <div style={{ borderTop: '1px solid var(--accent-border)', background: 'var(--accent-soft)', padding: '10px 14px' }}>
      <div style={{ fontSize: 13, marginBottom: 6 }}>
        <b>📅 Похоже, договорились о созвоне</b>
        <div style={{ marginTop: 3 }}>Когда: <b>{suggestion.when_human}</b>{suggestion.medium ? ` · ${suggestion.medium}` : ''}</div>
        {suggestion.reason && <div className="faint" style={{ fontSize: 11.5 }}>{suggestion.reason}</div>}
      </div>
      {edit && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
          <input type="datetime-local" value={at} onChange={e => setAt(e.target.value)}
                 style={{ fontSize: 12, padding: '3px 6px' }} />
          <select value={medium} onChange={e => setMedium(e.target.value)} style={{ fontSize: 12, padding: '3px 6px' }}>
            <option value="">Канал…</option>
            <option value="WhatsApp">WhatsApp</option>
            <option value="Телефон">Телефон</option>
            <option value="Zoom">Zoom</option>
            <option value="Google Meet">Google Meet</option>
          </select>
          <span className="faint" style={{ fontSize: 11 }}>время — в вашем поясе (браузера)</span>
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn sm primary" disabled={busy} onClick={confirm}>✅ Создать событие</button>
        <button className="btn sm ghost" disabled={busy} onClick={() => setEdit(v => !v)}>
          {edit ? '↩ Как есть' : '✏️ Изменить время'}
        </button>
        <button className="btn sm ghost" disabled={busy} onClick={dismiss}>🚫 Нет</button>
      </div>
    </div>
  )
}

// Блок «🤖 План бота»: что бот понял про лида + следующий запланированный шаг +
// развернуть «что напишет дальше» (прозрачность — видно, что у бота есть план, и
// можно подстроить). Просьба владельца: не просто «бот ведёт», а ЧТО он планирует.
function BotPlanBlock({ detail, convId, showToast }: {
  detail: ConvDetail
  convId: string
  showToast: (t: string, err?: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const MODE: Record<string, string> = {
    bot_auto: 'веду диалог сам', reengage: 'дожимаю молчуна', wait: 'жду ответа лида',
    needs_approval: 'жду твоего одобрения', human: 'передал тебе', unclear: 'не понял — нужна помощь',
  }
  const TYPE: Record<string, string> = {
    followup_message: '🔁 дожим молчуну', call_reminder: '🔔 напоминание о созвоне', call_booked: '📞 созвон',
  }
  const na = detail.next_action
  const acts = (detail.scheduled_actions || []).filter(a => !!a.due_at)
    .sort((a, b) => (String(a.due_at) < String(b.due_at) ? -1 : 1))
  const nextSched = acts.find(a => TYPE[a.action_type])
  const statusTxt = na?.mode ? (MODE[na.mode] || na.label || '—')
    : (detail.wa_autonomous ? 'веду диалог сам' : (na?.label || 'веду по логике'))

  const showPreview = async () => {
    if (preview !== null) { setOpen(o => !o); return }
    setBusy(true); setOpen(true)
    try {
      const r = await api.post<{ reply: string | null }>(`/conversations/${convId}/next-reply-preview`, {})
      setPreview(r.reply || '(бот пока не сформулировал следующий ответ — мало контекста)')
    } catch (e: any) { setPreview(null); setOpen(false); showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  return (
    <div style={{ borderTop: '1px solid var(--border)', background: 'var(--panel-2)', padding: '8px 14px', fontSize: 12.5 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b>🤖 План бота</b>
        <span className="chip" style={{ fontSize: 10.5 }}>{statusTxt}</span>
        {detail.nudge_paused && <span className="chip warn" style={{ fontSize: 10.5 }}>⏸ дожим на паузе</span>}
      </div>
      <div className="faint" style={{ fontSize: 11.5, marginTop: 3 }}>
        {nextSched
          ? <>Следующий шаг: <b>{TYPE[nextSched.action_type]}</b>{nextSched.due_at ? <> · {fmtTime(nextSched.due_at)}</> : null}</>
          : detail.nudge_paused
            ? 'Дожим на паузе — бот не пишет сам, пока не возобновишь.'
            : detail.projected_next_followup?.due_at
                ? <>Следующий дожим <span style={{ opacity: .7 }}>(планово)</span>: <b>{fmtTime(detail.projected_next_followup.due_at)}</b>{detail.projected_next_followup.of && detail.projected_next_followup.of > 1 ? ` · шаг ${detail.projected_next_followup.step}/${detail.projected_next_followup.of}` : ''}</>
                : 'Жду ответа лида. Если замолчит — сам напишу дожим (дата появится здесь).'}
      </div>
      {(nextSched?.payload?.text || detail.projected_next_followup?.text) && (
        <div style={{ marginTop: 5, padding: '6px 9px', background: 'var(--panel)', borderRadius: 7,
                      fontSize: 12, lineHeight: 1.45, whiteSpace: 'pre-wrap', borderLeft: '2px solid var(--accent-border)' }}>
          💬 «{nextSched?.payload?.text || detail.projected_next_followup?.text}»{!nextSched && <span style={{ opacity: .6 }}> (планово)</span>}
        </div>
      )}
      <button className="btn sm ghost" style={{ marginTop: 6, fontSize: 11.5 }} onClick={showPreview} disabled={busy}>
        {busy ? <span className="spin" /> : (open && preview !== null ? '▾ Что бот напишет дальше' : '▸ Что бот напишет дальше')}
      </button>
      {open && preview !== null && (
        <div style={{ marginTop: 6, padding: '8px 10px', background: 'var(--panel)', borderRadius: 8,
                      fontSize: 12.5, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
          {preview}
        </div>
      )}
    </div>
  )
}
