import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { PromptVersionItem } from '../api/types'
import { fmtTime } from '../lib'
import { HintBar } from '../components/HintBar'

/* «Мозг»: лёгкий режим — правила одной строкой («когда X — отвечай Y»),
   бот применяет их через retrieval как уроки. Продвинутое (полный системный
   промпт с версиями) спрятано в раскрывашку, чтобы не мешало глазам. */

export function Brain() {
  const [rules, setRules] = useState<any[]>([])
  const [newRule, setNewRule] = useState('')
  const [newResponse, setNewResponse] = useState('')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  // KB state
  const [kbSources, setKbSources] = useState<{ source: string; chunks: number }[]>([])
  const [kbName, setKbName] = useState('')
  const [kbContent, setKbContent] = useState('')
  const [kbBusy, setKbBusy] = useState(false)

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 4500)
  }

  const loadRules = async () => {
    try {
      const r = await api.get<{ items: any[] }>('/training-rules')
      setRules(r.items)
    } catch { /* ignore */ }
  }

  const loadKb = async () => {
    try {
      const r = await api.get<{ sources: { source: string; chunks: number }[] }>('/kb')
      setKbSources(r.sources)
    } catch { /* ignore */ }
  }

  useEffect(() => { void loadRules(); void loadKb() }, [])

  const addKb = async () => {
    const name = kbName.trim()
    const content = kbContent.trim()
    if (!name || content.length < 10 || kbBusy) return
    setKbBusy(true)
    try {
      await api.post('/kb/upload', { source: name, content })
      showToast('✅ Документ добавлен в базу знаний')
      setKbName('')
      setKbContent('')
      await loadKb()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setKbBusy(false) }
  }

  const deleteKb = async (source: string) => {
    if (!confirm(`Удалить «${source}» из базы знаний?`)) return
    setKbBusy(true)
    try {
      await api.del(`/kb/${encodeURIComponent(source)}`)
      showToast('Удалено из базы знаний')
      await loadKb()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setKbBusy(false) }
  }

  const addRule = async () => {
    const rule = newRule.trim()
    if (rule.length < 10 || busy) return
    setBusy(true)
    try {
      await api.post('/training-rules/quick', {
        rule,
        suggested_response: newResponse.trim() || undefined,
      })
      showToast('✅ Правило добавлено — бот начнёт применять сразу')
      setNewRule('')
      setNewResponse('')
      await loadRules()
    } catch (e: any) { showToast(`Ошибка: ${e.detail ?? e.message}`, true) }
    finally { setBusy(false) }
  }

  const deactivate = async (id: string) => {
    setBusy(true)
    try {
      await api.post(`/training-rules/${id}/deactivate`)
      showToast('Правило выключено')
      await loadRules()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Мозг бота</h1>
        <span className="sub">правила применяются без деплоя</span>
      </div>

      <HintBar id="brain" icon="🧠">
        Здесь настраивается, <b>как бот разговаривает</b>. Пишите правила по-человечески:
        «Когда спрашивают про цену — называй вилку и зови на созвон» — бот начнёт применять
        сразу, без программиста. Полный «характер» бота — в «Продвинутом» (трогайте осторожно).
      </HintBar>

      {/* ---- База знаний ---- */}
      <div className="card" style={{ marginBottom: 14 }}>
        <b>📚 База знаний</b>
        <p className="muted" style={{ margin: '4px 0 10px', fontSize: 12.5 }}>
          Документы, которые бот цитирует в ответах: прайс, FAQ, условия работы, кейсы.
          Добавьте текст — бот будет находить нужный фрагмент и включать в ответ.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input
            placeholder="Название источника (напр. «Прайс 2025»)"
            value={kbName}
            onChange={e => setKbName(e.target.value)}
          />
          <textarea
            placeholder="Содержимое документа (текст, FAQ, условия…)"
            value={kbContent}
            onChange={e => setKbContent(e.target.value)}
            style={{ minHeight: 80 }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button className="btn primary" onClick={addKb}
                    disabled={kbBusy || !kbName.trim() || kbContent.trim().length < 10}>
              {kbBusy ? <span className="spin" /> : 'Добавить'}
            </button>
          </div>
        </div>
        {kbSources.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 12 }}>
            <b style={{ fontSize: 12.5 }}>Загруженные источники</b>
            {kbSources.map(s => (
              <div key={s.source} className="version-item"
                   style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: 13 }}>{s.source}</span>
                  <span className="faint" style={{ fontSize: 11, marginLeft: 8 }}>{s.chunks} чанков</span>
                </div>
                <button className="btn sm ghost" disabled={kbBusy}
                        onClick={() => deleteKb(s.source)} title="Удалить источник">🗑</button>
              </div>
            ))}
          </div>
        )}
        {kbSources.length === 0 && (
          <div className="faint" style={{ fontSize: 12.5, marginTop: 10 }}>База знаний пуста — добавьте первый документ выше</div>
        )}
      </div>

      {/* ---- Лёгкий режим: быстрые правила ---- */}
      <div className="card" style={{ marginBottom: 14 }}>
        <b>⚡ Быстрые правила</b>
        <p className="muted" style={{ margin: '4px 0 10px', fontSize: 12.5 }}>
          Короткие подсказки «Когда X — делай Y». Бот подхватывает их в похожих ситуациях.
          Используйте для повседневной правки поведения — добавляйте в любой момент без деплоя.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <textarea
            placeholder="Когда [ситуация] — [что делать / как отвечать]…"
            value={newRule}
            onChange={e => setNewRule(e.target.value)}
            style={{ minHeight: 60 }}
          />
          <input
            placeholder="Пример готового ответа (необязательно)"
            value={newResponse}
            onChange={e => setNewResponse(e.target.value)}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button className="btn primary" onClick={addRule} disabled={busy || newRule.trim().length < 10}>
              {busy ? <span className="spin" /> : 'Добавить правило'}
            </button>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <b>📜 Активные правила ({rules.length})</b>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
          {rules.length === 0 && <span className="faint" style={{ fontSize: 12.5 }}>Правил пока нет — добавьте первое выше</span>}
          {rules.map(r => (
            <div key={r.id} className="version-item" style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13 }}>{r.guidance}</div>
                {r.suggested_response && (
                  <div className="muted" style={{ fontSize: 12, marginTop: 3 }}>💬 «{r.suggested_response}»</div>
                )}
                <div className="faint" style={{ fontSize: 11, marginTop: 3 }}>
                  {r.channel ? `канал: ${r.channel} · ` : ''}{fmtTime(r.created_at)} · {r.created_by}
                </div>
              </div>
              <button className="btn sm ghost" onClick={() => deactivate(r.id)} disabled={busy} title="Выключить правило">✕</button>
            </div>
          ))}
        </div>
      </div>

      {/* ---- Продвинутое: полный системный промпт ---- */}
      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}
             onClick={() => setAdvancedOpen(v => !v)}>
          <b>⚙️ Системный промпт <span className="chip" style={{ fontSize: 11, marginLeft: 6 }}>продвинутое</span></b>
          <span className="faint" style={{ marginLeft: 10, fontSize: 12 }}>
            ядро личности и инструкций бота, версионируется — менять осторожно
          </span>
          <div style={{ flex: 1 }} />
          <span>{advancedOpen ? '▾' : '▸'}</span>
        </div>
        {advancedOpen && <PromptEditor showToast={showToast} />}
      </div>

      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
    </div>
  )
}

/* ---------- Продвинутый редактор промпта (версии/тест/откат) ---------- */

function PromptEditor({ showToast }: { showToast: (t: string, err?: boolean) => void }) {
  const [content, setContent] = useState('')
  const [source, setSource] = useState<'db' | 'file'>('file')
  const [defaultContent, setDefaultContent] = useState('')
  const [versions, setVersions] = useState<PromptVersionItem[]>([])
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [problems, setProblems] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)
  const [previewQ, setPreviewQ] = useState('')
  const [previewReply, setPreviewReply] = useState<string | null>(null)
  const [previewErr, setPreviewErr] = useState<string | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)

  const load = async () => {
    try {
      const p = await api.get<{ source: 'db' | 'file'; content: string; default_content: string }>('/prompt')
      setContent(p.content)
      setSource(p.source)
      setDefaultContent(p.default_content)
      setDirty(false)
      const v = await api.get<{ items: PromptVersionItem[] }>('/prompt/versions')
      setVersions(v.items)
    } catch { /* ignore */ }
  }

  useEffect(() => { void load() }, [])

  const test = async () => {
    setBusy(true)
    setProblems([])
    try {
      const r = await api.post<{ ok: boolean; problems?: string[]; rendered_chars?: number }>(
        '/prompt/test', { content })
      if (r.ok) showToast(`✅ Промпт валиден (${r.rendered_chars} символов после сборки)`)
      else { setProblems(r.problems ?? []); showToast('Есть проблемы — см. список', true) }
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const save = async () => {
    setBusy(true)
    setProblems([])
    try {
      await api.post('/prompt', { content, comment: comment || undefined })
      showToast('✅ Сохранено и активировано — бот подхватит в течение минуты')
      setComment('')
      await load()
    } catch (e: any) {
      const probs = e.detail?.problems
      if (probs) { setProblems(probs); showToast('Не сохранено: промпт сломал бы бота', true) }
      else showToast(`Ошибка: ${e.message}`, true)
    } finally { setBusy(false) }
  }

  const activate = async (versionId: string | null) => {
    setBusy(true)
    try {
      await api.post('/prompt/activate', { version_id: versionId })
      showToast(versionId ? '✅ Версия активирована' : '✅ Откат на заводской промпт')
      await load()
    } catch (e: any) { showToast(`Ошибка: ${e.message}`, true) }
    finally { setBusy(false) }
  }

  const runPreview = async () => {
    const q = previewQ.trim()
    if (!q || previewBusy) return
    setPreviewBusy(true)
    setPreviewReply(null)
    setPreviewErr(null)
    try {
      const r = await api.post<{ ok: boolean; reply?: string; error?: string }>(
        '/prompt/preview', { question: q })
      if (r.ok) setPreviewReply(r.reply ?? '')
      else setPreviewErr(r.error ?? 'Неизвестная ошибка')
    } catch (e: any) { setPreviewErr(e.detail ?? e.message) }
    finally { setPreviewBusy(false) }
  }

  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
        <span className={`chip ${source === 'db' ? 'accent' : ''}`}>
          {source === 'db' ? 'кастомная версия' : 'заводской промпт'}
        </span>
        {dirty && <span className="chip warn">не сохранено</span>}
        <div style={{ flex: 1 }} />
        <button className="btn sm" onClick={test} disabled={busy}>🧪 Проверить</button>
        <button className="btn sm primary" onClick={save} disabled={busy || !dirty}>
          {busy ? <span className="spin" /> : '💾 Сохранить и активировать'}
        </button>
      </div>

      {problems.length > 0 && (
        <div className="card" style={{ borderColor: 'rgba(244,100,124,0.5)', marginBottom: 12 }}>
          <b style={{ color: 'var(--danger)' }}>Промпт не пройдёт — бот замолчит:</b>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 13 }}>
            {problems.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </div>
      )}

      <div className="brain-grid">
        <div className="brain-main">
          <textarea
            value={content}
            onChange={e => { setContent(e.target.value); setDirty(true) }}
            spellCheck={false}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              placeholder="Комментарий к версии (что поменяли)"
              value={comment}
              onChange={e => setComment(e.target.value)}
              style={{ flex: 1 }}
            />
            <button className="btn ghost" onClick={() => { setContent(defaultContent); setDirty(true) }}>
              ↺ Текст заводского
            </button>
          </div>
          <div className="faint" style={{ fontSize: 11.5 }}>
            Обязательные плейсхолдеры: {'{context} {history} {question} {corrections} {handoff_block}'} —
            без них сохранение заблокируется. Активная версия подхватывается ботом за 60 секунд, без деплоя.
          </div>

          {/* ---- Превью ответа бота ---- */}
          <div className="card" style={{ marginTop: 14, padding: 12, background: 'var(--bg2, rgba(0,0,0,0.04))' }}>
            <b style={{ fontSize: 13 }}>🧪 Проверить ответ</b>
            <p className="faint" style={{ fontSize: 11.5, margin: '4px 0 8px' }}>
              Быстрый прогон без истории лида и базы знаний — примерная проверка тона/логики.
            </p>
            <div style={{ display: 'flex', gap: 8 }}>
              <textarea
                placeholder="Введите тестовый вопрос лида…"
                value={previewQ}
                onChange={e => setPreviewQ(e.target.value)}
                style={{ flex: 1, minHeight: 52 }}
                onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) runPreview() }}
              />
              <button
                className="btn sm primary"
                onClick={runPreview}
                disabled={previewBusy || !previewQ.trim()}
                style={{ alignSelf: 'flex-start' }}
              >
                {previewBusy ? <span className="spin" /> : 'Прогнать'}
              </button>
            </div>
            {previewReply !== null && (
              <div style={{
                marginTop: 10, padding: '10px 12px',
                background: 'var(--bg, #fff)', borderRadius: 8,
                border: '1px solid var(--border, rgba(0,0,0,0.1))',
                fontSize: 13, lineHeight: 1.55, whiteSpace: 'pre-wrap',
              }}>
                {previewReply}
              </div>
            )}
            {previewErr && (
              <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--danger)' }}>
                Ошибка: {previewErr}
              </div>
            )}
          </div>
        </div>

        <div className="brain-side">
          <div className="card" style={{ padding: 12 }}>
            <b style={{ fontSize: 13 }}>Версии</b>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
              {source === 'db' && (
                <button className="btn sm" onClick={() => activate(null)} disabled={busy}>
                  ↩ Откатиться на заводской
                </button>
              )}
              {versions.length === 0 && <span className="faint" style={{ fontSize: 12 }}>Пока нет сохранённых версий</span>}
              {versions.map(v => (
                <div key={v.id} className={`version-item${v.is_active ? ' active' : ''}`}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    {v.is_active && <span className="chip accent">активна</span>}
                    <span className="faint">{fmtTime(v.created_at)}</span>
                  </div>
                  {v.comment && <div>{v.comment}</div>}
                  <div className="faint" style={{ fontSize: 11 }}>{v.preview.slice(0, 90)}…</div>
                  {!v.is_active && (
                    <button className="btn sm" onClick={() => activate(v.id)} disabled={busy}>Активировать</button>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
