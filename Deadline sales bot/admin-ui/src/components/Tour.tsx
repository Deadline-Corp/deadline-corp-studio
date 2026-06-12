import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

/* Обучение по функционалу: пошаговый тур. Карточка-оверлей ведёт по вкладкам
   (сама открывает каждую), объясняя по-человечески. Запуск: после онбординга
   или из Настроек («Показать обучение»). Прогресс в localStorage. */

const STEPS: Array<{ to: string; icon: string; title: string; text: string }> = [
  {
    to: '/', icon: '🕸', title: 'Канвас — пульт всей системы',
    text: 'Бот в центре, слева каналы (откуда приходят лиды), справа подсистемы. Кликайте по любой карточке — провалитесь внутрь. Цифры обновляются сами.',
  },
  {
    to: '/funnel', icon: '📊', title: 'Воронка — ваши сделки',
    text: 'Каждый лид — карточка. Перетаскивайте между стадиями мышкой. Кнопка «⚙ Настроить стадии» — переименуйте этапы под свой бизнес. Клик по карточке — открывается переписка.',
  },
  {
    to: '/inbox', icon: '💬', title: 'Переписки — все диалоги в одном месте',
    text: 'Сайт, Telegram, Instagram — всё здесь. Откройте любой диалог: можно ответить лиду самому («Взять на себя» — бот замолчит), сменить стадию, заполнить поля, пнуть молчуна.',
  },
  {
    to: '/tasks', icon: '⏰', title: 'Задачи — ваш день',
    text: '«Мой день»: 🔴 просрочено → 🟡 сегодня → 📅 неделя, плюс созвоны. Задачи бота он выполняет сам, ваши — закрываете кнопкой «Сделано». Поставить задачу можно из карточки любого лида.',
  },
  {
    to: '/automations', icon: '⚡', title: 'Автоматизации — бот работает сам',
    text: 'Правила «Когда → Если → То»: лид молчит сутки → бот напомнит о себе или поставит вам задачу. Собирается кнопками, без программиста. Проверка каждые 10 минут.',
  },
  {
    to: '/analytics', icon: '📈', title: 'Аналитика — цифры продаж',
    text: 'Сколько лидов пришло, откуда, где они в воронке, почему теряются. Смотрите сюда раз в день — и видно, где затык.',
  },
  {
    to: '/brain', icon: '🧠', title: 'Мозг — характер и правила бота',
    text: 'Главное место настройки. Пишете правило по-человечески: «Когда спрашивают про цену — называй вилку и зови на созвон» — бот начинает применять сразу. Сложное спрятано в «Продвинутое».',
  },
  {
    to: '/channels', icon: '🔌', title: 'Каналы — подключения',
    text: 'Статус каждого канала и пошаговые инструкции: как подключить Telegram, Instagram, WhatsApp. Если что-то не подключено — здесь написано, что сделать.',
  },
  {
    to: '/settings', icon: '⚙️', title: 'Настройки — всё под вашу нишу',
    text: 'Пресеты ниш (настроить систему в 1 клик), поля лида, поведение бота (когда пинговать молчунов), демо-данные для тренировки. Готово — вы знаете систему! 🎉',
  },
]

const TOUR_KEY = 'deadline_tour_done'

export function isTourDone(): boolean {
  return localStorage.getItem(TOUR_KEY) === '1'
}

export function Tour() {
  const [step, setStep] = useState<number | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    const start = () => { setStep(0); navigate(STEPS[0].to) }
    window.addEventListener('start-tour', start)
    return () => window.removeEventListener('start-tour', start)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (step === null) return null
  const s = STEPS[step]
  const finish = () => {
    localStorage.setItem(TOUR_KEY, '1')
    setStep(null)
  }
  const go = (d: number) => {
    const n = step + d
    if (n < 0 || n >= STEPS.length) return
    setStep(n)
    navigate(STEPS[n].to)
  }

  return (
    <div style={{
      position: 'fixed', left: 0, right: 0, bottom: 26, zIndex: 90,
      display: 'flex', justifyContent: 'center', pointerEvents: 'none',
    }}>
      <div className="card" style={{
        width: 480, pointerEvents: 'auto',
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
    </div>
  )
}

export function startTour() {
  window.dispatchEvent(new Event('start-tour'))
}
