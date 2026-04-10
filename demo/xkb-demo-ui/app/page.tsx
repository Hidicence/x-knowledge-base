'use client'

import { useState, useCallback } from 'react'
import dynamic from 'next/dynamic'
import ChatPanel, { AskResult } from '@/components/ChatPanel'
import EvidencePanel from '@/components/EvidencePanel'

const KnowledgeGraph = dynamic(() => import('@/components/KnowledgeGraph'), { ssr: false })

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
}

function buildHighlightIds(result: AskResult, nodes: GraphNode[]): Set<string> {
  const ids = new Set<string>()

  // Match card_refs titles against card node labels
  for (const ref of result.card_refs ?? []) {
    const refTitle = ref.title.toLowerCase()
    for (const node of nodes) {
      if (node.type !== 'card') continue
      const nodeLabel = node.label.toLowerCase()
      if (nodeLabel.includes(refTitle.slice(0, 20)) || refTitle.includes(nodeLabel.slice(0, 20))) {
        ids.add(node.id)
      }
    }
  }

  // Match wiki_refs slugs/titles against topic/concept nodes
  for (const ref of result.wiki_refs ?? []) {
    const slug = ref.slug.toLowerCase()
    const title = ref.title.toLowerCase()
    for (const node of nodes) {
      if (node.type === 'card') continue
      const nodeLabel = node.label.toLowerCase()
      if (node.id === `topic-${slug}` || nodeLabel.includes(title) || title.includes(nodeLabel)) {
        ids.add(node.id)
      }
    }
  }

  return ids
}

export default function Page() {
  const [latestResult, setLatestResult] = useState<AskResult | null>(null)
  const [highlightIds, setHighlightIds] = useState<Set<string>>(new Set())
  const [graphNodes, setGraphNodes] = useState<GraphNode[]>([])
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)

  const handleResult = useCallback((result: AskResult) => {
    setLatestResult(result)
    setHighlightIds(buildHighlightIds(result, graphNodes))
  }, [graphNodes])

  const handleNodesLoaded = useCallback((nodes: GraphNode[]) => {
    setGraphNodes(nodes)
  }, [])

  const handleNodeClick = useCallback((node: GraphNode) => {
    setSelectedNode(prev => prev?.id === node.id ? null : node)
  }, [])

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-[#0a0a0f]">
      {/* Header */}
      <header className="flex-shrink-0 flex items-center justify-between px-6 py-3 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 rounded-md bg-violet-600 flex items-center justify-center text-xs font-bold text-white">X</div>
          <span className="text-sm font-semibold text-slate-200 tracking-wide">XKB · Knowledge Recall</span>
        </div>
        <div className="flex items-center gap-4 text-xs text-slate-500">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            Live
          </span>
          <span>{graphNodes.length} nodes</span>
        </div>
      </header>

      {/* Three-column body */}
      <div className="flex-1 grid grid-cols-[1fr_380px_280px] gap-3 p-3 min-h-0">
        {/* Left: Knowledge Graph */}
        <div className="panel glow-purple rounded-xl overflow-hidden">
          <KnowledgeGraph
            highlightIds={highlightIds}
            onNodeClick={handleNodeClick}
            onNodesLoaded={handleNodesLoaded}
          />
        </div>

        {/* Middle: Chat */}
        <div className="panel glow-blue rounded-xl overflow-hidden flex flex-col">
          <div className="flex-shrink-0 px-4 py-2.5 border-b border-slate-800">
            <p className="text-xs text-slate-400 font-medium">Ask XKB</p>
          </div>
          <div className="flex-1 min-h-0">
            <ChatPanel onResult={handleResult} />
          </div>
        </div>

        {/* Right: Evidence */}
        <div className="panel glow-cyan rounded-xl overflow-hidden flex flex-col">
          <div className="flex-shrink-0 px-4 py-2.5 border-b border-slate-800">
            <p className="text-xs text-slate-400 font-medium">Evidence</p>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto">
            {selectedNode ? (
              <NodeDetail node={selectedNode} onClose={() => setSelectedNode(null)} />
            ) : (
              <EvidencePanel result={latestResult} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function NodeDetail({ node, onClose }: { node: GraphNode; onClose: () => void }) {
  return (
    <div className="px-4 py-3 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-slate-200 leading-snug">{node.label}</p>
        <button onClick={onClose} className="text-slate-600 hover:text-slate-400 text-xs flex-shrink-0 mt-0.5">✕</button>
      </div>
      <span className={`inline-block text-xs px-2 py-0.5 rounded-full border ${
        node.type === 'topic'   ? 'bg-violet-950/60 border-violet-700 text-violet-300' :
        node.type === 'concept' ? 'bg-cyan-950/60 border-cyan-700 text-cyan-300' :
                                  'bg-blue-950/60 border-blue-700 text-blue-300'
      }`}>
        {node.type}
      </span>
      {node.data?.summary && (
        <p className="text-xs text-slate-400 leading-relaxed">{node.data.summary}</p>
      )}
      {node.data?.tags?.length ? (
        <div className="flex flex-wrap gap-1">
          {node.data.tags.map(t => (
            <span key={t} className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-500 border border-slate-700">{t}</span>
          ))}
        </div>
      ) : null}
      {node.data?.source_url && (
        <p className="text-xs text-slate-600 truncate">{node.data.source_url.replace(/^https?:\/\//, '')}</p>
      )}
    </div>
  )
}
