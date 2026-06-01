/* ============================================================================
   DEADLINE — CASES DATA  (единый источник правды для страницы кейсов)
   ----------------------------------------------------------------------------
   Вся страница cases.html рендерится из массива CASES ниже.
   Чтобы ДОБАВИТЬ или ЗАМЕНИТЬ кейс — правьте только этот файл. Вёрстку не трогать.

   Как подставить реальный ассет (см. подробно assets/cases/README.md):
   1. Положите файл в  assets/cases/<id>/  (напр. assets/cases/vrp/demo.mp4)
   2. В нужном кейсе ниже поменяйте media.mode и пропишите путь:
        media.mode = 'video'  → media.video = 'assets/cases/vrp/demo.mp4'
                                 media.poster = 'assets/cases/vrp/poster.jpg'
        media.mode = 'image'  → media.images = ['assets/cases/x/1.jpg', ...]
        media.mode = 'iframe' → media.liveUrl = 'https://...'   (живой сайт в рамке)
        media.mode = 'chat'   → media.chat = [ {from, text:{ru,en}}, ... ]
        media.mode = 'placeholder' → ничего не нужно, рисуется фирменная заглушка
   3. Готово. mode определяет, КАК показывается кейс.

   Поля кейса:
     id        — уникальный slug (= имя папки в assets/cases/)
     type      — 'web' | 'app' | 'bot' | 'data'  (архетип показа + фильтр)
     featured  — true → крупная карточка в bento-сетке (2 колонки)
     frame     — 'browser' | 'phone'  (рамка устройства для web/app)
     title/meta/summary — { ru, en }
     chip      — короткий итог-бейдж
     metrics   — [{ value:Number, prefix?, suffix?, label:{ru,en} }]  (счётчики)
     media     — { mode, poster, video, images[], liveUrl, chat[] }
     flow      — [{ icon, label:{ru,en} }]  (узлы схемы, только для type:'data')
   ============================================================================ */

window.CASES = [

  /* ── 1 · VRP — сайт + AI (web) · ФЛАГМАН ───────────────────────────────── */
  {
    id: 'vrp',
    type: 'web',
    featured: true,
    frame: 'browser',
    title: { ru: 'AI-консьерж 24/7', en: 'AI concierge, 24/7' },
    meta:  { ru: 'VIP RENTAL PHUKET · 2026 · WEB + AI',
             en: 'VIP RENTAL PHUKET · 2026 · WEB + AI' },
    summary: {
      ru: 'Next.js + GPT-4 консьерж в чате booking-сайта. Закрывает 73% запросов без человека. Команда клиента управляет 80+ объектами одна.',
      en: 'Next.js + GPT-4 concierge inside the booking-site chat. Handles 73% of inquiries without a human. The client runs 80+ properties solo.'
    },
    chip: '+32% conversion',
    metrics: [
      { value: 32, prefix: '+', suffix: '%', label: { ru: 'конверсия лендинга', en: 'landing conversion' } },
      { value: 73, suffix: '%', label: { ru: 'запросов без человека', en: 'inquiries auto-handled' } },
      { value: 80, suffix: '+', label: { ru: 'объектов на команду', en: 'properties per team' } }
    ],
    media: {
      mode: 'placeholder',                 // → 'iframe' (liveUrl) или 'video'
      liveUrl: '',                         // напр. 'https://viprentalphuket.com'
      video: '',                           // напр. 'assets/cases/vrp/demo.mp4'
      poster: ''                           // напр. 'assets/cases/vrp/poster.jpg'
    }
  },

  /* ── 2 · KEYDROP — Telegram-бот / MiniApp (bot) · ФЛАГМАН ──────────────── */
  {
    id: 'keydrop',
    type: 'bot',
    featured: true,
    frame: 'phone',
    title: { ru: 'Telegram MiniApp e-commerce', en: 'Telegram MiniApp e-commerce' },
    meta:  { ru: 'KEYDROP · 2025 · AUTOMATION + MINIAPP',
             en: 'KEYDROP · 2025 · AUTOMATION + MINIAPP' },
    summary: {
      ru: 'Полная автоматизация выдачи Steam-кодов. 1 000+ заказов/мес без человека. Uptime 99.99% за 18 месяцев.',
      en: 'End-to-end Steam-code delivery. 1,000+ orders/mo, zero human ops. 99.99% uptime over 18 months.'
    },
    chip: '99.99% uptime',
    metrics: [
      { value: 1000, suffix: '+', label: { ru: 'заказов / мес',  en: 'orders / month' } },
      { value: 99.99, suffix: '%', label: { ru: 'uptime · 18 мес', en: 'uptime · 18 mo' } },
      { value: 0, suffix: '₽', label: { ru: 'зарплата операторов', en: 'operator payroll' } }
    ],
    /* mode:'chat' — фейковый диалог с ботом печатается при попадании в экран.
       Замените тексты на реальный сценарий вашего бота. */
    media: {
      mode: 'chat',
      chat: [
        { from: 'user', text: { ru: '/start', en: '/start' } },
        { from: 'bot',  text: { ru: 'Привет! 🎮 Каталог Steam-кодов открыт. Что ищем?', en: 'Hey! 🎮 Steam-code catalog is open. What are you after?' } },
        { from: 'user', text: { ru: 'Cyberpunk 2077', en: 'Cyberpunk 2077' } },
        { from: 'bot',  text: { ru: 'Есть в наличии — 1 290 ₽. Оплата картой/крипто. Выдача мгновенная.', en: 'In stock — $14.90. Card/crypto. Instant delivery.' } },
        { from: 'user', text: { ru: 'Беру', en: 'Take it' } },
        { from: 'bot',  text: { ru: '✅ Оплачено. Ваш ключ: XXXX-XXXX-XXXX. Активируйте в Steam. Спасибо!', en: '✅ Paid. Your key: XXXX-XXXX-XXXX. Activate in Steam. Thanks!' } }
      ]
    }
  },

  /* ── 3 · RA — бэкенд / данные / AI (data) · ФЛАГМАН ────────────────────── */
  {
    id: 'ra',
    type: 'data',
    featured: true,
    title: { ru: 'On-chain аналитика', en: 'On-chain analytics' },
    meta:  { ru: 'RA PROJECT · 2025–2026 · DATA + AI',
             en: 'RA PROJECT · 2025–2026 · DATA + AI' },
    summary: {
      ru: '12 блокчейн-сетей, ClickHouse + Kafka, AI-классификация транзакций. От нуля до production за 14 недель.',
      en: '12 blockchain networks, ClickHouse + Kafka, AI transaction classification. Zero to production in 14 weeks.'
    },
    chip: '12 chains live',
    metrics: [
      { value: 41, suffix: 'M+', label: { ru: 'блоков проиндексировано', en: 'blocks indexed' } },
      { value: 12, suffix: '',   label: { ru: 'сетей в проде', en: 'chains in prod' } },
      { value: 14, suffix: ' нед', label: { ru: 'до production', en: 'to production' } }
    ],
    /* type:'data' → рисуется анимированная flow-схема пайплайна */
    flow: [
      { icon: 'chains', label: { ru: '12 сетей',        en: '12 networks' } },
      { icon: 'stream', label: { ru: 'Kafka стрим',     en: 'Kafka stream' } },
      { icon: 'store',  label: { ru: 'ClickHouse',      en: 'ClickHouse' } },
      { icon: 'ai',     label: { ru: 'AI-классификация', en: 'AI classify' } },
      { icon: 'agents', label: { ru: '4 агента в проде', en: '4 agents live' } }
    ],
    media: { mode: 'placeholder' }
  },

  /* ── 4 · ПЛЕЙСХОЛДЕР — мобильное приложение (app) ──────────────────────── */
  {
    id: 'app-demo',
    type: 'app',
    featured: false,
    frame: 'phone',
    title: { ru: 'Мобильное приложение', en: 'Mobile app' },
    meta:  { ru: 'СКОРО · 2026 · iOS + ANDROID', en: 'SOON · 2026 · iOS + ANDROID' },
    summary: {
      ru: 'Слот под кейс мобильного приложения. Сюда — скринкаст интерфейса (mode:video) или экраны (mode:image).',
      en: 'Slot for a mobile-app case. Drop in a UI screencast (mode:video) or screens (mode:image).'
    },
    chip: 'placeholder',
    metrics: [],
    media: { mode: 'placeholder' }
  },

  /* ── 5 · ПЛЕЙСХОЛДЕР — лендинг / сайт (web) ────────────────────────────── */
  {
    id: 'web-demo',
    type: 'web',
    featured: false,
    frame: 'browser',
    title: { ru: 'Лендинг под ключ', en: 'Landing page' },
    meta:  { ru: 'СКОРО · 2026 · WEB', en: 'SOON · 2026 · WEB' },
    summary: {
      ru: 'Слот под веб-кейс. Поставьте mode:iframe + liveUrl — реальный сайт прокрутится внутри рамки браузера.',
      en: 'Slot for a web case. Set mode:iframe + liveUrl — the live site scrolls inside the browser frame.'
    },
    chip: 'placeholder',
    metrics: [],
    media: { mode: 'placeholder' }
  },

  /* ── 6 · ПЛЕЙСХОЛДЕР — бот (bot) ───────────────────────────────────────── */
  {
    id: 'bot-demo',
    type: 'bot',
    featured: false,
    title: { ru: 'Telegram-бот', en: 'Telegram bot' },
    meta:  { ru: 'СКОРО · 2026 · BOT', en: 'SOON · 2026 · BOT' },
    summary: {
      ru: 'Слот под кейс бота. Пропишите media.chat сценарием диалога — он анимируется как живая переписка.',
      en: 'Slot for a bot case. Fill media.chat with a dialogue script — it animates like a live chat.'
    },
    chip: 'placeholder',
    metrics: [],
    media: {
      mode: 'chat',
      chat: [
        { from: 'user', text: { ru: 'Привет', en: 'Hi' } },
        { from: 'bot',  text: { ru: 'Здесь будет ваш сценарий 👋', en: 'Your script goes here 👋' } }
      ]
    }
  }

];

/* Порядок и подписи фильтра. key='all' — показать все. */
window.CASE_FILTERS = [
  { key: 'all', label: { ru: 'Все',          en: 'All' } },
  { key: 'web', label: { ru: 'Сайты',        en: 'Web' } },
  { key: 'bot', label: { ru: 'Боты',         en: 'Bots' } },
  { key: 'app', label: { ru: 'Приложения',   en: 'Apps' } },
  { key: 'data',label: { ru: 'Данные / AI',  en: 'Data / AI' } }
];
