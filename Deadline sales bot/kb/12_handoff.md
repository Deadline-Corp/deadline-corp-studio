# Handoff — передача лида команде / Lead handoff to team

## RU

### Когда бот делает handoff

Бот переключает лида на команду (Telegram @deadline_corp + email corpdeadline@gmail.com), когда:

1. **Brief собран:** известны тип проекта + краткое описание задачи + (по возможности) сроки + контакт
2. **Лид прямо просит человека:** «свяжите с человеком», «нужен звонок», «давайте созвонимся»
3. **Бот не знает ответа** с уверенностью (галлюцинировать запрещено)
4. **Запрос вне scope:** native mobile, performance-маркетинг, дизайн без разработки → перенаправление с честным «это не наш формат»
5. **Срочность:** «горит дедлайн», «нужно вчера», «сделайте до пятницы»
6. **Цена / контракт / NDA:** любые финансовые вопросы и юридическое — только люди

### Формат brief'а для команды

Бот собирает и отправляет команде:

```
🆕 НОВЫЙ ЛИД

Тип: [Web / Automation / AI Agents / Mixed / Unknown]
Описание задачи: [1-2 предложения]
Срок: [если упомянут, иначе "не указан"]
Индустрия / контекст: [если упомянуто]
Контакт лида: [Telegram / email / имя, если оставил]
Срочность: [Normal / Urgent / Burning]
Первый запрос лида: [исходное сообщение]

[Полный диалог ниже]
```

### Что бот говорит лиду в момент handoff

- (RU) «Передал команде. Ответ в Telegram @deadline_corp в течение минут. 📩»
- (EN) «Passed to the team. Reply in Telegram @deadline_corp within minutes. 📩»

### После handoff бот делает

- Продолжает быть доступен в чате
- Но больше не задаёт уточняющих вопросов
- Если лид пишет что-то ещё — отправляет это команде следующим сообщением
- Не пытается «дозакрыть» лида сам

## EN

### When the bot triggers handoff

The bot passes the lead to the team (Telegram @deadline_corp + email corpdeadline@gmail.com) when:

1. **Brief is collected:** project type + short task description + (if possible) timeline + contact known
2. **Lead asks for a human directly:** "connect me to a human", "need a call", "let's jump on a call"
3. **Bot doesn't know the answer confidently** (hallucination forbidden)
4. **Out-of-scope request:** native mobile, performance marketing, design without dev → redirect with honest "not our format"
5. **Urgency:** "deadline burning", "needed yesterday", "by Friday"
6. **Price / contract / NDA:** anything financial or legal — humans only

### Handoff brief format

```
🆕 NEW LEAD

Type: [Web / Automation / AI Agents / Mixed / Unknown]
Task description: [1-2 sentences]
Timeline: [if mentioned, otherwise "not specified"]
Industry / context: [if mentioned]
Lead contact: [Telegram / email / name, if shared]
Urgency: [Normal / Urgent / Burning]
Lead's first message: [original]

[Full conversation below]
```

### What the bot says to the lead at handoff

- (RU) "Передал команде. Ответ в Telegram @deadline_corp в течение минут. 📩"
- (EN) "Passed to the team. Reply in Telegram @deadline_corp within minutes. 📩"

### After handoff the bot

- Remains available in the chat
- Stops asking clarifying questions
- If the lead writes more — forwards it as a follow-up message to the team
- Does NOT try to "close" the lead on its own
