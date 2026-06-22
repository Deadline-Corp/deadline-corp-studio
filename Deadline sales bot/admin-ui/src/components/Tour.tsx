import { useEffect, useLayoutEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

/* Spotlight-обучение: интерфейс затемняется, нужный раздел подсвечивается «дыркой»,
   рядом — карточка с глубоким объяснением (что это, как работает, где применить).
   Ведёт по вкладкам само. Можно пропустить / пройти заново (Настройки). */

interface Step {
  to: string
  target?: string
  icon: string
  title: string
  text: string
  points?: string[]   // что конкретно можно делать (по делу, профессионально)
  apply?: string      // «где это применить» — для не знающего проект
}

const STEPS: Step[] = [
  {
    to: '/', icon: '👋', title: 'Что это за система — за 30 секунд',
    text: 'Это ваш отдел продаж в одном окне. AI-бот сам общается с клиентами со всех каналов, греет их, ведёт к цели (созвон / заявка / оплата), а вы всё видите и вмешиваетесь, когда нужно. Пройдём по разделам — это 3 минуты, после них вы будете понимать систему целиком.',
    points: [
      'Бот = первая линия 24/7: отвечает за секунды, не устаёт, не теряет лидов',
      'Вы = закрывающий: контролируете, подключаетесь к важным, обучаете бота',
      'Всё в одном месте: переписки, воронка, задачи, календарь, аналитика',
    ],
  },
  {
    to: '/', icon: '🤖', title: 'Как бот работает сам',
    text: 'Клиент пишет в любой канал — бот сразу включается и ведёт диалог по вашей логике продаж.',
    points: [
      'Понимает запрос (в т.ч. голосовые — расшифровывает), узнаёт потребность',
      'Запоминает контакты, двигает сделку по воронке по мере прогресса',
      'Ведёт к цели: предлагает созвон, бронирует время, ставит напоминания',
      'Молчунам напоминает о себе сам (дожим по расписанию)',
    ],
    apply: 'Применяйте где угодно: продажи услуг, запись клиентов, квалификация заявок, поддержка первой линии.',
  },
  {
    to: '/', target: 'nav-/', icon: '🕸', title: 'Канвас — пульт всей системы',
    text: 'Карта вашей системы: видно, как устроен поток и что где происходит.',
    points: [
      'Слева — источники трафика (каналы), в центре — бот, справа — подсистемы',
      'Ключевые цифры (диалоги, горячие, созвоны) — прямо на узле-агенте',
      'Клик по карточке — провалиться внутрь раздела; карточки перетаскиваются',
    ],
  },
  {
    to: '/funnel', target: 'nav-/funnel', icon: '📊', title: 'Воронка — все ваши сделки на доске',
    text: 'Канбан-доска: карточки лидов по этапам сделки — наглядно видно, кто на какой стадии и где затык.',
    points: [
      'Перетаскивайте карточки мышкой между стадиями',
      'Клик по карточке — открывается переписка и всё управление лидом',
      'Бот двигает сделки сам по мере прогресса диалога',
    ],
  },
  {
    to: '/funnel', target: 'nav-/funnel', icon: '⚙️', title: 'Воронка — настройте под свой бизнес',
    text: 'Воронка не жёсткая — это ваш процесс продаж, названный вашими словами.',
    points: [
      '«⚙ Настроить стадии» — переименуйте/добавьте этапы под вашу нишу',
      'Пресет ниши (в Настройках) перестроит воронку под типовой бизнес в 1 клик',
      'Карточки НЕ теряются при смене стадий — система сама переносит их на безопасный этап',
    ],
    apply: 'Клининг: Заявка → Замер → КП → Договор. Стоматология: Запись → Приём → План лечения. Настройте как у вас.',
  },
  {
    to: '/inbox', target: 'nav-/inbox', icon: '💬', title: 'Переписки — все каналы в одном окне',
    text: 'Главное окно работы. ВСЕ диалоги со всех источников трафика собираются здесь — не нужно прыгать между приложениями.',
    points: [
      'Сайт, WhatsApp, Telegram, Instagram, Messenger — всё в одном списке',
      'Цветной бейдж показывает, откуда пришёл лид',
      'История синхронизирована с реальным мессенджером: окно панели = окно WhatsApp (кнопка «🔄 Из WhatsApp» подтянет актуальное)',
    ],
  },
  {
    to: '/inbox', target: 'nav-/inbox', icon: '🎚', title: 'Переписки — 3 режима работы с диалогом',
    text: 'Вы решаете, сколько контроля брать на себя в каждом диалоге — от полного автопилота до полностью ручного.',
    points: [
      '🤖 Бот ведёт сам — автопилот, отвечает клиенту без вас',
      '👤 «Взять на себя» — бот замолкает, отвечаете вручную (лид не заметит)',
      '↩️ Вернуть боту — продолжит с того же места',
      '✍️ «Бот предлагает ответ» — вы только одобряете кнопкой, можно отредактировать',
    ],
    apply: 'Простые вопросы — на боте; сложный/дорогой клиент — берите на себя; не уверены — режим «бот предлагает, вы одобряете».',
  },
  {
    to: '/inbox', target: 'nav-/inbox', icon: '🧰', title: 'Переписки — всё управление лидом в карточке',
    text: 'Открыв диалог, прямо здесь вы делаете всю работу по клиенту — не выходя из окна.',
    points: [
      'Сменить стадию, заполнить поля клиента (бюджет/срок/задача)',
      'Поставить задачу по клиенту (себе или боту), назначить/перенести созвон',
      'Пнуть молчуна готовым сообщением; «🎓 научить бота» на вашем удачном ответе',
      'Видно «почему лид на этой стадии» — кто и когда его двигал',
    ],
  },
  {
    to: '/tasks', target: 'nav-/tasks', icon: '⏰', title: 'Задачи — ваше утро начинается здесь',
    text: 'Система сама собирает план на день — что горит, кому ответить, с кем созвон.',
    points: [
      '🔴 Просрочено → 🟡 Сегодня → 📅 Неделя + созвоны',
      '🤖-задачи бот выполняет сам, 👤-задачи закрываете вы',
      'Спящие лиды и массовый пинок молчунам — в один клик (с анти-баном)',
    ],
  },
  {
    to: '/calendar', target: 'nav-/calendar', icon: '📅', title: 'Календарь — созвоны и напоминания',
    text: 'Все назначенные созвоны и задачи на календарной сетке.',
    points: [
      'Бот ставит напоминания о созвоне лиду и вам — интервалы настраиваются (Настройки → 🔔 Напоминания)',
      'Лид ушёл в «Не сложилось» — его созвоны автоматически уходят из календаря',
      '«Подписаться в телефоне» — события появятся в вашем Google/Apple календаре',
    ],
  },
  {
    to: '/automations', target: 'nav-/automations', icon: '⚡', title: 'Автоматизации — правила без кода',
    text: 'Конструктор «Когда → Если → То»: система делает рутину за вас по вашим правилам.',
    points: [
      'Цепочки касаний (день 1 → 3 → 7): молчуна догреет сам',
      'Нового лида объявит, зависшего передаст вам',
      'Собирается кнопками — программировать ничего не нужно',
    ],
  },
  {
    to: '/analytics', target: 'nav-/analytics', icon: '📈', title: 'Аналитика — где затык и почему не покупают',
    text: 'Раз в день взгляните — видно, что работает, а что теряет деньги.',
    points: [
      'Лиды по каналам и дням, конверсия по воронке',
      'Возражения «почему не покупают» — с реальными цитатами клиентов',
      'Утренний AI-дайджест прямо в Telegram',
    ],
  },
  {
    to: '/brain', target: 'nav-/brain', icon: '🧠', title: 'Мозг — обучайте бота как стажёра',
    text: 'Бот говорит вашими словами и вашими фактами — вы его учите по-человечески.',
    points: [
      'Правило простым языком: «Когда спрашивают цену — называй вилку и зови на созвон»',
      'База знаний (кейсы, услуги, цены) — бот подмешивает её в ответы',
      'Цель бота (созвон / заявки / продажа) задаётся в Настройках',
    ],
    apply: 'Загрузите ваши услуги и цены — бот перестанет «выдумывать» и будет отвечать как ваш лучший менеджер.',
  },
  {
    to: '/settings', target: 'nav-/settings', icon: '🔌', title: 'Каналы — подключение источников',
    text: 'Откуда приходят клиенты. Настройки → карточка «Каналы»: подключайте WhatsApp, Telegram, сайт.',
    points: [
      'Зелёное — канал работает; webhook-URL и токены — на месте',
      'Подключение из панели, без переустановки и редеплоя',
    ],
  },
  {
    to: '/settings', target: 'nav-/settings', icon: '⚙️', title: 'Настройки — всё «под себя»',
    text: 'Здесь система подстраивается под ваш бизнес и команду.',
    points: [
      'Пресет ниши, поля лида, цель бота, языки, формат дайджеста',
      'Команда и роли: менеджер (ведёт лидов) / наблюдатель (только просмотр)',
      'Логотип и цвет, скрытие лишних разделов под нишу, напоминания о созвоне',
    ],
  },
  {
    to: '/settings', target: 'nav-/settings', icon: '📋', title: 'Логи — история работы системы',
    text: 'Настройки → «📋 Логи системы»: что / как / почему / кто.',
    points: [
      'Видно смену настроек, решения бота, отправки, ошибки — с временем и автором',
      'Быстро найти причину любого события или сбоя, не гадая',
    ],
  },
  {
    to: '/', icon: '🎉', title: 'Готово — вы знаете систему',
    text: 'Теперь главное: система работает на вас, а не наоборот.',
    points: [
      'Бот ведёт первую линию со всех каналов, вы — закрываете и обучаете',
      'Вы всегда видите всё и вмешиваетесь ровно когда хотите',
      'Начните: подключите канал → загрузите услуги/цены в Мозг → выберите режим в Переписках',
    ],
    apply: 'Удачных продаж! Запустить обучение заново можно в любой момент: Настройки → «🎓 Показать обучение».',
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

  // Позиция подсвечиваемого элемента (по data-tour). Небольшая задержка — дать
  // вкладке отрисоваться после navigate.
  useLayoutEffect(() => {
    if (step === null) { setRect(null); return }
    const t = STEPS[step].target
    if (!t) { setRect(null); return }
    let tries = 0
    const find = () => {
      const el = document.querySelector(`[data-tour="${t}"]`)
      if (el) { setRect(el.getBoundingClientRect()); return }
      if (tries++ < 10) setTimeout(find, 60); else setRect(null)
    }
    find()
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

  const cardStyle: React.CSSProperties = rect
    ? { position: 'fixed', left: rect.right + 18, top: Math.max(16, Math.min(rect.top - 10, window.innerHeight - 360)), zIndex: 96 }
    : { position: 'fixed', left: '50%', bottom: 30, transform: 'translateX(-50%)', zIndex: 96 }

  return (
    <>
      {rect ? (
        <div style={{
          position: 'fixed',
          left: rect.left - 6, top: rect.top - 6,
          width: rect.width + 12, height: rect.height + 12,
          borderRadius: 10, zIndex: 95, pointerEvents: 'none',
          boxShadow: '0 0 0 9999px rgba(5,6,12,0.74)',
          border: '2px solid var(--accent)',
          transition: 'all 0.25s ease',
        }} />
      ) : (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(5,6,12,0.74)', zIndex: 95 }}
             onClick={finish} />
      )}

      <div className="card" style={{
        ...cardStyle, width: 460, maxHeight: '78vh', overflowY: 'auto',
        boxShadow: '0 18px 50px rgba(0,0,0,0.6)',
        border: '1px solid var(--accent-border)',
        display: 'flex', flexDirection: 'column', gap: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 22 }}>{s.icon}</span>
          <b style={{ flex: 1, fontSize: 15 }}>{s.title}</b>
          <span className="faint" style={{ fontSize: 12 }}>{step + 1}/{STEPS.length}</span>
        </div>
        <p style={{ margin: 0, fontSize: 13.5, color: 'var(--text-dim)', lineHeight: 1.55 }}>{s.text}</p>
        {s.points && (
          <ul style={{ margin: '2px 0 0', paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 5 }}>
            {s.points.map((p, i) => (
              <li key={i} style={{ fontSize: 12.8, color: 'var(--text)', lineHeight: 1.5 }}>{p}</li>
            ))}
          </ul>
        )}
        {s.apply && (
          <div style={{ marginTop: 4, padding: '7px 10px', borderRadius: 8, fontSize: 12.5, lineHeight: 1.5,
                        background: 'var(--accent-soft)', color: 'var(--text)' }}>
            💡 {s.apply}
          </div>
        )}
        {/* прогресс-точки */}
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 2 }}>
          {STEPS.map((_, i) => (
            <span key={i} onClick={() => { setStep(i); navigate(STEPS[i].to) }}
                  style={{ width: 7, height: 7, borderRadius: '50%', cursor: 'pointer',
                           background: i === step ? 'var(--accent)' : 'var(--border)' }} />
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 2 }}>
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
