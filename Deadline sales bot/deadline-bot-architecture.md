# Deadline Website Bot — Архитектурный анализ и рекомендация

*Дата: 17 мая 2026 · Источники: Perplexity-style market scan + technical deep-dive + анализ сайта deadline-corp.github.io*

---

## TL;DR — Жёстко и по делу

1. **Твой исходный план провальный по трём осям.** GLM-5.1 локально не существует (только `:cloud`); RTX 3070 8GB не вытянет два инстанса; синтетический sales-датасет — не про твой домен и не про support.
2. **Это не support-бот, это lead-qualification-бот.** Deadline — студия с услугой "AI Agents" и кейсами в продакшене. Бот = витрина компетенции + воронка для входящих лидов, а не help-desk.
3. **Архитектура решена индустрией: RAG, не fine-tune.** В 2026 production-стандарт для cold-start customer-facing ботов — Agentic RAG с гибридным retrieval'ом. Fine-tune без 500+ реальных примеров — антипаттерн.
4. **Self-play "два инстанса болтают" — недоразумение.** Это путаница с SPIN/Self-Rewarding LLMs, которые требуют gradient updates + размеченные gold answers. У GLM-5.1:cloud нет fine-tuning API → SPIN физически невозможен.
5. **Реальная стоимость MVP — $30-60/мес.** На вашем трафике хватает Ollama Pro ($20) + pgvector (бесплатно) + self-hosted Chatwoot ($10-20 VPS). Запуск за 1-2 недели.
6. **7 ГБ датасета — выбросить или законсервировать.** 6.4 ГБ из 7 — это OpenAI-эмбеддинги (бесполезны для другой модели), осталки — B2B SaaS sales, не dev studio. Если очень хочется — взять 0.5% как "стилевые примеры тона", остальное в архив.
7. **Risk-кейс: репутационный.** Если бот на сайте Deadline тупит / лагает / галлюцинирует цены — пропадает доверие к услуге #3 "AI Agents". Стандарт качества тут выше, чем у обычного support-бота.

---

## 1. Переформулировка задачи

### Что ты сказал
> Прикрутить к сайту Deadline бота, обучить на 7 ГБ переписок, поднять два локальных инстанса GLM-5 для self-play.

### Что на самом деле нужно
**Conversational lead-qualification агент** для входящего трафика на deadline-corp.github.io, который:

| Задача | Подзадачи |
|---|---|
| **Квалификация лида** | Тип проекта (Web / Automation / AI Agents) · Срочность · Бюджетный диапазон · Размер компании |
| **Подтверждение релевантности** | Подходит ли задача Deadline (берут "всё", но есть лимиты по стеку и срокам) |
| **Презентация кейсов** | Подтянуть релевантный кейс из 12+ projects (VRP / KeyDrop / RA Project) под запрос лида |
| **FAQ по процессу** | Discovery → Architecture → Sprint Build → Handoff; политика "не окупилось → переделаем" |
| **Capture & handoff** | Email + Telegram → передача в `@deadline_corp` с сжатым brief'ом |
| **Витрина** | Сам бот должен звучать как продукт Deadline: bilingual RU/EN, минимализм, без воды |

Это **не customer support**. Это inbound BDR/SDR.

---

## 2. Сравнение четырёх архитектур — таблица решений

| Параметр | Pure RAG | RAG + SFT | RLAIF | Self-play (как ты описал) |
|---|---|---|---|---|
| Готовность к продакшену | **Высокая** — индустриальный стандарт 2026 | Средняя — нужны данные | Низкая — нужен reward model | **Не применимо** — не метод обучения |
| Стоимость setup | $0 (только инфра) | $3-29 (one-shot QLoRA) | $$$ (annotation + infra) | Бессмысленна |
| Стоимость в проде | retrieval + LLM | то же | то же | то же |
| Latency | +50-200ms на retrieval | baseline | baseline | baseline |
| Hallucination risk | **Низкий** — grounded в доках | Средний (зависит от данных) | Низкий при хорошей реализации | **Высокий** — нет grounding |
| Нужны данные | Только product docs + кейсы | 500+ качественных примеров | Preference pairs + reward | Размеченные gold answers (всё равно) |
| Time to value | **2-5 дней** | 2-4 недели | 2-3 месяца | N/A |
| Подходит Deadline сейчас | **ДА** | Через 6-12 мес после накопления реальных диалогов | Нет, преждевременно | **Нет, никогда** в такой формулировке |

### Почему self-play в исходной формулировке невозможен

> "Один как клиент, другой как чат-бот сайта. И чтобы один спрашивал другой, чтобы таким образом обучать его."

Это смесь двух разных вещей в одну неработающую:

1. **SPIN (Self-Play Fine-Tuning, Chen et al. 2024, arXiv:2401.01335)** — это *алгоритм обучения*, не runtime-архитектура. Требует: (а) gradient updates на каждой итерации, (б) фиксированный размеченный человеком dataset как gold target, (в) множество тренировочных циклов. У GLM-5.1:cloud **нет fine-tuning API** — Ollama Cloud отдаёт только инференс. Gradient-доступа нет → SPIN невозможен в принципе.

2. **Self-Rewarding LMs (Yuan et al. 2024, arXiv:2401.10020)** — модель оценивает свои же ответы через LLM-as-judge и тренируется на этих preference. Документированный failure mode: **representational convergence** — после нескольких итераций "chosen" и "rejected" ответы сходятся, score gap падает в 9 раз, DPO-градиент исчезает.

3. **Без gradient updates** два инстанса, общающиеся друг с другом — это просто **синтетическая генерация диалогов**, а не обучение. И эта синтетика будет плохой, потому что обе модели тянут из одного распределения.

**Что из этого можно спасти:** ты можешь использовать одну сильную модель (Claude/GPT-4) как генератор синтетических Q&A для RAG-evaluation set'а. Это работает и широко применяется. Это не "self-play" — это distillation для тестовых данных.

---

## 3. Рекомендуемая архитектура для Deadline

```
┌─────────────────────────────────────────────────────────────┐
│  deadline-corp.github.io (статический сайт на GitHub Pages) │
│                                                             │
│  ┌──────────────┐                                           │
│  │ Chat Widget  │ ◄──── JS embed (websocket → backend)      │
│  └──────┬───────┘                                           │
└─────────┼───────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│  Self-hosted backend (VPS, ~$10-20/мес)                     │
│                                                             │
│  ┌──────────────────────────────────────────────┐           │
│  │ Chatwoot (inbox + widget) ИЛИ Botpress       │           │
│  └────────────────┬─────────────────────────────┘           │
│                   │                                         │
│         ┌─────────┴──────────┐                              │
│         ▼                    ▼                              │
│  ┌─────────────┐    ┌──────────────────┐                    │
│  │ LangGraph   │    │ Logging / Eval   │                    │
│  │ orchestrator│    │ (RAGAS / Phoenix)│                    │
│  └──────┬──────┘    └──────────────────┘                    │
│         │                                                   │
│  ┌──────┴───────────────────────────┐                       │
│  │  Retrieval (hybrid)              │                       │
│  │  • pgvector (Postgres) - dense   │                       │
│  │  • BM25 (Postgres GIN) - keyword │                       │
│  │  • bge-reranker - rerank         │                       │
│  └──────┬───────────────────────────┘                       │
│         │                                                   │
└─────────┼───────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│  Ollama Cloud · GLM-5.1:cloud · Pro plan ($20/mo) или Max   │
│                                                             │
│  System prompt (ToV Deadline) + RAG context + user query    │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│  Escalation triggers:                                       │
│  • Low confidence → Telegram @deadline_corp                 │
│  • Lead qualified → Email corpdeadline@gmail.com            │
│  • After hours → "Reply in minutes" сохраняется             │
└─────────────────────────────────────────────────────────────┘
```

### Конкретный стек

| Слой | Выбор | Почему | Альтернатива |
|---|---|---|---|
| **LLM** | GLM-5.1:cloud · Pro $20/мес | Раз ты уже выбрал, и MoE 754B даёт качество. **Но:** не SLA-продукт, есть rate limits | OpenRouter (Claude 3.5 Haiku ~$0.25/M токенов) — дешевле и SLA лучше |
| **Vector DB** | pgvector | Постгрес уже наверняка есть в инфре студии. Бесплатно, простой ops | Qdrant Cloud ($65/мес) если не любишь Postgres |
| **Reranker** | bge-reranker-v2-m3 (локально на CPU) | Бесплатно, ~50ms на запрос | Cohere Rerank API ($1/1k запросов) |
| **Orchestration** | LangGraph | Stateful многотурновые сценарии, явный flow | LlamaIndex Workflows · Rasa (overkill) |
| **Inbox / Widget** | Chatwoot (self-host) | 40k+ stars, omnichannel, agent handoff built-in | Botpress (no-code) если инженерных ресурсов нет |
| **Hosting** | VPS Hetzner CX22 (~$5/мес) или DigitalOcean ($12) | RTX 3070 не нужна — всё работает на CPU | AWS Lightsail $10 |
| **Eval** | RAGAS (dev) + DeepEval (CI) + Phoenix (prod) | Бесплатно, OSS | TruLens |
| **Embedding model** | bge-m3 (multilingual RU/EN) | Bilingual ToV сайта требует мультиязычных эмбеддингов. Бесплатно, локально | OpenAI text-embedding-3-large ($0.13/M tokens) |

### Что КАТЕГОРИЧЕСКИ не делать

1. ❌ **Fine-tune на 7 ГБ синтетического sales-датасета.** Двойной доменный mismatch + риск инжектировать sales-rhetoric в lead-qualification бота, который должен звучать минималистично.
2. ❌ **Два локальных инстанса для self-play.** Не работает без gradient access; даже если бы работал — это не обучение, а синтетика без сигнала.
3. ❌ **Загружать всю переписку как RAG-контекст.** 100k записей про чужие SaaS-продукты загрязнят retrieval. Релевантным окажется случайный сейлс-питч про cloud storage, когда лид спрашивает про автоматизацию CRM.
4. ❌ **Полагаться на GLM-5.1:cloud для high-traffic.** На Medium есть жалобы апрель 2026: до 3 минут response time. Если стрельнёт пик трафика — бот ляжет, репутация Deadline ("ответ за минуты") пострадает.

---

## 4. Что делать с 7 ГБ датасета

| Подход | Действие | Ожидаемая ценность |
|---|---|---|
| **A. В архив, не использовать** | Удалить с production-инфры, оставить копию на отдельном диске на случай экспериментов через год | Максимально безопасно. Рекомендуется по умолчанию |
| **B. Извлечь стилевые примеры (опционально)** | Скриптом отобрать 50-100 диалогов где `conversation_style = "direct_professional"` и `outcome = 1`. Использовать как **few-shot examples в промпте** (не для fine-tune!) | Может улучшить тон сухих профессиональных ответов. Низкий риск, низкая выгода |
| **C. Использовать как negative examples в eval-сете** | Взять 200-500 диалогов как примеры "как НЕ должен общаться dev studio бот". Скормить в RAGAS-eval как контр-примеры | Хорошее упражнение для команды, но не критично |
| **D. Fine-tune** | НЕ ДЕЛАТЬ | Доменный mismatch + риск model collapse + ROI отрицательный |

**Рекомендация:** A + B (если есть инженерное время). Эмбеддинги (6.4 ГБ) — выбросить, они от OpenAI и несовместимы с bge-m3.

### Лучшая альтернатива — синтезируй свой датасет под Deadline

Это **намного полезнее** для cold-start, чем чужие sales-логи:

```
Шаг 1: Скрейп deadline-corp.github.io + Notion (когда дашь доступ) → knowledge base
Шаг 2: Через Claude/GPT-4 сгенерировать 100-200 Q&A пар по схеме:
       - Лид спрашивает "сколько стоит автоматизация CRM?" → ответ в ToV Deadline с эскалацией
       - Лид спрашивает "делаете ли вы AI-агентов?" → ссылка на VRP кейс
       - Лид спрашивает "у меня горящий дедлайн через 9 дней" → ссылка на кейс Александр К.
Шаг 3: Эти Q&A — основа eval-сета для RAGAS, НЕ training data
Шаг 4: Прогнать через RAGAS → итеративно улучшать retrieval/prompt
```

Это даст качественный сигнал за день, без рисков.

---

## 5. Поэтапный roadmap (1-3 месяца)

### Фаза 0 — Подготовка (3-5 дней)
- [ ] Развернуть VPS, поставить Chatwoot, проверить widget на staging-копии сайта
- [ ] Подписаться на Ollama Cloud Pro ($20), получить API-ключ
- [ ] Скрейпить весь deadline-corp.github.io → markdown (5 секций × ~10 чанков)
- [ ] Извлечь из Notion (когда будет доступ) описание сервисов + price ranges + кейсы
- [ ] Прогнать через bge-m3, залить в pgvector

### Фаза 1 — MVP бот (5-7 дней)
- [ ] System prompt с ToV Deadline (RU/EN, минимализм, "0 воды", manifesto-стиль)
- [ ] LangGraph flow: greeting → intent classification (lead / FAQ / casual) → RAG → response
- [ ] Эскалация: при низкой confidence → "Опишите задачу одним сообщением" → Telegram link
- [ ] Лид-капчер: имя + email/telegram + тип проекта + дедлайн → JSON → email
- [ ] 50 синтетических Q&A через Claude как RAGAS-set

### Фаза 2 — Shadow mode (10-14 дней)
- [ ] Деплой на сайт, но **бот в read-only** — не показывать ответы юзерам
- [ ] Логировать все запросы, прогонять через бот, сравнивать с тем, что бы ответил ты
- [ ] Calibrate confidence threshold
- [ ] Замерить: какие 5 интентов покрывают 80% трафика — для них допилить prompts

### Фаза 3 — Live с эскалацией (вечно)
- [ ] Включить ответы юзерам с агрессивным escalation threshold
- [ ] RAGAS eval раз в неделю как CI-гейт
- [ ] Через 3-6 месяцев — накопится 500+ реальных диалогов → можно начать думать о fine-tune (но скорее всего и тогда не понадобится)

### Не-фаза — **никогда**: self-play обучение, fine-tune на синтетике, два инстанса локально

---

## 6. Сметы

| Сценарий | LLM | Vector DB | Hosting | Eval | **Итого/мес** |
|---|---|---|---|---|---|
| **MVP (низкий трафик до 500/мес)** | Ollama Pro $20 | pgvector free | VPS $10 | OSS free | **~$30-40** |
| **Mid (500-2000/мес)** | Ollama Max $100 | pgvector $0 | VPS $20 | OSS free | **~$120-130** |
| **High (>2000/мес)** | OpenRouter ~$150 (Claude Haiku) | pgvector $0 | VPS $40 | OSS free | **~$200-250** |

Сравнение: коммерческий Intercom Fin = $0.99/resolved conversation. На 1000 чатов/мес — $990. То есть твоё self-hosted решение в 10-30× дешевле на тех же объёмах. **Это и есть UVP для Deadline в продаже клиентам**: "мы сделаем тебе такого бота за $200/мес вместо $1000 у Intercom".

---

## 7. Risk register

| Риск | Вероятность | Impact | Mitigation |
|---|---|---|---|
| Ollama Cloud GLM-5.1 лагает 1-3 мин на запрос | Средняя (есть прецеденты в апреле 2026) | **Высокий** — рушит "ответ за минуты" позиционирование | Fallback на OpenRouter / Claude Haiku при timeout > 5 сек |
| Rate limit Pro plan кончается в пик трафика | Высокая на >100 чатов/день | Средний | Auto-upgrade на Max при threshold, готовый fallback на DeepSeek/Qwen через OpenRouter |
| Бот галлюцинирует сроки или цены проекта | Средняя без guardrails | **Очень высокий** — юридический + репутационный | Жёсткое правило в system prompt: "Никогда не называй конкретные сроки и цены. Всегда: 'обсудим в Telegram'." + eval-проверка на этот паттерн |
| Бот отвечает в стиле GPT-4, ломает ToV | Высокая без файнтюна | Средний | 10-15 few-shot examples в промпте + RAGAS eval на "tone match" |
| 7 ГБ датасета случайно попадёт в RAG | Низкая (если запрещено в SOP) | Высокий | Не подключать его к pgvector. Точка |
| GitHub Pages не поддерживает чат-widget? | Низкая | Низкий — widget грузится через JS, бэк на VPS | Если ограничения — перенести на Cloudflare Pages |

---

## 8. Когда (если) понадобится fine-tune

После 3-6 месяцев работы в проде, если накопилось 500+ реальных диалогов **и** RAG-only бот стабильно проваливает что-то конкретное (например, не умеет правильно форматировать brief для Telegram-эскалации), **тогда** имеет смысл:

1. Взять последние 1000-2000 реальных диалогов с пометкой "хорошо / плохо"
2. QLoRA на cloud (Together AI ~$29 за full run на 7B) — а не локально на RTX 3070
3. Только LLM-as-judge filter — выкидывать всё с груз-фактором < 3
4. Применить RAFT-подход (fine-tune в open-book setting, не в closed-book) — это даёт хороший прирост, когда есть реальный домен
5. A/B-тест против baseline RAG, минимум 100 живых разговоров на ветку
6. Раскатать только если winrate > 60% по qualitative metrics

Это **продвинутый этап**, не для месяца №1.

---

## 9. Что нужно от тебя дальше

1. **Notion-доступ** — закрыто за auth, я получил только мета-теги. Дай гостевой share-link или экспортируй контент в markdown
2. **Подтверди стек** — устраивает ли pgvector / Chatwoot / LangGraph как базовая комбинация
3. **Решение по бюджету** — $30-40/мес MVP или сразу Mid за $120
4. **Доступ к VPS / инфре** — если хочешь, могу написать docker-compose файл и system prompt в следующем шаге
5. **Tone of voice details** — хочешь, чтобы бот ставил `//` префиксы и "EST. 2025" в ответах, или это только для маркетинговых частей сайта?

---

## Источники

### Stack & Market
- [Rasa: How to Build a Customer Service Chatbot in 2026](https://rasa.com/blog/how-to-build-a-customer-service-chatbot-in-2026)
- [Production RAG in 2026: LangChain vs LlamaIndex](https://rahulkolekar.com/production-rag-in-2026-langchain-vs-llamaindex/)
- [MarkTechPost: Best Vector Databases in 2026](https://www.marktechpost.com/2026/05/10/best-vector-databases-in-2026-pricing-scale-limits-and-architecture-tradeoffs-across-nine-leading-systems/)
- [CallSphere: Vector DB Benchmarks 2026](https://callsphere.ai/blog/vector-database-benchmarks-2026-pgvector-qdrant-weaviate-milvus-lancedb)
- [Ollama Pricing](https://ollama.com/pricing)
- [Bad Experience Running GLM5.1 through Ollama Cloud (Medium, Apr 2026)](https://tekloon.medium.com/bad-experience-running-glm5-1-through-ollama-cloud-f290b6a2fae4)
- [GitHub Issue: 16k token cap on Ollama Cloud](https://github.com/ollama/ollama/issues/13089)
- [Chatwoot](https://github.com/chatwoot/chatwoot) · [Botpress](https://botpress.com/) · [Intercom Fin Pricing](https://fin.ai/pricing)
- [Zams: Cold Start Problem with AI Agents](https://zams.com/blog/the-cold-start-problem-with-ai-agents-and-how-to-push-past-it)

### Technical / Research
- [SPIN: Self-Play Fine-Tuning (arXiv:2401.01335)](https://arxiv.org/abs/2401.01335)
- [Self-Rewarding Language Models (arXiv:2401.10020)](https://arxiv.org/abs/2401.10020)
- [Shumailov et al. — AI Models Collapse on Recursive Synthetic Data (Nature, 2024)](https://www.nature.com/articles/s41586-024-07566-y)
- [Gerstgrasser et al. — Is Model Collapse Inevitable? (arXiv:2404.01413)](https://arxiv.org/abs/2404.01413)
- [Position: Model Collapse Does Not Mean What You Think (arXiv:2503.03150)](https://arxiv.org/pdf/2503.03150)
- [Constitutional AI: Harmlessness from AI Feedback (Anthropic)](https://www.anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback)
- [RAGAS Evaluation Framework](https://docs.ragas.io/en/stable/) · [Together AI Fine-Tuning Pricing](https://docs.together.ai/docs/fine-tuning-pricing)
- [Fine-Tuning LLMs in 2026: When RAG Isn't Enough (BigData Boutique)](https://bigdataboutique.com/blog/fine-tuning-llms-when-rag-isnt-enough)

### Deadline-specific
- [Deadline Corp Studio website](https://deadline-corp.github.io/deadline-corp-studio/)
- HuggingFace dataset (NOT recommended): [DeepMostInnovations/saas-sales-conversations](https://huggingface.co/datasets/DeepMostInnovations/saas-sales-conversations)
