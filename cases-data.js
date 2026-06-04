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
    hideInGrid: true,        // показывается одним крупным разделом сверху, не дублируется в сетке
    frame: 'browser',
    title: { ru: 'Сайт + бот + админка', en: 'Site + bot + admin' },
    meta:  { ru: 'VIP RENTAL PHUKET · 2026 · WEB + AI',
             en: 'VIP RENTAL PHUKET · 2026 · WEB + AI' },
    summary: {
      ru: 'Автоматизировали продажи под ключ: клиент пустил трафик на сайт — и пошли продажи сами. Booking-сайт + Telegram-бот + админка: клиенты бронируют технику, транспорт и яхты сами, админ правит каталог из одной панели, а заявки сразу падают ему. AI-консьерж закрывает 73% вопросов.',
      en: 'Sales automated end-to-end: the client drove traffic to the site — and sales started on their own. Booking site + Telegram bot + admin panel: clients book gear, transport and yachts themselves, the admin runs the catalog from one panel, and requests land straight to them. The AI concierge handles 73%.'
    },
    chip: '+32% conversion',
    stack: ['Next.js', 'GPT-4', 'Telegram bot', 'Admin panel', 'Railway'],
    metrics: [
      { value: 32, prefix: '+', suffix: '%', label: { ru: 'конверсия лендинга', en: 'landing conversion' } },
      { value: 73, suffix: '%', label: { ru: 'запросов без человека', en: 'inquiries auto-handled' } },
      { value: 80, suffix: '+', label: { ru: 'объектов на команду', en: 'properties per team' } }
    ],
    /* архитектура: админ правит сайт+бота, клиент бронирует сам, заявка → админу */
    graph: {
      nodes: [
        { id: 'client', x: 11, y: 50, title: { ru: 'Клиент', en: 'Client' }, sub: { ru: 'бронь сам', en: 'self-book' }, cls: 'src', ico: 'agents' },
        { id: 'site',   x: 38, y: 26, title: { ru: 'Сайт /ru', en: 'Site /ru' }, sub: 'booking', cls: '', ico: 'box' },
        { id: 'bot',    x: 38, y: 74, title: { ru: 'TG-бот', en: 'TG bot' }, sub: { ru: 'меню', en: 'menu' }, cls: '', ico: 'stream' },
        { id: 'admin',  x: 64, y: 50, title: { ru: 'Админка', en: 'Admin' }, sub: { ru: 'правит контент', en: 'edits content' }, cls: 'core', ico: 'store' },
        { id: 'req',    x: 88, y: 50, title: { ru: 'Заявка', en: 'Request' }, sub: { ru: '→ админу', en: '→ admin' }, cls: 'out', ico: 'ai' }
      ],
      edges: [['admin','site'],['admin','bot'],['client','site'],['client','bot'],['site','req'],['bot','req'],['req','admin']]
    },
    /* карточка — запись реального сайта во всю рамку */
    media: { mode: 'video', video: 'assets/cases/vrp/site.mp4', ar: '1896/868' }
  },

  /* ── 2 · KEYDROP — Telegram-бот / MiniApp (bot) · ФЛАГМАН ──────────────── */
  {
    id: 'keydrop',
    type: 'bot',
    featured: false,
    hideInGrid: true,        // один кейс: интерфейс + схема в разделе ниже, без дубля в сетке
    frame: 'phone',
    title: { ru: 'Telegram MiniApp e-commerce', en: 'Telegram MiniApp e-commerce' },
    meta:  { ru: 'KEYDROP · 2025 · AUTOMATION + MINIAPP',
             en: 'KEYDROP · 2025 · AUTOMATION + MINIAPP' },
    summary: {
      ru: 'Магазин, который работает сам: каталог и цены подтягиваются с разных источников и автоматически обновляются на сайте, когда меняются у поставщиков. Выдача Steam-кодов мгновенная — 1 000+ заказов/мес без человека, uptime 99.99%.',
      en: 'A store that runs itself: catalog and prices are pulled from multiple sources and auto-update on the site when suppliers change them. Steam-code delivery is instant — 1,000+ orders/mo, zero humans, 99.99% uptime.'
    },
    chip: '99.99% uptime',
    stack: ['Telegram MiniApp', 'Node.js', 'Payments API', 'PostgreSQL'],
    metrics: [
      { value: 1000, suffix: '+', label: { ru: 'заказов / мес',  en: 'orders / month' } },
      { value: 99.99, suffix: '%', label: { ru: 'uptime · 18 мес', en: 'uptime · 18 mo' } },
      { value: 0, suffix: '₽', label: { ru: 'зарплата операторов', en: 'operator payroll' } }
    ],
    /* mode:'chat' — фейковый диалог с ботом печатается при попадании в экран.
       Замените тексты на реальный сценарий вашего бота. */
    /* схема авто-синка: источники → парсер → каталог → сайт/бот → клиент */
    graph: {
      nodes: [
        { id: 'steam',   x: 9,  y: 14, title: 'Steam',      sub: 'API',          cls: 'src', logo: { t: 'St', c: '#1b2838' } },
        { id: 'g2a',     x: 9,  y: 38, title: 'G2A',        sub: 'feed',         cls: 'src', logo: { t: 'G',  c: '#ff6b00' } },
        { id: 'kinguin', x: 9,  y: 62, title: 'Kinguin',    sub: 'feed',         cls: 'src', logo: { t: 'K',  c: '#e4392b' } },
        { id: 'sheet',   x: 9,  y: 86, title: 'Excel / CSV', sub: { ru: 'фид', en: 'feed' }, cls: 'src', logo: { t: 'XLS', c: '#1d6f42' } },
        { id: 'sync',    x: 31, y: 50, title: 'Sync',       sub: { ru: 'парсер 24/7', en: 'parser 24/7' }, cls: 'core', ico: 'ai' },
        { id: 'norm',    x: 49, y: 50, title: { ru: 'Нормализация', en: 'Normalize' }, sub: { ru: 'валюта · дедуп', en: 'fx · dedup' }, ico: 'stream' },
        { id: 'catalog', x: 68, y: 50, title: { ru: 'Каталог', en: 'Catalog' }, sub: { ru: 'БД · auto', en: 'DB · auto' }, ico: 'store' },
        { id: 'site',    x: 90, y: 24, title: { ru: 'Сайт', en: 'Site' }, sub: 'web', cls: 'out', ico: 'box' },
        { id: 'bot',     x: 90, y: 50, title: { ru: 'TG mini-app', en: 'TG mini-app' }, sub: 'bot', cls: 'out', ico: 'box' },
        { id: 'client',  x: 90, y: 78, title: { ru: 'Клиент', en: 'Buyer' }, sub: { ru: 'покупает', en: 'buys' }, cls: 'out', ico: 'agents' }
      ],
      edges: [['steam','sync'],['g2a','sync'],['kinguin','sync'],['sheet','sync'],['sync','norm'],['norm','catalog'],['catalog','site'],['catalog','bot'],['site','client'],['bot','client']]
    },
    /* современный мок мини-аппа магазина: каталог → «Купить» → выезжает ключ */
    media: { mode: 'app', app: 'store', ar: '3/4' }
  },

  /* ── SMM EASY — TG бот + mini-app генерации SMM (app) · ФЛАГМАН ────────── */
  {
    id: 'smmeasy',
    type: 'app',
    featured: false,
    frame: 'phone',
    title: { ru: 'AI-SMM на автопилоте', en: 'SMM on autopilot' },
    meta:  { ru: 'SMM EASY · 2026 · MINI-APP + AI', en: 'SMM EASY · 2026 · MINI-APP + AI' },
    summary: {
      ru: 'Telegram mini-app, который заменяет SMM-щика кафе и отелям: автоматизирует производство контента — тексты, карусели, voice-first reels — и его выкладывание по расписанию. Бизнес жмёт пару кнопок, дальше всё само.',
      en: 'A Telegram mini-app replacing an SMM manager for cafes and hotels: it automates content production — captions, carousels, voice-first reels — and its scheduled posting. The business taps a few buttons, the rest runs itself.'
    },
    chip: '×70 cheaper',
    stack: ['Telegram Mini-App', 'GPT-4', 'Image-gen', 'ElevenLabs TTS', 'Python'],
    metrics: [
      { value: 70, suffix: '×', label: { ru: 'дешевле SMM-щика', en: 'cheaper than an SMM' } },
      { value: 3,  label: { ru: 'формата контента', en: 'content formats' } },
      { value: 60, suffix: ' сек', label: { ru: 'на пост вместо часов', en: 'per post, not hours' } }
    ],
    /* запись реального интерфейса mini-app во всю рамку, усиленный контраст */
    media: { mode: 'video', video: 'assets/cases/smmeasy/demo.mp4', ar: '16/10', appSize: true, pop: true },
    graph: {
      nodes: [
        { id: 'biz',  x: 13, y: 50, title: { ru: 'Бизнес', en: 'Business' }, sub: { ru: 'кафе / отель', en: 'cafe / hotel' }, cls: 'src', ico: 'box' },
        { id: 'app',  x: 35, y: 24, title: 'Mini-app', sub: { ru: 'бриф в 3 тапа', en: '3-tap brief' }, cls: '', ico: 'stream' },
        { id: 'ai',   x: 52, y: 56, title: { ru: 'AI-движок', en: 'AI engine' }, sub: 'GPT-4 · img · TTS', cls: 'core', ico: 'ai' },
        { id: 'txt',  x: 80, y: 18, title: { ru: 'Тексты', en: 'Captions' }, sub: 'posts', cls: 'out', ico: 'box' },
        { id: 'crsl', x: 85, y: 50, title: { ru: 'Карусели', en: 'Carousels' }, sub: 'image-gen', cls: 'out', ico: 'box' },
        { id: 'reel', x: 80, y: 82, title: 'Reels', sub: { ru: 'voice-first', en: 'voice-first' }, cls: 'out', ico: 'stream' }
      ],
      edges: [['biz','app'],['app','ai'],['biz','ai'],['ai','txt'],['ai','crsl'],['ai','reel']]
    }
  },

  /* ── РАСПЕВКА — вокальный тренажёр (app) · LIVE · в паре с SMM ──────────── */
  {
    id: 'raspevka',
    type: 'app',
    featured: false,
    title: { ru: 'Тренажёр для вокалистов', en: 'Vocal trainer app' },
    meta:  { ru: 'РАСПЕВКА · 2026 · WEB APP / PWA', en: 'RASPEVKA · 2026 · WEB APP / PWA' },
    summary: {
      ru: 'Игровой вокальный тренажёр в браузере: ежедневные распевки, тюнер высоты ноты в реальном времени («видишь свой голос»), упражнения — мычание по гамме, губной тренаж, удержание ноты, гаммы — плюс дыхание и прогресс. Определяет диапазон голоса, работает с микрофона. Без установки — PWA.',
      en: 'A gamified in-browser vocal trainer: daily warm-ups, a real-time pitch tuner (“see your voice”), exercises — humming scales, lip trills, note holds, scales — plus breathing and progress. Detects your vocal range, runs off the mic. No install — a PWA.'
    },
    chip: 'live',
    stack: ['Web Audio API', 'Pitch detection', 'Canvas', 'Vite', 'PWA'],
    metrics: [
      { value: 5, label: { ru: 'упражнений распевки', en: 'warm-up exercises' } },
      { value: 0, suffix: ' сек', label: { ru: 'на установку', en: 'to install' } }
    ],
    /* мобильный mini-app: небольшой экран по центру + усиленный контраст */
    media: { mode: 'video', video: 'assets/cases/raspevka/demo.mp4', ar: '16/10', appSize: true, pop: true }
  },

  /* ── ПРОЯВИ — лендинг (web) · LIVE на GitHub Pages ─────────────────────── */
  {
    id: 'proyavi',
    type: 'web',
    featured: true,
    frame: 'browser',
    title: { ru: 'Лендинг вокальной школы', en: 'Vocal school landing' },
    meta:  { ru: 'ПРОЯВИ · 2026 · WEB', en: 'PROYAVI · 2026 · WEB' },
    summary: {
      ru: 'Продающий лендинг школы вокала и раскрытия голоса. Тёмная палитра plum-noir, кастомная типографика, анимации на GSAP. В проде на GitHub Pages.',
      en: 'A landing for a vocal & self-expression school. Plum-noir palette, custom type, GSAP animations. Live on GitHub Pages.'
    },
    chip: 'live',
    stack: ['HTML / CSS / JS', 'GSAP', 'GitHub Pages'],
    metrics: [],
    /* scroll-режим: лёгкая анимация — скриншот сам прокручивается в рамке (без live-ссылки) */
    media: { mode: 'video', video: 'assets/cases/proyavi/demo.mp4', ar: '1918/890' }
  },

  /* ── ПОМОЩНИК ВАСИЛИЙ — AI-ассистент отдела продаж (bot) · ФЛАГМАН ──────── */
  {
    id: 'vasiliy',
    type: 'bot',
    featured: true,
    frame: 'browser',
    title: { ru: 'AI-ассистент «Василий»', en: 'AI assistant "Vasiliy"' },
    meta:  { ru: 'ПОМОЩНИК ВАСИЛИЙ · 2026 · AI ASSISTANT', en: 'VASILIY · 2026 · AI ASSISTANT' },
    summary: {
      ru: 'B2B AI-ассистент, который не просто отвечает, а выполняет сложные задачи прямо в Telegram: поднимает лендинги, фиксирует важное, собирает рабочие системы под отдел продаж.',
      en: 'A B2B AI assistant that doesn’t just reply — it executes complex tasks right inside Telegram: spins up landings, captures what matters, assembles working systems for the sales team.'
    },
    chip: 'does the work',
    stack: ['GPT / LLM', 'Telegram', 'Python', 'Tool-use', 'Automations'],
    metrics: [
      { value: 4,  label: { ru: 'ниши под ключ', en: 'niches ready' } },
      { value: 24, suffix: '/7', label: { ru: 'на связи', en: 'online' } },
      { value: 0,  suffix: ' мин', label: { ru: 'ожидания ответа', en: 'reply wait' } }
    ],
    /* карточка — запись лендинга; deep-dive — мок как бот выполняет задачи */
    media: { mode: 'video', video: 'assets/cases/vasiliy/demo.mp4', ar: '1920/892' },
    task: [
      { from: 'user', text: { ru: 'Сделай контент-план на неделю', en: 'Make a weekly content plan' } },
      { from: 'bot',  text: { ru: 'Делаю.', en: 'On it.' } },
      { from: 'sys',  text: { ru: '⚙ анализ ниши + темы… ✓', en: '⚙ niche + topics… ✓' } },
      { from: 'sys',  text: { ru: '📅 7 постов + форматы… ✓', en: '📅 7 posts + formats… ✓' } },
      { from: 'bot',  text: { ru: 'Готово — план в канале.', en: 'Done — plan posted to the channel.' } },
      { from: 'user', text: { ru: 'Создай инфо-рилс про новый закон', en: 'Make an explainer reel about the new law' } },
      { from: 'sys',  text: { ru: '✍️ сценарий + раскадровка… ✓', en: '✍️ script + storyboard… ✓' } },
      { from: 'sys',  text: { ru: '🎬 монтаж + озвучка… ✓', en: '🎬 edit + voiceover… ✓' } },
      { from: 'bot',  text: { ru: '🎞 Reel готов, выложил.', en: '🎞 Reel ready, posted.' } },
      { from: 'user', text: { ru: 'Проанализируй конкурентов', en: 'Analyze the competitors' } },
      { from: 'sys',  text: { ru: '🔎 5 конкурентов · цены · офферы… ✓', en: '🔎 5 rivals · pricing · offers… ✓' } },
      { from: 'bot',  text: { ru: '📊 Отчёт со слабыми местами — прислал.', en: '📊 Report with their weak spots — sent.' } },
      { from: 'user', text: { ru: 'Подними лендинг под акцию к пятнице', en: 'Spin up a promo landing by Friday' } },
      { from: 'sys',  text: { ru: '⚙ структура + вёрстка + анимации… ✓', en: '⚙ structure + markup + animations… ✓' } },
      { from: 'bot',  text: { ru: '🚀 Готово. Лендинг в проде, ссылку отправил.', en: '🚀 Done. Landing is live, link sent.' } }
    ]
  },

  /* ── ЛИД-БОТ + CRM — карточка клиента едет по воронке (bot) ─────────────── */
  {
    id: 'leadbot',
    type: 'bot',
    featured: false,
    title: { ru: 'Лид-бот + CRM', en: 'Lead bot + CRM' },
    meta:  { ru: 'DEADLINE · 2026 · BOT + CRM', en: 'DEADLINE · 2026 · BOT + CRM' },
    summary: {
      ru: 'Бот ловит лидов со всех каналов и ведёт каждого по воронке в CRM: диалог → квалификация → продажа → оплата → отзыв. Карточка клиента двигается сама, ни один лид не теряется.',
      en: 'The bot catches leads from every channel and drives each through the CRM funnel: chat → qualified → offer → paid → review. The client card moves itself, no lead is lost.'
    },
    chip: '0 lost leads',
    stack: ['Telegram', 'GPT', 'CRM', 'PostgreSQL', 'Cron'],
    metrics: [
      { value: 0,  label: { ru: 'потерянных лидов', en: 'lost leads' } },
      { value: 24, suffix: '/7', label: { ru: 'на связи', en: 'online' } }
    ],
    media: {
      mode: 'chat',
      chat: [
        { from: 'user', text: { ru: 'Здравствуйте, интересует бот под запись', en: 'Hi, interested in a booking bot' } },
        { from: 'bot',  text: { ru: 'Привет! Подскажу за минуту. Сколько заявок в день и куда приходят?', en: 'Hi! One quick question — how many leads a day and where?' } },
        { from: 'user', text: { ru: '~30, в директ', en: '~30, into DM' } },
        { from: 'bot',  text: { ru: 'Понял. Соберём под ключ, веду вас в CRM. Оставьте контакт — менеджер свяжется.', en: 'Got it. Turnkey build, you’re in our CRM now. Drop a contact — a manager will reach out.' } },
        { from: 'user', text: { ru: '@anna', en: '@anna' } },
        { from: 'bot',  text: { ru: '✅ Записал. Карточка создана, задача поставлена — не потеряетесь.', en: '✅ Logged. Card created, task set — you won’t be lost.' } }
      ]
    },
    kanban: {
      client: { ru: 'Анна · лид', en: 'Anna · lead' },
      value: '80 000 ₽',
      stages: [
        { emoji: '🆕', label: { ru: 'Новый', en: 'New' },             pop: { ru: 'лид пойман',          en: 'lead caught' } },
        { emoji: '💬', label: { ru: 'Диалог', en: 'Chat' },           pop: { ru: 'ответ за 0 сек',      en: '0-sec reply' } },
        { emoji: '✅', label: { ru: 'Квалификация', en: 'Qualified' }, pop: { ru: 'узнал бюджет',        en: 'budget captured' } },
        { emoji: '💰', label: { ru: 'Продажа', en: 'Offer' },         pop: { ru: 'оффер отправлен',     en: 'offer sent' } },
        { emoji: '💵', label: { ru: 'Оплата', en: 'Paid' },           pop: { ru: 'деньги поступили',    en: 'payment received' } },
        { emoji: '⭐', label: { ru: 'Отзыв', en: 'Review' },          pop: { ru: 'отзыв 5★ — спасибо!', en: '5★ review — thanks!' } }
      ]
    }
  },

  /* ── АВТО-РЕСЁРЧ-БОТ — контент-автопилот (bot) ─────────────────────────── */
  {
    id: 'research',
    type: 'bot',
    featured: false,
    title: { ru: 'Контент-автопилот', en: 'Content autopilot' },
    meta:  { ru: 'AUTO-RESEARCH BOT · 2026 · CONTENT', en: 'AUTO-RESEARCH BOT · 2026 · CONTENT' },
    summary: {
      ru: 'Бот сам ищет в интернете свежие новости и важное по теме, переписывает в нужном стиле и автоматически постит в Telegram-канал. На каждую тему делает карусели и reels — и выкладывает в Instagram. Контент без рук.',
      en: 'The bot researches the web for fresh news on a topic, rewrites it in your voice and auto-posts to a Telegram channel. For each topic it builds carousels and reels — and publishes to Instagram. Hands-free content.'
    },
    chip: 'hands-free',
    stack: ['Search API', 'GPT', 'Image-gen', 'TTS', 'Telegram', 'Instagram API'],
    metrics: [
      { value: 24, suffix: '/7', label: { ru: 'мониторинг', en: 'monitoring' } },
      { value: 3,  label: { ru: 'формата контента', en: 'content formats' } },
      { value: 0,  suffix: ' мин', label: { ru: 'ручной работы', en: 'manual work' } }
    ],
    media: {
      mode: 'chat',
      chat: [
        { from: 'bot', text: { ru: '🔎 Нашёл: новый закон о маркировке — горячая тема', en: '🔎 Found: new labeling law — hot topic' } },
        { from: 'bot', text: { ru: '📰 Переписал в наш стиль и выложил в канал ✓', en: '📰 Rewrote in our voice and posted to the channel ✓' } },
        { from: 'bot', text: { ru: '🖼 Собрал карусель из 6 слайдов ✓', en: '🖼 Built a 6-slide carousel ✓' } },
        { from: 'bot', text: { ru: '🎬 Смонтировал Reel с озвучкой → Instagram ✓', en: '🎬 Edited a voiced Reel → Instagram ✓' } }
      ]
    },
    graph: {
      nodes: [
        { id: 'news',    x: 9,  y: 26, title: { ru: 'Новости', en: 'News' }, sub: 'RSS / web', cls: 'src', logo: { t: 'RSS', c: '#ff6b00' } },
        { id: 'search',  x: 9,  y: 72, title: { ru: 'Поиск', en: 'Search' }, sub: 'research', cls: 'src', logo: { t: 'Q', c: '#4285f4' } },
        { id: 'ai',      x: 35, y: 50, title: 'AI', sub: { ru: 'rewrite + тема', en: 'rewrite + topic' }, cls: 'core', ico: 'ai' },
        { id: 'channel', x: 62, y: 24, title: { ru: 'TG-канал', en: 'TG channel' }, sub: { ru: 'автопост', en: 'auto-post' }, cls: 'out', logo: { t: 'TG', c: '#229ed9' } },
        { id: 'gen',     x: 60, y: 72, title: { ru: 'Генератор', en: 'Generator' }, sub: { ru: 'карусели + reels', en: 'carousels + reels' }, ico: 'box' },
        { id: 'ig',      x: 88, y: 72, title: 'Instagram', sub: { ru: 'автопост', en: 'auto-post' }, cls: 'out', logo: { t: 'IG', c: '#e1306c' } }
      ],
      edges: [['news','ai'],['search','ai'],['ai','channel'],['ai','gen'],['gen','ig']]
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
