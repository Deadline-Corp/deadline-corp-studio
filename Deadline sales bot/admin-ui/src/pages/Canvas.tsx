import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ReactFlow, Background, Controls, MiniMap, Node, Edge, Handle, Position, useNodesState,
} from '@xyflow/react'
import { api } from '../api/client'
import { AnalyticsView } from '../api/types'
import { useOverview } from '../overviewContext'
import { CHANNEL_META, fmtAgo } from '../lib'
import { HintBar } from '../components/HintBar'

/* Канвас-«рубка» в духе eva.bz: бот в центре, слева каналы, справа подсистемы —
   живые узлы с метриками. Тащи ноды куда удобно (раскладка в localStorage),
   клик по ноде → её раздел (сквозная навигация). «↺ Раскладка» = стандарт. */
const LAYOUT_KEY = 'deadline_canvas_layout_v2'

function loadLayout(): Record<string, { x: number; y: number }> {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}') } catch { return {} }
}

const TONE = {
  bot: '#7c6cff', funnel: '#7c6cff', brain: '#3bb4a0', kb: '#6ba3e8',
  crm: '#c06bd8', tasks: '#e0a23b', auto: '#3bb4a0',
  whatsapp: '#25d366', telegram: '#3b9eff', website: '#8b93a7',
  instagram: '#e1306c', messenger: '#0084ff',
}
const HOT = '#e0524f'
const WARN = '#e0a23b'

interface Metric { label: string; value: string | number; tone?: string }
interface NodeData {
  icon: string; title: string; sub?: string; tone?: string; badge?: string
  rows?: Array<{ k: string; v: string | number; cls?: string }>
  metrics?: Metric[]
  bars?: number[]
  chips?: Array<{ text: string; cls: string }>
  center?: boolean; dim?: boolean; to?: string
  [key: string]: unknown
}

function CardNode({ data }: { data: NodeData }) {
  return (
    <div className={`flow-node${data.center ? ' center' : ''}${data.dim ? ' dim' : ''}`}
         style={{ ['--tone' as any]: data.tone || 'var(--accent)' }}>
      <span className="n-stripe" />
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="n-head">
        <div className="n-ico">{data.icon}</div>
        <div style={{ minWidth: 0 }}>
          <div className="n-title">{data.title}</div>
          {data.sub && <div className="n-sub">{data.sub}</div>}
        </div>
        {data.badge && <span className="n-badge">{data.badge}</span>}
      </div>
      {data.bars && (
        <div className="n-bars">{data.bars.map((h, i) => <i key={i} style={{ height: Math.max(2, h) + '%' }} />)}</div>
      )}
      {data.metrics && (
        <div className="n-metrics">
          {data.metrics.map((m, i) => (
            <div className="n-metric" key={i}>
              <div className="ml">{m.label}</div>
              <div className="mv" style={m.tone ? { color: m.tone } : undefined}>{m.value}</div>
            </div>
          ))}
        </div>
      )}
      {data.rows && (
        <div className="n-body">
          {data.rows.map((r, i) => <div className="n-row" key={i}><span>{r.k}</span><b className={r.cls}>{r.v}</b></div>)}
        </div>
      )}
      {data.chips && (
        <div style={{ display: 'flex', gap: 5, marginTop: 7, flexWrap: 'wrap' }}>
          {data.chips.map((c, i) => <span key={i} className={`chip ${c.cls}`}>{c.text}</span>)}
        </div>
      )}
      {data.to && <div className="n-go">Открыть <span>→</span></div>}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  )
}

const nodeTypes = { card: CardNode }

export function Canvas() {
  const ov = useOverview()
  const navigate = useNavigate()
  const [kpi, setKpi] = useState<AnalyticsView | null>(null)
  const [layoutV, setLayoutV] = useState(0)

  useEffect(() => {
    void api.get<AnalyticsView>('/analytics?days=7').then(setKpi).catch(() => { /* */ })
  }, [])

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])

  const computed = useMemo(() => {
    if (!ov) return { nodes: [] as Node[], edges: [] as Edge[] }
    const saved = loadLayout()
    const nodes: Node[] = []
    const edges: Edge[] = []
    const edge = (id: string, source: string, target: string, tone: string, on = true): Edge => ({
      id, source, target, type: 'smoothstep', animated: on,
      style: { stroke: tone, strokeWidth: on ? 2.2 : 1.2, opacity: on ? 0.85 : 0.25 },
    })

    if (kpi) {
      nodes.push({
        id: 'kpi', type: 'card', position: saved['kpi'] ?? { x: 470, y: 20 },
        data: {
          icon: '📈', title: 'Дашборд · 7 дней', to: '/analytics', tone: TONE.bot,
          metrics: [
            { label: 'новых лидов', value: kpi.totals.new_leads },
            { label: 'созвонов', value: kpi.totals.booked_calls },
            { label: 'автоматизаций', value: kpi.totals.automation_fires },
          ],
        } satisfies NodeData,
      })
    }

    nodes.push({
      id: 'bot', type: 'card', position: saved['bot'] ?? { x: 470, y: 250 },
      data: {
        icon: '🤖', title: 'Дедлайн · AI-агент', center: true, tone: TONE.bot,
        badge: ov.bot.model.split('/').pop(),
        sub: ov.bot.prompt_source === 'db' ? 'мозг кастомный' : 'мозг заводской',
        metrics: [
          { label: 'диалогов', value: ov.inbox.open },
          { label: 'на операторе', value: ov.inbox.takeover },
          { label: 'передано', value: ov.inbox.handed_off },
        ],
        to: '/inbox',
      } satisfies NodeData,
    })

    // Слева — каналы трафика с метриками.
    ov.channels.forEach((ch, i) => {
      const meta = CHANNEL_META[ch.id]
      const tone = (TONE as any)[ch.id] || TONE.website
      const id = `ch-${ch.id}`
      nodes.push({
        id, type: 'card', position: saved[id] ?? { x: 60, y: 30 + i * 168 },
        data: {
          icon: meta.icon, title: meta.label, tone, dim: !ch.configured,
          badge: ch.configured ? 'подключён' : 'выкл',
          sub: ch.configured ? `актив. ${fmtAgo(ch.last_message_at)} назад` : 'не подключён',
          metrics: ch.configured ? [
            { label: 'диалогов', value: ch.conversations },
            { label: 'вчера', value: ch.new_yesterday ?? 0 },
            { label: 'горячих', value: ch.hot ?? 0, tone: (ch.hot ?? 0) > 0 ? HOT : undefined },
            { label: 'без задачи', value: ch.no_task ?? 0, tone: (ch.no_task ?? 0) > 0 ? WARN : undefined },
          ] : undefined,
          to: `/inbox?channel=${ch.id}`,
        } satisfies NodeData,
      })
      edges.push(edge(`e-${ch.id}`, id, 'bot', tone, ch.configured))
    })

    // Справа — подсистемы.
    const fcounts = ov.funnel.stages.map(s => s.count)
    const fmax = Math.max(1, ...fcounts)
    const totalFunnel = fcounts.reduce((s, x) => s + x, 0)
    const t = ov.tasks
    const right: Array<{ id: string; y: number; data: NodeData }> = [
      {
        id: 'funnel', y: 10,
        data: {
          icon: '📊', title: 'Воронка', to: '/funnel', tone: TONE.funnel,
          sub: `${totalFunnel} сделок в работе`,
          bars: fcounts.map(c => Math.round((c / fmax) * 100)),
        },
      },
      {
        id: 'tasks', y: 180,
        data: {
          icon: '⏰', title: 'Задачи', to: '/tasks', tone: TONE.tasks,
          metrics: [
            { label: 'просрочено', value: t.overdue ?? 0, tone: (t.overdue ?? 0) > 0 ? HOT : undefined },
            { label: 'сегодня', value: t.today ?? 0 },
            { label: 'без задачи', value: t.no_task ?? 0, tone: (t.no_task ?? 0) > 0 ? WARN : undefined },
          ],
        },
      },
      {
        id: 'brain', y: 350,
        data: {
          icon: '🧠', title: 'Мозг', to: '/brain', tone: TONE.brain,
          sub: ov.bot.provider,
          metrics: [
            { label: 'правил', value: ov.training.active_corrections },
            { label: 'KB фактов', value: ov.kb.chunks },
          ],
        },
      },
      {
        id: 'crm', y: 500,
        data: {
          icon: '🗂', title: 'CRM', to: '/settings', tone: TONE.crm,
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

    return { nodes, edges }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ov, kpi, layoutV])

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
          Пульт системы: слева каналы трафика (с метриками), в центре бот, справа подсистемы.
          Клик по карточке — <b>провалитесь внутрь</b>. Карточки <b>перетаскиваются</b> — раскладка запомнится.
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
          fitViewOptions={{ padding: 0.16 }}
          proOptions={{ hideAttribution: true }}
          nodesDraggable
          nodesConnectable={false}
          minZoom={0.4}
          onNodeClick={(_, node) => {
            const to = (node.data as NodeData).to
            if (to) navigate(to)
          }}
          onNodeDragStop={(_, node) => {
            const saved = loadLayout()
            saved[node.id] = { x: node.position.x, y: node.position.y }
            localStorage.setItem(LAYOUT_KEY, JSON.stringify(saved))
          }}
        >
          <Background gap={22} size={1.2} color="rgba(148,156,210,0.12)" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable nodeStrokeWidth={2}
                   nodeColor={(n) => ((n.data as NodeData)?.tone as string) || '#7c6cff'}
                   maskColor="rgba(10,12,22,0.6)"
                   style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 10 }} />
        </ReactFlow>
      </div>
    </div>
  )
}
