import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ReactFlow, Background, Controls, Node, Edge, Handle, Position } from '@xyflow/react'
import { api } from '../api/client'
import { AnalyticsView } from '../api/types'
import { useOverview } from '../overviewContext'
import { CHANNEL_META, fmtAgo } from '../lib'
import { HintBar } from '../components/HintBar'

/* Раскладка канваса настраивается: тащите ноды куда удобно — позиции
   запоминаются (localStorage) и переживают перезагрузку. «↺ Раскладка»
   возвращает стандарт. */
const LAYOUT_KEY = 'deadline_canvas_layout_v2'

function loadLayout(): Record<string, { x: number; y: number }> {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}') } catch { return {} }
}

/* Канвас в духе eva.bz: бот в центре, слева каналы, справа подсистемы.
   Клик по ноде → соответствующий раздел (сквозная навигация). */

interface NodeData {
  icon: string
  title: string
  sub?: string
  rows?: Array<{ k: string; v: string | number; cls?: string }>
  chips?: Array<{ text: string; cls: string }>
  center?: boolean
  dim?: boolean
  to?: string
  [key: string]: unknown
}

function CardNode({ data }: { data: NodeData }) {
  return (
    <div className={`flow-node${data.center ? ' center' : ''}${data.dim ? ' dim' : ''}`}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="n-head">
        <div className="n-ico">{data.icon}</div>
        <div>
          <div className="n-title">{data.title}</div>
          {data.sub && <div className="n-sub">{data.sub}</div>}
        </div>
      </div>
      {data.rows && (
        <div className="n-body">
          {data.rows.map((r, i) => (
            <div className="n-row" key={i}><span>{r.k}</span><b className={r.cls}>{r.v}</b></div>
          ))}
        </div>
      )}
      {data.chips && (
        <div style={{ display: 'flex', gap: 5, marginTop: 7, flexWrap: 'wrap' }}>
          {data.chips.map((c, i) => <span key={i} className={`chip ${c.cls}`}>{c.text}</span>)}
        </div>
      )}
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

  const { nodes, edges } = useMemo(() => {
    if (!ov) return { nodes: [] as Node[], edges: [] as Edge[] }

    const saved = loadLayout()
    const nodes: Node[] = []
    const edges: Edge[] = []

    // KPI-дашборд за 7 дней — сверху по центру.
    if (kpi) {
      nodes.push({
        id: 'kpi', type: 'card', position: saved['kpi'] ?? { x: 430, y: 30 },
        data: {
          icon: '📈', title: 'Дашборд · 7 дней', to: '/analytics',
          rows: [
            { k: 'Новых лидов', v: kpi.totals.new_leads },
            { k: 'Созвонов назначено', v: kpi.totals.booked_calls },
            { k: 'Автоматизаций сработало', v: kpi.totals.automation_fires },
          ],
        } satisfies NodeData,
      })
    }

    // Центр — бот.
    nodes.push({
      id: 'bot', type: 'card', position: saved['bot'] ?? { x: 430, y: 230 },
      data: {
        icon: '🤖', title: 'Дедлайн · AI-агент', center: true,
        sub: ov.bot.model.split('/').pop(),
        rows: [
          { k: 'Открытых диалогов', v: ov.inbox.open },
          { k: 'На операторе', v: ov.inbox.takeover },
          { k: 'Мозг', v: ov.bot.prompt_source === 'db' ? 'кастомный' : 'заводской' },
        ],
        to: '/brain',
      } satisfies NodeData,
    })

    // Слева — каналы.
    ov.channels.forEach((ch, i) => {
      const meta = CHANNEL_META[ch.id]
      nodes.push({
        id: `ch-${ch.id}`, type: 'card', position: saved[`ch-${ch.id}`] ?? { x: 60, y: 40 + i * 150 },
        data: {
          icon: meta.icon, title: meta.label,
          dim: !ch.configured,
          sub: ch.configured ? `активность: ${fmtAgo(ch.last_message_at)} назад` : 'не подключён',
          rows: ch.configured ? [
            { k: 'Диалогов', v: ch.conversations },
            { k: 'Открыто', v: ch.open },
          ] : undefined,
          chips: ch.configured ? [{ text: 'подключён', cls: 'ok' }] : [{ text: 'выключен', cls: '' }],
          to: `/inbox?channel=${ch.id}`,
        } satisfies NodeData,
      })
      edges.push({
        id: `e-${ch.id}`, source: `ch-${ch.id}`, target: 'bot',
        animated: ch.configured, style: { strokeWidth: 1.5 },
      })
    })

    // Справа — подсистемы.
    const totalFunnel = ov.funnel.stages.reduce((s, x) => s + x.count, 0)
    const right: Array<{ id: string; y: number; data: NodeData }> = [
      {
        id: 'funnel', y: 10,
        data: {
          icon: '📊', title: 'Воронка', to: '/funnel',
          rows: ov.funnel.stages.filter(s => s.count > 0).slice(0, 4)
            .map(s => ({ k: s.label, v: s.count })),
          sub: `${totalFunnel} сделок`,
        },
      },
      {
        id: 'kb', y: 165,
        data: {
          icon: '📚', title: 'База знаний', to: '/settings',
          rows: [
            { k: 'Документов', v: ov.kb.sources },
            { k: 'Чанков', v: ov.kb.chunks },
          ],
        },
      },
      {
        id: 'training', y: 300,
        data: {
          icon: '🎓', title: 'Обучение', to: '/brain',
          rows: [{ k: 'Активных правил', v: ov.training.active_corrections }],
        },
      },
      {
        id: 'crm', y: 410,
        data: {
          icon: '🗂', title: 'CRM', to: '/settings',
          sub: ov.crm.enabled ? ov.crm.provider : 'выключена',
          dim: !ov.crm.enabled,
          rows: [
            { k: 'В очереди', v: ov.crm.events_pending },
            { k: 'Ошибок', v: ov.crm.events_failed, cls: ov.crm.events_failed ? 'chip danger' : undefined },
          ],
          chips: ov.crm.events_failed
            ? [{ text: `⚠ ${ov.crm.events_failed} failed`, cls: 'danger' }]
            : undefined,
        },
      },
      {
        id: 'tasks', y: 545,
        data: {
          icon: '⏰', title: 'Задачи', to: '/tasks',
          rows: [{ k: 'Отложенных', v: ov.tasks.scheduled_pending }],
        },
      },
    ]
    right.forEach(r => {
      nodes.push({ id: r.id, type: 'card', position: saved[r.id] ?? { x: 850, y: r.y }, data: r.data })
      edges.push({
        id: `e-${r.id}`, source: 'bot', target: r.id,
        animated: true, style: { strokeWidth: 1.5 },
      })
    })

    return { nodes, edges }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ov, kpi, layoutV])

  if (!ov) {
    return <div className="page"><div className="empty"><span className="spin" /> Загрузка…</div></div>
  }

  return (
    <div className="page canvas-page">
      <div style={{ padding: '14px 18px 0' }}>
        <HintBar id="canvas" icon="🕸">
          Пульт системы: слева каналы, в центре бот, справа подсистемы. Кликните по карточке —
          провалитесь внутрь. <b>Карточки можно перетаскивать</b> — раскладка запомнится.
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
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          proOptions={{ hideAttribution: true }}
          nodesDraggable
          nodesConnectable={false}
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
          <Background gap={26} size={1.4} color="rgba(148,156,210,0.10)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}
