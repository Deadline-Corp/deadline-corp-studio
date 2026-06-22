"""Demo sandbox — тестовые данные, чтобы потыкать систему без реального трафика.

Онбординг/обучение: «Наполнить демо-данными» создаёт правдоподобных лидов
(разные каналы/стадии/температуры/переписки) + задачи. Всё помечено
profile_data.demo=true — «Очистить демо» удаляет ТОЛЬКО их (каскад уносит
диалоги/сообщения/задачи), реальные лиды не затрагиваются ни при каких
условиях.

Безопасность: у демо-диалогов channel_conversation_id=None → ни одно
действие (ответ оператора, бот-пинг, автоматизация) не уйдёт во внешний
мессенджер — write_scheduled_action без chat_id делает executor='human',
deliver_operator_reply без id канала падает в website-ветку (только БД).
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# (имя, email, канал, стадия, температура, скор, [(role, text), ...], часов_назад)
_DEMO_LEADS = [
    ("Анна Морозова", "demo-anna@example.com", "website", "new_lead", "warm", 35, [
        ("user", "Здравствуйте! Хочу лендинг для студии йоги, что по срокам?"),
        ("assistant", "Здравствуйте, Анна! Лендинг — наша тема, обычно 1-2 недели. Расскажите чуть подробнее: нужна запись на занятия прямо с сайта?"),
        ("user", "Да, запись очень нужна. И оплата абонементов онлайн."),
    ], 3),
    ("Игорь Севастьянов", "demo-igor@example.com", "telegram", "in_dialog", "hot", 65, [
        ("user", "Привет! Нужен бот для записи клиентов в барбершоп"),
        ("assistant", "Привет, Игорь! Делали похожее — бот с записью, напоминаниями и оплатой. Сколько у вас мастеров и точек?"),
        ("user", "2 точки, 6 мастеров. Бюджет до 100к"),
        ("assistant", "Отлично вписываемся. Предлагаю созвон на 15 минут с командой — покажем кейс барбершопа. Завтра в 14:00 удобно?"),
    ], 26),
    ("Мария Ким", "demo-maria@example.com", "telegram", "qualified", "hot", 80, [
        ("user", "Добрый день, мне порекомендовали вас. Интернет-магазин косметики, нужен сайт + CRM"),
        ("assistant", "Добрый день, Мария! Спасибо за доверие 🙂 Магазин + CRM — делаем под ключ. Какой примерно ассортимент?"),
        ("user", "Около 400 позиций. maria-demo@example.com — пришлите КП"),
        ("assistant", "Передал команде! Ребята подготовят расчёт и напишут вам сегодня."),
    ], 50),
    ("Денис Артёмов", "demo-denis@example.com", "instagram", "on_call", "ready", 90, [
        ("user", "Видел ваш кейс с доставкой еды, у меня похожий проект"),
        ("assistant", "Денис, привет! Да, тот проект вырос x3 по заказам после автоматизации. Давайте созвонимся — расскажете детали?"),
        ("user", "Давайте в пятницу в 12 по Москве"),
        ("assistant", "Записал: пятница 12:00 мск. За день и за час напомню. До связи!"),
    ], 8),
    ("Ольга Лебедева", "demo-olga@example.com", "website", "proposal", "warm", 70, [
        ("user", "Получила ваше КП по корпоративному порталу. Дороговато выходит..."),
        ("assistant", "Ольга, понимаю. Давайте посмотрим, что можно вынести во вторую очередь — база и кабинет сотрудника останутся, а модуль аналитики добавим после запуска. Так выйдет ощутимо легче по бюджету."),
    ], 75),
    ("Тимур Хасанов", "demo-timur@example.com", "telegram", "lost", "cold", 20, [
        ("user", "сколько стоит приложение типа убера?"),
        ("assistant", "Тимур, привет! Маркетплейс-приложение — это от $5k и 2-3 месяца. Какой у вас бюджет и сроки?"),
        ("user", "не, мне надо за 50 тысяч рублей и за неделю"),
        ("assistant", "Честно: за этот бюджет полноценный аналог не собрать. Могу предложить MVP-вариант на конструкторе — если интересно, расскажу."),
    ], 200),
    ("Светлана Дёмина", "demo-sveta@example.com", "website", "in_dialog", "warm", 45, [
        ("user", "Нужна автоматизация заявок: сейчас всё в excel, менеджеры тонут"),
        ("assistant", "Светлана, знакомая боль 🙂 Обычно решаем связкой: форма → CRM → автораспределение на менеджеров + напоминания. Сколько заявок в день у вас?"),
        ("user", "Порядка 60-80 в сезон"),
    ], 30),
]

_DEMO_TASKS = [
    # (индекс лида, текст, executor, через_часов)
    (1, "Подготовить кейс барбершопа к созвону", "human", 4),
    (3, "Созвон с Денисом — пятница 12:00 (подтвердить за день)", "human", 20),
    (4, "Дожать Ольгу: предложить поэтапный план оплаты", "human", -2),  # просрочена
]


def seed_demo(db) -> dict:
    """Создать демо-набор. Идемпотентно: если демо уже есть — сначала чистим."""
    from db.models import (
        Customer, Conversation, Message, ScheduledAction, StageTransition,
    )

    cleared = clear_demo(db)

    created = {"customers": 0, "conversations": 0, "messages": 0, "tasks": 0}
    now = datetime.now(timezone.utc)
    conv_by_idx: list = []

    for i, (name, email, channel, stage, temp, score, msgs, hours_ago) in enumerate(_DEMO_LEADS):
        cust = Customer(
            name=name, email=email,
            first_channel=channel,
            lead_score=score, lead_temperature=temp,
            profile_data={"demo": True, "fields": {}},
        )
        db.add(cust)
        db.flush()
        conv = Conversation(
            customer_id=cust.id, channel=channel,
            channel_conversation_id=None,  # КРИТИЧНО: никаких реальных отправок
            status="open", lead_stage=stage,
            lost_reason="price" if stage == "lost" else None,
        )
        db.add(conv)
        db.flush()
        t0 = now - timedelta(hours=hours_ago)
        for j, (role, text) in enumerate(msgs):
            m = Message(conversation_id=conv.id, role=role, content=text,
                        extra_meta={"demo": True})
            db.add(m)
            db.flush()
            m.created_at = t0 + timedelta(minutes=j * 7)
        conv.last_message_at = t0 + timedelta(minutes=len(msgs) * 7)
        conv.created_at = t0
        if stage != "new_lead":
            db.add(StageTransition(
                conversation_id=conv.id, customer_id=cust.id,
                from_stage="new_lead", to_stage=stage, by="bot",
            ))
        conv_by_idx.append(conv)
        created["customers"] += 1
        created["conversations"] += 1
        created["messages"] += len(msgs)

    for lead_idx, text, executor, due_h in _DEMO_TASKS:
        conv = conv_by_idx[lead_idx]
        db.add(ScheduledAction(
            customer_id=conv.customer_id, conversation_id=conv.id,
            channel=conv.channel, chat_id=None,
            action_type="operator_callback", executor=executor,
            due_at=now + timedelta(hours=due_h), status="pending",
            payload={"text": text, "by": "demo", "demo": True},
        ))
        created["tasks"] += 1

    db.flush()
    logger.info("demo_seed: created %s (cleared before: %s)", created, cleared)
    return {"created": created, "cleared_before": cleared}


def clear_demo(db) -> dict:
    """Удалить ТОЛЬКО демо-данные (profile_data.demo=true). Каскад FK уносит
    диалоги/сообщения/identities; задачи демо-клиентов — каскадом по customer.
    Реальные лиды не затрагиваются: фильтр строго по метке demo."""
    from db.models import Customer

    demo_customers = (
        db.query(Customer)
        .filter(Customer.profile_data["demo"].as_boolean() == True)  # noqa: E712
        .all()
    )
    n = len(demo_customers)
    for c in demo_customers:
        db.delete(c)  # ON DELETE CASCADE: identities, conversations→messages, scheduled_actions
    db.flush()
    if n:
        logger.info("demo_seed: cleared %d demo customers", n)
    return {"customers": n}


def demo_count(db) -> int:
    from db.models import Customer
    return (
        db.query(Customer)
        .filter(Customer.profile_data["demo"].as_boolean() == True)  # noqa: E712
        .count()
    )
