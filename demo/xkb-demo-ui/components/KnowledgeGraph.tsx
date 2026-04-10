'use client'

import { useEffect, useRef, useCallback, useState } from 'react'
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
}

interface Props {
  highlightIds: Set<string>
  onNodeClick: (node: GraphNode) => void
  onNodesLoaded?: (nodes: GraphNode[]) => void
}

const TYPE_COLORS = {
  topic:   '#7c3aed',
  concept: '#0891b2',
  card:    '#3b82f6',
}

export default function KnowledgeGraph({ highlightIds, onNodeClick, onNodesLoaded }: Props) {
  const [graphData, setGraphData] = useState<{ nodes: GraphNode[]; links: GraphEdge[] } | null>(null)
  const fgRef = useRef<any>(null)

  useEffect(() => {
    fetch('/graph-data.json')
      .then(r => r.json())
      .then((d: GraphData) => {
        setGraphData({ nodes: d.nodes, links: d.edges })
        onNodesLoaded?.(d.nodes)
      })
  }, [onNodesLoaded])

  // Fit graph after first load
  useEffect(() => {
    if (graphData && fgRef.current) {
      setTimeout(() => fgRef.current?.zoomToFit(400, 40), 600)
    }
  }, [graphData])

  const nodeCanvasObject = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const isHighlighted = highlightIds.has(node.id)
    const baseColor = TYPE_COLORS[node.type as keyof typeof TYPE_COLORS] || '#3b82f6'
    const radius = (node.val || 2) * (isHighlighted ? 1.8 : 0.9)

    // Glow for highlighted nodes
    if (isHighlighted) {
      ctx.shadowBlur = 20
      ctx.shadowColor = baseColor
    } else {
      ctx.shadowBlur = 0
    }

    ctx.beginPath()
    ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI)
    ctx.fillStyle = isHighlighted ? baseColor : (highlightIds.size > 0 ? baseColor + '44' : baseColor + 'aa')
    ctx.fill()
    ctx.shadowBlur = 0

    // Label for topics and highlighted cards
    const showLabel = node.type === 'topic' || (isHighlighted && globalScale > 0.8) || (node.type === 'concept' && globalScale > 1.2)
    if (showLabel) {
      const label = node.label.length > 22 ? node.label.slice(0, 22) + '…' : node.label
      ctx.font = `${Math.max(10 / globalScale, 4)}px Inter, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillStyle = isHighlighted ? '#fff' : '#94a3b8'
      ctx.fillText(label, node.x, node.y + radius + 8 / globalScale)
    }
  }, [highlightIds])

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
    <div className="relative w-full h-full">
      <ForceGraph2D
        ref={fgRef}
        graphData={graphData}
        nodeCanvasObject={nodeCanvasObject}
        nodeCanvasObjectMode={() => 'replace'}
        linkColor={linkColor}
        linkWidth={0.5}
        linkDirectionalParticles={0}
        backgroundColor="#0f0f1a"
        onNodeClick={(node) => onNodeClick(node as GraphNode)}
        nodePointerAreaPaint={(node: any, color, ctx) => {
          ctx.fillStyle = color
          ctx.beginPath()
          ctx.arc(node.x, node.y, (node.val || 2) * 1.5 + 4, 0, 2 * Math.PI)
          ctx.fill()
        }}
        cooldownTicks={80}
        d3AlphaDecay={0.03}
        d3VelocityDecay={0.3}
      />
      <button
        onClick={() => fgRef.current?.zoomToFit(400, 40)}
        className="absolute bottom-3 right-3 px-3 py-1.5 text-xs bg-slate-800 hover:bg-slate-700 text-slate-400 rounded-lg border border-slate-700 transition-colors"
      >
        Fit Graph
      </button>
      {/* Legend */}
      <div className="absolute top-3 left-3 flex flex-col gap-1.5 text-xs text-slate-500">
        {[['topic','#7c3aed','Topic'], ['concept','#0891b2','Concept'], ['card','#3b82f6','Card']].map(([,c,l]) => (
          <div key={l} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full" style={{background: c as string}} />
            {l}
          </div>
        ))}
      </div>
    </div>
  )
}
