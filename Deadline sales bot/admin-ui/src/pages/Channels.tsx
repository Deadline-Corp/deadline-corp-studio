import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useOverview } from '../overviewContext'
import { CHANNEL_META, fmtAgo } from '../lib'
import { HintBar } from '../components/HintBar'

/* Каналы: карточки подключения в стиле «подключи за N шагов» (паттерн
   Kommo/Chatwoot). Статус из overview; инструкции — пошаговые раскрывашки.
   Данные по WhatsApp-провайдерам — ресёрч 2026-06. */

function Steps({ items }: { items: Array<string | JSX.Element> }) {
  return (
    <ol style={{ margin: '8px 0 0', paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
      {items.map((s, i) => <li key={i}>{s}</li>)}
    </ol>
  )
}

function Card({ icon, title, status, statusCls, children, footer }: {
  icon: string; title: string; status: string; statusCls: string
  children: React.ReactNode; footer?: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ width: 36, height: 36, borderRadius: 9, background: 'var(--panel-2)', display: 'grid', placeItems: 'center', fontSize: 18 }}>{icon}</div>
        <div style={{ flex: 1 }}>
          <b>{title}</b>
        </div>
        <span className={`chip ${statusCls}`}>{status}</span>
      </div>
      {footer}
      <button className="btn sm ghost" style={{ alignSelf: 'flex-start' }} onClick={() => setOpen(v => !v)}>
        {open ? '▾ Скрыть инструкцию' : '▸ Как подключить / настроить'}
      </button>
      {open && <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>{children}</div>}
    </div>
  )
}

export function Channels() {
  const ov = useOverview()
  const navigate = useNavigate()

  const ch = (id: string) => ov?.channels.find(c => c.id === id)
  const statusOf = (id: string) =>
    ch(id)?.configured
      ? { s: '✅ подключён', cls: 'ok' }
      : { s: 'не подключён', cls: '' }

  const counts = (id: string) => {
    const c = ch(id)
    if (!c?.configured) return null
    return (
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span className="chip">{c.conversations} диалогов</span>
        <span className="chip">{c.open} открыто</span>
        {c.last_message_at && <span className="chip">активность {fmtAgo(c.last_message_at)} назад</span>}
        <button className="btn sm ghost" onClick={() => navigate(`/inbox?channel=${id}`)}>переписки →</button>
      </div>
    )
  }

  const mono: React.CSSProperties = { fontFamily: 'var(--mono)', fontSize: 11.5, background: 'var(--bg-soft)', padding: '2px 6px', borderRadius: 4 }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Каналы</h1>
        <span className="sub">откуда бот принимает лидов и как подключить новые</span>
      </div>

      <HintBar id="channels" icon="🔌">
        Каналы — откуда бот принимает клиентов. Зелёный бейдж = работает. Чтобы подключить
        новый — раскройте «Как подключить» на карточке, там пошаговая инструкция без айтишных сложностей.
      </HintBar>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 14 }}>

        <Card icon="🌐" title="Сайт-виджет" {...{ status: statusOf('website').s, statusCls: statusOf('website').cls }} footer={counts('website')}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>Уже работает на deadlinecorp.com. Чтобы поставить на другой сайт:</p>
          <Steps items={[
            <span>Вставьте перед <span style={mono}>&lt;/body&gt;</span>: <span style={mono}>&lt;script src="https://deadlinecorp.com/widget.js" defer&gt;&lt;/script&gt;</span></span>,
            <span>Добавьте домен сайта в <span style={mono}>ALLOWED_ORIGINS</span> в Railway → Variables (через запятую) и редеплойте</span>,
            'Готово — лиды с виджета появятся в «Переписках» с иконкой 🌐',
          ]} />
        </Card>

        <Card icon="✈️" title="Telegram" {...{ status: statusOf('telegram').s, statusCls: statusOf('telegram').cls }} footer={counts('telegram')}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>Бот в TG + операторская группа (перехват диалогов). Для нового бота/аккаунта:</p>
          <Steps items={[
            <span>В <b>@BotFather</b>: /newbot → получите токен → в Railway положите <span style={mono}>TELEGRAM_BOT_TOKEN</span></span>,
            <span>Сгенерируйте секрет (<span style={mono}>openssl rand -hex 32</span>) → <span style={mono}>TELEGRAM_WEBHOOK_SECRET</span></span>,
            <span>Зарегистрируйте вебхук: <span style={mono}>curl "https://api.telegram.org/bot&lt;TOKEN&gt;/setWebhook" --data-urlencode url="https://deadline-sales-bot-production.up.railway.app/webhooks/telegram" --data-urlencode secret_token="&lt;секрет&gt;"</span></span>,
            <span>Операторская группа: создайте супергруппу с Topics, добавьте бота админом (право Manage Topics), id группы → <span style={mono}>TELEGRAM_OPERATOR_GROUP_ID</span></span>,
            'Редеплой — и каждый лид получает свою тему в группе, перехват кнопкой «Возьму на себя» или прямо отсюда из «Переписок»',
          ]} />
        </Card>

        <Card icon="📸" title="Instagram (DM + комментарии)" {...{ status: statusOf('instagram').s, statusCls: statusOf('instagram').cls }} footer={counts('instagram')}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            Код в боте уже готов (DM + автоответы на комменты) — нужно только подключение Meta. Чеклист (актуален на 2026):
          </p>
          <Steps items={[
            'Instagram переводится в Professional (Business/Creator) и привязывается к Facebook-странице',
            <span>На <a href="https://developers.facebook.com" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>developers.facebook.com</a> создаётся приложение типа Business</span>,
            <span>Permissions: <span style={mono}>instagram_basic</span>, <span style={mono}>instagram_manage_comments</span>, <span style={mono}>instagram_business_manage_messages</span> — до App Review можно тестировать на 25 тест-юзерах</span>,
            <span>Webhooks → Instagram: URL <span style={mono}>…/webhooks/instagram</span>, верификация по <span style={mono}>META_VERIFY_TOKEN</span></span>,
            <span>В Railway: <span style={mono}>META_VERIFY_TOKEN</span>, <span style={mono}>META_APP_SECRET</span>, <span style={mono}>META_PAGE_ACCESS_TOKEN</span></span>,
            'App Review (недели) — после него работа с реальными подписчиками. Лимит: 200 авто-DM/час, ответ в DM — в 24ч-окне',
          ]} />
        </Card>

        <Card icon="💬" title="Facebook Messenger" {...{ status: statusOf('messenger').s, statusCls: statusOf('messenger').cls }} footer={counts('messenger')}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>Идёт в комплекте с Instagram — то же Meta-приложение и тот же Page Access Token:</p>
          <Steps items={[
            'Выполните подключение Instagram (карточка выше) — Messenger использует те же ключи',
            <span>В Meta App включите продукт Messenger, подпишите вебхук на <span style={mono}>…/webhooks/messenger</span></span>,
            'Сообщения и комментарии со страницы FB начнут попадать в «Переписки»',
          ]} />
        </Card>

        <Card icon="🟢" title="WhatsApp (Cloud API)" {...{ status: statusOf('whatsapp').s, statusCls: statusOf('whatsapp').cls }} footer={counts('whatsapp')}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            ✅ Коннектор готов в боте — приём и ответы тем же мозгом, что в Telegram. Подключаем
            официальным <b>WhatsApp Cloud API</b> от Meta: платформа бесплатна, без риска бана
            (в отличие от «серых» QR-сервисов). Шаги:
          </p>
          <Steps items={[
            <span>На <a href="https://developers.facebook.com" target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>developers.facebook.com</a> создайте приложение типа Business → добавьте продукт WhatsApp</span>,
            <span>Привяжите номер (которого нет в обычном WhatsApp) и пройдите <b>Business Verification</b> — 1–7 дней, начните первым</span>,
            <span>Webhook → URL <span style={mono}>…/webhooks/whatsapp</span>, Verify Token — любая строка; подпишитесь на поле <span style={mono}>messages</span></span>,
            <span>В Railway → Variables: <span style={mono}>WHATSAPP_TOKEN</span> (permanent System User), <span style={mono}>WHATSAPP_PHONE_NUMBER_ID</span>, <span style={mono}>WHATSAPP_VERIFY_TOKEN</span>, <span style={mono}>WHATSAPP_APP_SECRET</span> (или общий <span style={mono}>META_APP_SECRET</span>)</span>,
            'Редеплой — входящие появятся в «Переписках» 🟢. Исходящие/реактивация вне 24ч-окна — только через одобренный шаблон + согласие клиента (иначе бан).',
          ]} />
        </Card>

        <Card icon="🗂" title="HubSpot CRM (зеркало)" status={ov?.crm.enabled ? '✅ включена' : 'выключена'} statusCls={ov?.crm.enabled ? 'ok' : ''}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            Главная воронка теперь живёт здесь, в панели. HubSpot — опциональное зеркало:
            встроенные стадии и контакты дублируются туда автоматически (очередь, не блокирует бота).
            Кастомные стадии — только в нашей воронке. Отключение/включение — флаг
            <span style={mono}> CRM_ENABLED</span> в Railway.
          </p>
        </Card>

      </div>
    </div>
  )
}
