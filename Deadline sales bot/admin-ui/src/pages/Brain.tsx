import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { PromptVersionItem } from '../api/types'
import { fmtTime } from '../lib'

/* «Мозг»: редактор системного промпта с версиями, валидацией и откатом
   + список активных training-правил. Бот подхватывает новую версию ≤60с. */

export function Brain() {
  const [content, setContent] = useState('')
  const [source, setSource] = useState<'db' | 'file'>('file')
  const [defaultContent, setDefaultContent] = useState('')
  const [versions, setVersions] = useState<PromptVersionItem[]>([])
  const [rules, setRules] = useState<any[]>([])
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null)
  const [problems, setProblems] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)

  const showToast = (text: string, err = false) => {
    setToast({ text, err })
    setTimeout(() => setToast(null), 4500)
  }

  const load = async () => {
    try {
      const p = await api.get<{ source: 'db' | 'file'; content: string; default_content: string }>('/prompt')
      setContent(p.content)
      setSource(p.source)
      setDefaultContent(p.default_content)
      setDirty(false)
      const v = await api.get<{ items: PromptVersionItem[] }>('/prompt/versions')
      setVersions(v.items)
      const r = await api.get<{ items: any[] }>('/training-rules')
      setRules(r.items)
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

  return (
    <div className="page">
      <div className="page-head">
        <h1>Мозг бота</h1>
        <span className={`chip ${source === 'db' ? 'accent' : ''}`}>
          {source === 'db' ? 'кастомная версия' : 'заводской промпт'}
        </span>
        {dirty && <span className="chip warn">не сохранено</span>}
        <div className="spacer" />
        <button className="btn" onClick={test} disabled={busy}>🧪 Проверить</button>
        <button className="btn primary" onClick={save} disabled={busy || !dirty}>
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
            без них сохранение заблокируется. Активная версия подхватывается ботом в течение 60 секунд, без деплоя.
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

          <div className="card" style={{ padding: 12 }}>
            <b style={{ fontSize: 13 }}>Правила обучения ({rules.length})</b>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
              {rules.length === 0 && <span className="faint" style={{ fontSize: 12 }}>Активных правил нет</span>}
              {rules.map(r => (
                <div key={r.id} className="version-item">
                  <div style={{ fontSize: 12 }}>{r.guidance}</div>
                  <div className="faint" style={{ fontSize: 11 }}>
                    {r.channel ? `канал: ${r.channel} · ` : ''}{fmtTime(r.created_at)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.text}</div>}
    </div>
  )
}
