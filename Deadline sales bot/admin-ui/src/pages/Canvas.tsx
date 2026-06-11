import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ReactFlow, Background, Controls, Node, Edge, Handle, Position } from '@xyflow/react'
import { useOverview } from '../components/Layout'
import { CHANNEL_META, fmtAgo } from '../lib'

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

  const { nodes, edges } = useMemo(() => {
    if (!ov) return { nodes: [] as Node[], edges: [] as Edge[] }

    const nodes: Node[] = []
    const edges: Edge[] = []

    // Центр — бот.
    nodes.push({
      id: 'bot', type: 'card', position: { x: 430, y: 230 },
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
        id: `ch-${ch.id}`, type: 'card', position: { x: 60, y: 40 + i * 150 },
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
      nodes.push({ id: r.id, type: 'card', position: { x: 850, y: r.y }, data: r.data })
      edges.push({
        id: `e-${r.id}`, source: 'bot', target: r.id,
        animated: true, style: { strokeWidth: 1.5 },
      })
    })

    return { nodes, edges }
  }, [ov])

  if (!ov) {
    return <div className="page"><div className="empty"><span className="spin" /> Загрузка…</div></div>
  }

  return (
    <div className="page canvas-page">
      <div className="canvas-wrap">
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
        >
          <Background gap={26} size={1.4} color="rgba(148,156,210,0.10)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}
