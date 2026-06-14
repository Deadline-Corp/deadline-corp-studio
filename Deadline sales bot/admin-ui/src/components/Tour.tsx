import { useEffect, useLayoutEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

/* Spotlight-обучение: интерфейс затемняется, нужный элемент подсвечивается
   «дыркой» в оверлее, рядом — карточка с объяснением. Можно пропустить,
   закрыть, пройти заново (Настройки). Ведёт по вкладкам само. */

const STEPS: Array<{ to: string; target?: string; icon: string; title: string; text: string }> = [
  {
    to: '/', icon: '👋', title: 'Что это за система — за 20 секунд',
    text: 'Это ваш отдел продаж в одном окне. AI-бот сам общается с клиентами, греет их и ведёт к цели, а вы всё видите и вмешиваетесь, когда нужно. Пройдём по шагам — 2 минуты.',
  },
  {
    to: '/', icon: '🤖', title: 'Как бот работает сам',
    text: 'Клиент пишет → бот отвечает за секунды, узнаёт потребность, запоминает контакты, двигает сделку по воронке и ведёт к созвону. Молчунам напоминает о себе сам. Вы — закрывающий.',
  },
  {
    to: '/', target: 'nav-/', icon: '🕸', title: 'Канвас — пульт системы',
    text: 'Слева каналы, в центре бот, справа подсистемы и дашборд. Кликните по карточке — провалитесь внутрь. Карточки можно перетаскивать.',
  },
  {
    to: '/funnel', target: 'nav-/funnel', icon: '📊', title: 'Воронка — ваши сделки',
    text: 'Карточки лидов по этапам. Перетаскивайте мышкой; «⚙ Настроить стадии» — назовите этапы словами вашего бизнеса. Клик по карточке — переписка.',
  },
  {
    to: '/inbox', target: 'nav-/inbox', icon: '💬', title: 'Переписки — всё в одном месте',
    text: 'Сайт, Telegram, Instagram — все диалоги здесь, бейдж показывает канал. Внутри: «Взять на себя» (бот замолкает), стадия, поля, пинок молчуну, «🎓 научить бота» на ваших ответах.',
  },
  {
    to: '/tasks', target: 'nav-/tasks', icon: '⏰', title: 'Задачи — ваше утро начинается тут',
    text: '🔴 просрочено → 🟡 сегодня → 📅 неделя + созвоны. 🤖-задачи бот делает сам, 👤-задачи закрываете вы. План на день собирается сам.',
  },
  {
    to: '/automations', target: 'nav-/automations', icon: '⚡', title: 'Автоматизации',
    text: '«Когда → Если → То» и цепочки касаний (день 1→3→7): молчуна догреет, нового лида объявит, зависшего передаст вам. Собирается кнопками.',
  },
  {
    to: '/analytics', target: 'nav-/analytics', icon: '📈', title: 'Аналитика',
    text: 'Лиды, каналы, воронка, возражения («почему не покупают» — с цитатами клиентов). Раз в день взгляните — видно, где затык. Плюс утренний дайджест в Telegram.',
  },
  {
    to: '/brain', target: 'nav-/brain', icon: '🧠', title: 'Мозг — учите бота как стажёра',
    text: 'Правило по-человечески: «Когда спрашивают цену — называй вилку и зови на созвон» — применяется сразу. Цель бота (созвон/заявки/продажа) — в Настройках.',
  },
  {
    to: '/channels', target: 'nav-/channels', icon: '🔌', title: 'Каналы',
    text: 'Откуда приходят клиенты. Зелёное — работает. Подключить новое — раскройте «Как подключить», там пошагово простыми словами.',
  },
  {
    to: '/settings', target: 'nav-/settings', icon: '⚙️', title: 'Настройки + значки «?»',
    text: 'Пресет ниши, поля лида, поведение бота, команда, демо-данные, логотип и цвет. Рядом с кнопками — значки «?»: наведите, всплывёт объяснение. Удачных продаж! 🎉',
  },
]

const TOUR_KEY = 'deadline_tour_done'

export function isTourDone(): boolean {
  return localStorage.getItem(TOUR_KEY) === '1'
}

export function startTour() {
  window.dispatchEvent(new Event('start-tour'))
}

export function Tour() {
  const [step, setStep] = useState<number | null>(null)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    const start = () => { setStep(0); navigate(STEPS[0].to) }
    window.addEventListener('start-tour', start)
    return () => window.removeEventListener('start-tour', start)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Позиция подсвечиваемого элемента (по data-tour атрибуту).
  useLayoutEffect(() => {
    if (step === null) { setRect(null); return }
    const t = STEPS[step].target
    if (!t) { setRect(null); return }
    const el = document.querySelector(`[data-tour="${t}"]`)
    setRect(el ? el.getBoundingClientRect() : null)
  }, [step])

  if (step === null) return null
  const s = STEPS[step]
  const finish = () => { localStorage.setItem(TOUR_KEY, '1'); setStep(null) }
  const go = (d: number) => {
    const n = step + d
    if (n < 0 || n >= STEPS.length) return
    setStep(n)
    navigate(STEPS[n].to)
  }

  // Карточка: рядом с подсветкой (справа от сайдбара) или по центру низа.
  const cardStyle: React.CSSProperties = rect
    ? { position: 'fixed', left: rect.right + 18, top: Math.max(16, Math.min(rect.top - 10, window.innerHeight - 280)), zIndex: 96 }
    : { position: 'fixed', left: '50%', bottom: 30, transform: 'translateX(-50%)', zIndex: 96 }

  return (
    <>
      {/* Затемнение с «дыркой» вокруг элемента (box-shadow трюк) */}
      {rect ? (
        <div style={{
          position: 'fixed',
          left: rect.left - 6, top: rect.top - 6,
          width: rect.width + 12, height: rect.height + 12,
          borderRadius: 10, zIndex: 95, pointerEvents: 'none',
          boxShadow: '0 0 0 9999px rgba(5,6,12,0.72)',
          border: '2px solid var(--accent)',
          transition: 'all 0.25s ease',
        }} />
      ) : (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(5,6,12,0.72)', zIndex: 95 }}
             onClick={finish} />
      )}

      <div className="card" style={{
        ...cardStyle, width: 440,
        boxShadow: '0 18px 50px rgba(0,0,0,0.6)',
        border: '1px solid var(--accent-border)',
        display: 'flex', flexDirection: 'column', gap: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 22 }}>{s.icon}</span>
          <b style={{ flex: 1 }}>{s.title}</b>
          <span className="faint" style={{ fontSize: 12 }}>{step + 1}/{STEPS.length}</span>
        </div>
        <p style={{ margin: 0, fontSize: 13.5, color: 'var(--text-dim)', lineHeight: 1.55 }}>{s.text}</p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn sm ghost" onClick={finish}>Пропустить</button>
          <div style={{ flex: 1 }} />
          {step > 0 && <button className="btn sm" onClick={() => go(-1)}>← Назад</button>}
          {step < STEPS.length - 1
            ? <button className="btn sm primary" onClick={() => go(1)}>Дальше →</button>
            : <button className="btn sm primary" onClick={finish}>Готово 🎉</button>}
        </div>
      </div>
    </>
  )
}
