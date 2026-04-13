'use client'

import { useEffect, useLayoutEffect, useRef, useCallback, useState } from 'react'
import dynamic from 'next/dynamic'

const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), { ssr: false })

interface GraphNode {
  id: string
  type: 'topic' | 'concept' | 'card'
  label: string
  val: number
  color: string
  data?: {
    title: string
    summary: string
    tags: string[]
    category: string
    source_url: string
  }
  // runtime
  x?: number; y?: number
}

interface GraphEdge {
  source: string
  target: string
  type: string
}

interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  bridge_nodes?: BridgeNode[]
}

interface BridgeNode {
  id: string
  label: string
  type: string
  centrality: number
  degree: number
  topics: string[]
}

interface Props {
  highlightIds: Set<string>
  bridgeIds?: Set<string>
  onNodeClick: (node: GraphNode) => void
  onNodesLoaded?: (nodes: GraphNode[], bridges: BridgeNode[]) => void
}

const TYPE_COLORS = {
  topic:   '#7c3aed',
  concept: '#0891b2',
  card:    '#3b82f6',
}

export default function KnowledgeGraph({ highlightIds, bridgeIds, onNodeClick, onNodesLoaded }: Props) {
  const [graphData, setGraphData] = useState<{ nodes: GraphNode[]; links: GraphEdge[] } | null>(null)
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 })
  const containerRef = useRef<HTMLDivElement>(null)
  const fgRef = useRef<any>(null)

  useEffect(() => {
    fetch(`/graph-data.json?t=${Date.now()}`)
      .then(r => r.json())
      .then((d: GraphData) => {
        setGraphData({ nodes: d.nodes, links: d.edges })
        onNodesLoaded?.(d.nodes, d.bridge_nodes ?? [])
      })
  }, [onNodesLoaded])

  // Measure container immediately on mount, then track resizes
  useLayoutEffect(() => {
    if (!containerRef.current) return
    const { width, height } = containerRef.current.getBoundingClientRect()
    setDimensions({ width, height })
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      setDimensions({ width, height })
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  // Fit graph after first load or resize
  useEffect(() => {
    if (graphData && fgRef.current) {
      setTimeout(() => fgRef.current?.zoomToFit(400, 40), 600)
    }
  }, [graphData, dimensions])

  const nodeCanvasObject = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const isHighlighted = highlightIds.has(node.id)
    const isBridge = bridgeIds?.has(node.id) ?? false
    const dimmed = highlightIds.size > 0 && !isHighlighted
    const baseColor = TYPE_COLORS[node.type as keyof typeof TYPE_COLORS] || '#3b82f6'
    const radius = (node.val || 2) * (isHighlighted ? 2.0 : 1.0)

    // Bridge node: draw outer ring
    if (isBridge && !dimmed) {
      ctx.beginPath()
      ctx.arc(node.x, node.y, radius + 4, 0, 2 * Math.PI)
      ctx.strokeStyle = '#f59e0b88'
      ctx.lineWidth = 1.5
      ctx.stroke()
    }

    // Glow
    if (node.type === 'topic' || node.type === 'concept' || isHighlighted) {
      ctx.shadowBlur = isHighlighted ? 24 : isBridge ? 16 : 10
      ctx.shadowColor = isBridge ? '#f59e0b' : baseColor
    } else {
      ctx.shadowBlur = 4
      ctx.shadowColor = baseColor
    }

    ctx.beginPath()
    ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI)
    ctx.fillStyle = dimmed ? baseColor + '4d' : baseColor + 'ee'
    ctx.fill()
    ctx.shadowBlur = 0

    // Labels
    const showLabel = node.type === 'topic' || node.type === 'concept' || isHighlighted || isBridge
    if (showLabel) {
      const label = node.label.length > 22 ? node.label.slice(0, 22) + '…' : node.label
      const fontSize = Math.max(10 / globalScale, 3)
      ctx.font = `${fontSize}px Inter, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillStyle = isHighlighted ? '#ffffff' : isBridge ? '#fcd34d' : dimmed ? '#475569' : '#94a3b8'
      ctx.fillText(label, node.x, node.y + radius + 8 / globalScale)
    }
  }, [highlightIds, bridgeIds])

  const linkColor = useCallback((link: any) => {
    const srcId = typeof link.source === 'object' ? link.source.id : link.source
    const tgtId = typeof link.target === 'object' ? link.target.id : link.target
    const either = highlightIds.has(srcId) || highlightIds.has(tgtId)
    if (highlightIds.size === 0) return '#1e1e3a'
    return either ? '#7c3aed88' : '#1e1e3a22'
  }, [highlightIds])

  if (!graphData) {
    return (
      <div className="flex items-center justify-center h-full text-slate-500 text-sm">
        Loading graph…
      </div>
    )
  }

  return (
    <div ref={containerRef} className="absolute inset-0">
      <ForceGraph2D
        ref={fgRef}
        width={dimensions.width || undefined}
        height={dimensions.height || undefined}
        graphData={graphData}
        nodeCanvasObject={nodeCanvasObject}
        nodeCanvasObjectMode={() => 'replace'}
        linkColor={linkColor}
        linkWidth={0.5}
        linkDirectionalParticles={0}
        backgroundColor="rgba(0,0,0,0)"
        onNodeClick={(node) => onNodeClick(node as GraphNode)}
        nodePointerAreaPaint={(node: any, color, ctx) => {
          ctx.fillStyle = color
          ctx.beginPath()
          ctx.arc(node.x, node.y, (node.val || 2) * 1.5 + 4, 0, 2 * Math.PI)
          ctx.fill()
        }}
        cooldownTicks={150}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.25}
        onEngineStop={() => fgRef.current?.zoomToFit(600, 60)}
      />
      {/* Fit button */}
      <button
        onClick={() => fgRef.current?.zoomToFit(400, 40)}
        className="absolute bottom-4 right-4 w-8 h-8 flex items-center justify-center rounded-lg transition-colors"
        style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}
        title="Fit Graph"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" strokeWidth="2">
          <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
          <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
        </svg>
      </button>

      {/* Legend — top left, minimal */}
      <div className="absolute top-4 left-4 flex flex-col gap-1.5 px-2.5 py-2 rounded-lg" style={{ background: 'rgba(0,0,0,0.35)', border: '1px solid rgba(255,255,255,0.06)' }}>
        {([['#7c3aed','Topic'], ['#0891b2','Concept'], ['#3b82f6','Card']] as [string,string][]).map(([c,l]) => (
          <div key={l} className="flex items-center gap-2 text-[11px] text-slate-500">
            <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: c }} />
            {l}
          </div>
        ))}
      </div>
    </div>
  )
}
