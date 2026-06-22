import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ReactFlow, Background, Controls, Node, Edge, Handle, Position, useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '../api/client'
import { AnalyticsView } from '../api/types'
import { useOverview } from '../overviewContext'
import { CHANNEL_META, fmtAgo } from '../lib'
import { HintBar } from '../components/HintBar'

/* Канвас-«рубка» в духе eva.bz: бот в центре, слева каналы → хаб «Источники» → бот,
   справа подсистемы — живые узлы с метриками. Связи изогнутые (bezier), подключённые
   узлы «живые» (анимация потока), неподключённые статичные. Каналы сворачиваются:
   клик = полу-разворот (показать метрики), кнопка «Открыть» = переход в раздел.
   Дашборд встроен в центральную ноду-агента. Тащи ноды (раскладка в localStorage). */
const LAYOUT_KEY = 'deadline_canvas_layout_v3'

function loadLayout(): Record<string, { x: number; y: number }> {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}') } catch { return {} }
}

const TONE = {
  bot: '#7c6cff', funnel: '#7c6cff', brain: '#3bb4a0', kb: '#6ba3e8',
  crm: '#c06bd8', tasks: '#e0a23b', auto: '#3bb4a0', sources: '#6ba3e8',
  whatsapp: '#25d366', telegram: '#3b9eff', website: '#8b93a7',
  instagram: '#e1306c', messenger: '#0084ff',
}
const HOT = '#e0524f'
const WARN = '#e0a23b'

interface Metric { label: string; value: string | number; tone?: string }
interface NodeData {
  icon: string; title: string; sub?: string; tone?: string; badge?: string; dot?: 'live' | 'warn' | 'idle'
  rows?: Array<{ k: string; v: string | number; cls?: string }>
  metrics?: Metric[]
  kpis?: Metric[]
  bars?: number[]
  chips?: Array<{ text: string; cls: string }>
  center?: boolean; dim?: boolean; to?: string
  collapsible?: boolean; expanded?: boolean
  onOpen?: () => void
  [key: string]: unknown
}

function CardNode({ data }: { data: NodeData }) {
  const collapsed = !!data.collapsible && !data.expanded
  return (
    <div className={`flow-node${data.center ? ' center' : ''}${data.dim ? ' dim' : ''}${data.collapsible ? ' expandable' : ''}`}
         style={{ ['--tone' as any]: data.tone || 'var(--accent)' }}>
      <span className="n-stripe" />
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="n-head">
        <div className="n-ico">{data.icon}{data.dot && <span className={`n-dot ${data.dot}`} />}</div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="n-title">{data.title}</div>
          {data.sub && <div className="n-sub">{data.sub}</div>}
        </div>
        {data.badge && <span className="n-badge">{data.badge}</span>}
        {data.collapsible && <span className="n-chevron">{data.expanded ? '▾' : '▸'}</span>}
      </div>
      {!collapsed && data.bars && (
        <div className="n-bars">{data.bars.map((h, i) => <i key={i} style={{ height: Math.max(2, h) + '%' }} />)}</div>
      )}
      {!collapsed && data.metrics && (
        <div className="n-metrics">
          {data.metrics.map((m, i) => (
            <div className="n-metric" key={i}>
              <div className="ml">{m.label}</div>
              <div className="mv" style={m.tone ? { color: m.tone } : undefined}>{m.value}</div>
            </div>
          ))}
        </div>
      )}
      {!collapsed && data.kpis && (
        <>
          <div className="n-cap">за 7 дней</div>
          <div className="n-metrics">
            {data.kpis.map((m, i) => (
              <div className="n-metric" key={i}>
                <div className="ml">{m.label}</div>
                <div className="mv" style={m.tone ? { color: m.tone } : undefined}>{m.value}</div>
              </div>
            ))}
          </div>
        </>
      )}
      {!collapsed && data.rows && (
        <div className="n-body">
          {data.rows.map((r, i) => <div className="n-row" key={i}><span>{r.k}</span><b className={r.cls}>{r.v}</b></div>)}
        </div>
      )}
      {!collapsed && data.chips && (
        <div style={{ display: 'flex', gap: 5, marginTop: 7, flexWrap: 'wrap' }}>
          {data.chips.map((c, i) => <span key={i} className={`chip ${c.cls}`}>{c.text}</span>)}
        </div>
      )}
      {collapsed && <div className="n-hint">нажми, чтобы развернуть</div>}
      {!collapsed && data.to && (
        <button type="button" className="n-go"
                onClick={(e) => { e.stopPropagation(); data.onOpen?.() }}>
          Открыть <span>→</span>
        </button>
      )}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  )
}

function LabelNode({ data }: { data: { text: string } }) {
  return <div className="flow-label">{data.text}</div>
}

const nodeTypes = { card: CardNode, label: LabelNode }

export function Canvas() {
  const ov = useOverview()
  const navigate = useNavigate()
  const [kpi, setKpi] = useState<AnalyticsView | null>(null)
  const [layoutV, setLayoutV] = useState(0)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    void api.get<AnalyticsView>('/analytics?days=7').then(setKpi).catch(() => { /* */ })
  }, [])

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])

  const go = useCallback((to?: string) => { if (to) navigate(to) }, [navigate])

  const computed = useMemo(() => {
    if (!ov) return { nodes: [] as Node[], edges: [] as Edge[] }
    const saved = loadLayout()
    const nodes: Node[] = []
    const edges: Edge[] = []
    // Изогнутые (bezier) связи. Подключённое = «живой» поток (анимация), иначе статика.
    const edge = (id: string, source: string, target: string, _tone: string, on = true): Edge => ({
      id, source, target, type: 'default', animated: on,
      style: { stroke: on ? 'var(--accent)' : 'var(--border-strong)', strokeWidth: on ? 2 : 1.3, opacity: on ? 0.5 : 0.7 },
    })

    // ЦЕНТР — бот-агент со встроенным дашбордом (отдельной ноды «Дашборд» больше нет).
    nodes.push({
      id: 'bot', type: 'card', position: saved['bot'] ?? { x: 560, y: 230 },
      data: {
        icon: '🤖', title: 'Дедлайн · AI-агент', center: true, tone: TONE.bot, dot: 'live',
        badge: ov.bot.model.split('/').pop(),
        sub: ov.bot.prompt_source === 'db' ? 'мозг кастомный' : 'мозг заводской',
        metrics: [
          { label: 'диалогов', value: ov.inbox.open },
          { label: 'на операторе', value: ov.inbox.takeover },
          { label: 'передано', value: ov.inbox.handed_off },
        ],
        kpis: kpi ? [
          { label: 'новых лидов', value: kpi.totals.new_leads },
          { label: 'созвонов', value: kpi.totals.booked_calls },
          { label: 'автоматизаций', value: kpi.totals.automation_fires },
        ] : undefined,
        to: '/inbox', onOpen: () => go('/inbox'),
      } satisfies NodeData,
    })

    // ХАБ «Источники» — между каналами и ботом: весь входящий трафик стекается сюда.
    const totalConvs = ov.channels.reduce((s, c) => s + (c.conversations || 0), 0)
    const totalNewY = ov.channels.reduce((s, c) => s + (c.new_yesterday || 0), 0)
    const totalHot = ov.channels.reduce((s, c) => s + (c.hot || 0), 0)
    const activeCh = ov.channels.filter(c => c.configured).length
    nodes.push({
      id: 'sources', type: 'card', position: saved['sources'] ?? { x: 305, y: 250 },
      data: {
        icon: '📥', title: 'Источники', tone: TONE.sources,
        dot: totalNewY > 0 ? 'live' : 'idle',
        sub: `${activeCh} из ${ov.channels.length} каналов на связи`,
        metrics: [
          { label: 'диалогов', value: totalConvs },
          { label: 'вчера', value: totalNewY },
          { label: 'горячих', value: totalHot, tone: totalHot > 0 ? HOT : undefined },
        ],
        to: '/inbox', onOpen: () => go('/inbox'),
      } satisfies NodeData,
    })
    edges.push(edge('e-sources-bot', 'sources', 'bot', TONE.sources, true))

    // СЛЕВА — каналы трафика (сворачиваемые: клик = полу-разворот, «Открыть» = переход).
    ov.channels.forEach((ch, i) => {
      const meta = CHANNEL_META[ch.id]
      const tone = (TONE as any)[ch.id] || TONE.website
      const id = `ch-${ch.id}`
      nodes.push({
        id, type: 'card', position: saved[id] ?? { x: 40, y: 20 + i * 150 },
        data: {
          icon: meta.icon, title: meta.label, tone, dim: !ch.configured,
          dot: ch.configured ? (((ch.hot ?? 0) > 0 || (ch.no_task ?? 0) > 0) ? 'warn' : 'live') : 'idle',
          badge: ch.configured ? 'подключён' : 'выкл',
          sub: ch.configured ? `актив. ${fmtAgo(ch.last_message_at)} назад` : 'не подключён',
          collapsible: true, expanded: expanded.has(id),
          metrics: ch.configured ? [
            { label: 'диалогов', value: ch.conversations },
            { label: 'вчера', value: ch.new_yesterday ?? 0 },
            { label: 'горячих', value: ch.hot ?? 0, tone: (ch.hot ?? 0) > 0 ? HOT : undefined },
            { label: 'без задачи', value: ch.no_task ?? 0, tone: (ch.no_task ?? 0) > 0 ? WARN : undefined },
          ] : undefined,
          to: `/inbox?channel=${ch.id}`, onOpen: () => go(`/inbox?channel=${ch.id}`),
        } satisfies NodeData,
      })
      // Каналы стекаются в «Источники», а тот — в бот.
      edges.push(edge(`e-${ch.id}`, id, 'sources', tone, ch.configured))
    })

    // СПРАВА — подсистемы (CRM с другой стороны от источников).
    const fcounts = ov.funnel.stages.map(s => s.count)
    const fmax = Math.max(1, ...fcounts)
    const totalFunnel = fcounts.reduce((s, x) => s + x, 0)
    const t = ov.tasks
    const right: Array<{ id: string; y: number; data: NodeData }> = [
      {
        id: 'funnel', y: 10,
        data: {
          icon: '📊', title: 'Воронка', to: '/funnel', onOpen: () => go('/funnel'), tone: TONE.funnel,
          sub: `${totalFunnel} сделок в работе`,
          bars: fcounts.map(c => Math.round((c / fmax) * 100)),
        },
      },
      {
        id: 'tasks', y: 190,
        data: {
          icon: '⏰', title: 'Задачи', to: '/tasks', onOpen: () => go('/tasks'), tone: TONE.tasks,
          dot: (t.overdue ?? 0) > 0 ? 'warn' : 'live',
          metrics: [
            { label: 'просрочено', value: t.overdue ?? 0, tone: (t.overdue ?? 0) > 0 ? HOT : undefined },
            { label: 'сегодня', value: t.today ?? 0 },
            { label: 'без задачи', value: t.no_task ?? 0, tone: (t.no_task ?? 0) > 0 ? WARN : undefined },
          ],
        },
      },
      {
        id: 'brain', y: 365,
        data: {
          icon: '🧠', title: 'Мозг', to: '/brain', onOpen: () => go('/brain'), tone: TONE.brain,
          sub: ov.bot.provider,
          metrics: [
            { label: 'правил', value: ov.training.active_corrections },
            { label: 'KB фактов', value: ov.kb.chunks },
          ],
        },
      },
      {
        id: 'crm', y: 520,
        data: {
          icon: '🗂', title: 'CRM', to: '/settings', onOpen: () => go('/settings'), tone: TONE.crm,
          dot: ov.crm.enabled ? (ov.crm.events_failed ? 'warn' : 'live') : 'idle',
          sub: ov.crm.enabled ? ov.crm.provider : 'выключена', dim: !ov.crm.enabled,
          metrics: [
            { label: 'в очереди', value: ov.crm.events_pending },
            { label: 'ошибок', value: ov.crm.events_failed, tone: ov.crm.events_failed ? HOT : undefined },
          ],
        },
      },
    ]
    right.forEach(r => {
      nodes.push({ id: r.id, type: 'card', position: saved[r.id] ?? { x: 880, y: r.y }, data: r.data })
      edges.push(edge(`e-${r.id}`, 'bot', r.id, r.data.tone || TONE.bot))
    })

    // Подписи колонок (статичные ориентиры, не перетаскиваются).
    const label = (id: string, x: number, y: number, text: string): Node => ({
      id, type: 'label', position: { x, y }, data: { text },
      draggable: false, selectable: false, connectable: false,
    })
    nodes.push(
      label('lbl-ch', 48, -26, 'Каналы'),
      label('lbl-bot', 580, 196, 'AI-агент'),
      label('lbl-sub', 896, -26, 'Подсистемы'),
    )

    return { nodes, edges }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ov, kpi, layoutV, expanded, go])

  useEffect(() => {
    setNodes(computed.nodes)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [computed])

  if (!ov) {
    return <div className="page"><div className="empty"><span className="spin" /> Загрузка…</div></div>
  }

  return (
    <div className="page canvas-page">
      <div style={{ padding: '14px 18px 0' }}>
        <HintBar id="canvas" icon="🕸">
          Пульт системы: слева каналы трафика → хаб <b>«Источники»</b> → бот, справа подсистемы.
          Каналы <b>сворачиваются</b> (клик — развернуть метрики), кнопка <b>«Открыть»</b> — провалиться в раздел.
          Карточки <b>перетаскиваются</b> — раскладка запомнится.
        </HintBar>
      </div>
      <div className="canvas-wrap" style={{ position: 'relative' }}>
        <button className="btn sm ghost" style={{ position: 'absolute', top: 8, right: 14, zIndex: 5 }}
                title="Вернуть стандартную раскладку"
                onClick={() => { localStorage.removeItem(LAYOUT_KEY); setLayoutV(v => v + 1) }}>
          ↺ Раскладка
        </button>
        <ReactFlow
          nodes={nodes}
          edges={computed.edges}
          onNodesChange={onNodesChange}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          proOptions={{ hideAttribution: true }}
          nodesDraggable
          nodesConnectable={false}
          panOnDrag
          selectionOnDrag={false}
          zoomOnDoubleClick={false}
          minZoom={0.3}
          onNodeClick={(_, node) => {
            const d = node.data as NodeData
            // Сворачиваемые каналы: клик = полу-разворот (переход — только кнопкой «Открыть»).
            if (d.collapsible) {
              setExpanded(prev => {
                const next = new Set(prev)
                if (next.has(node.id)) next.delete(node.id); else next.add(node.id)
                return next
              })
              return
            }
            if (d.to) navigate(d.to)
          }}
          onNodeDragStop={(_, node) => {
            const saved = loadLayout()
            saved[node.id] = { x: node.position.x, y: node.position.y }
            localStorage.setItem(LAYOUT_KEY, JSON.stringify(saved))
          }}
        >
          <Background gap={24} size={1.2} color="var(--grid-dot)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}
