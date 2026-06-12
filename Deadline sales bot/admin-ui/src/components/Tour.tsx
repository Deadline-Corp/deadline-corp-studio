import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

/* Обучение по функционалу: пошаговый тур. Карточка-оверлей ведёт по вкладкам
   (сама открывает каждую), объясняя по-человечески. Запуск: после онбординга
   или из Настроек («Показать обучение»). Прогресс в localStorage. */

const STEPS: Array<{ to: string; icon: string; title: string; text: string }> = [
  {
    to: '/', icon: '👋', title: 'Что это за система — за 20 секунд',
    text: 'Это ваш отдел продаж в одном окне. AI-бот сам общается с клиентами (на сайте, в Telegram…), греет их и ведёт к созвону, а вы здесь всё видите и вмешиваетесь, когда нужно. Пройдём по шагам — 2 минуты.',
  },
  {
    to: '/', icon: '🤖', title: 'Как бот работает сам (без вас)',
    text: 'Клиент пишет → бот отвечает в течение секунд, узнаёт что нужно, запоминает имя и контакты, двигает сделку по воронке и зовёт на созвон. Замолчавшим — сам напоминает о себе. Ваша роль — подхватывать тёплых и закрывать сделки.',
  },
  {
    to: '/', icon: '🕸', title: 'Канвас — пульт системы',
    text: 'Слева каналы (откуда приходят клиенты), в центре бот, справа подсистемы и дашборд. Кликните по карточке — провалитесь внутрь. Карточки можно перетаскивать — раскладка запомнится.',
  },
  {
    to: '/funnel', icon: '📊', title: 'Воронка — ваши сделки',
    text: 'Каждый клиент — карточка, колонки — этапы сделки. Бот двигает карточки сам по мере прогресса; вы можете перетащить мышкой вручную. «⚙ Настроить стадии» — назовите этапы словами вашего бизнеса.',
  },
  {
    to: '/inbox', icon: '💬', title: 'Переписки — все диалоги в одном месте',
    text: 'Сайт, Telegram, Instagram — всё стекается сюда, цветной бейдж показывает откуда лид. Откройте диалог — увидите всю историю общения бота с клиентом.',
  },
  {
    to: '/inbox', icon: '👤', title: 'Когда вмешиваться?',
    text: 'Главное правило: бот — первая линия, вы — закрывающий. Вмешивайтесь, когда лид готов (бот передал, назначен созвон) или когда разговор пошёл не туда. Кнопка «Взять на себя» — бот замолкает, пишете вы. «Вернуть боту» — он продолжит.',
  },
  {
    to: '/tasks', icon: '⏰', title: 'Типовое утро: открыли «Задачи»',
    text: 'Начинайте день отсюда: 🔴 просрочено (разобраться сразу) → 🟡 сегодня → 📞 созвоны недели. 🤖-задачи бот сделает сам, 👤-задачи — ваши, закрывайте «Сделано». Это ваш план на день, который собирается сам.',
  },
  {
    to: '/automations', icon: '⚡', title: 'Автоматизации — правила для бота',
    text: 'Здесь вы задаёте боту глобальные правила: «Появился новый лид → уведомить меня» или «Молчит сутки → напомни о себе». Конструктор «Когда → Если → То», собирается кнопками. Бот проверяет правила каждые 10 минут.',
  },
  {
    to: '/analytics', icon: '📈', title: 'Аналитика — где затык',
    text: 'Сколько лидов, откуда, на каком этапе застревают, почему теряются. Раз в день взгляните — и понятно, что улучшать: рекламу, скорость ответа или дожим.',
  },
  {
    to: '/brain', icon: '🧠', title: 'Мозг — учите бота как стажёра',
    text: 'Бот ошибся или хотите по-другому? Напишите правило по-человечески: «Когда спрашивают про цену — называй вилку и зови на созвон». Применяется сразу. Так бот становится умнее с каждой неделей.',
  },
  {
    to: '/channels', icon: '🔌', title: 'Каналы — подключения',
    text: 'Откуда бот принимает клиентов. Зелёное — работает. Подключить новое — раскройте «Как подключить», там пошаговая инструкция простыми словами.',
  },
  {
    to: '/settings', icon: '⚙️', title: 'Настройки + значки «?» вам в помощь',
    text: 'Пресет ниши (перестроит всё в 1 клик), поля лида, поведение бота, демо-данные для тренировки. И главное: рядом с кнопками есть значки «?» — наведите, и всплывёт объяснение. Надоели — выключите здесь галкой 💡. Удачных продаж! 🎉',
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
