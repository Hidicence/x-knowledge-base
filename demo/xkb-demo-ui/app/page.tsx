'use client'

import { useState, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'
import ChatPanel, { AskResult, RecallResult } from '@/components/ChatPanel'
import EvidencePanel from '@/components/EvidencePanel'
import KnowledgeTree from '@/components/KnowledgeTree'
import ReactMarkdown from 'react-markdown'

type RightTab = 'chat' | 'recall' | 'detail' | 'insights'

interface BridgeNode {
  id: string
  label: string
  type: string
  centrality: number
  degree: number
  topics: string[]
}

const KnowledgeGraph = dynamic(() => import('@/components/KnowledgeGraph'), { ssr: false })

interface WikiItem {
  title: string
  body: string
}
interface WikiSection {
  heading: string
  items: WikiItem[]
}
interface GraphNode {
  id: string
  type: 'topic' | 'concept' | 'card'
  label: string
  val: number
  color: string
  description?: string
  wikiSlug?: string
  wikiSections?: WikiSection[]
  data?: {
    title: string
    summary: string
    body?: string
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
  const [recallResult, setRecallResult] = useState<RecallResult | null>(null)
  const [highlightIds, setHighlightIds] = useState<Set<string>>(new Set())
  const [graphNodes, setGraphNodes] = useState<GraphNode[]>([])
  const [bridgeNodes, setBridgeNodes] = useState<BridgeNode[]>([])
  const [bridgeHighlight, setBridgeHighlight] = useState<Set<string>>(new Set())
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [activeTab, setActiveTab] = useState<RightTab>('chat')

  const handleResult = useCallback((result: AskResult) => {
    setLatestResult(result)
    setHighlightIds(buildHighlightIds(result, graphNodes))
  }, [graphNodes])

  const handleRecall = useCallback((result: RecallResult) => {
    setRecallResult(result)
    // Auto-switch to recall tab when a non-suppress trigger fires
    if (result.trigger_class !== 'suppress') setActiveTab('recall')
  }, [])

  const handleNodesLoaded = useCallback((nodes: GraphNode[], bridges: BridgeNode[]) => {
    setGraphNodes(nodes)
    setBridgeNodes(bridges)
  }, [])

  const handleNodeClick = useCallback((node: GraphNode) => {
    setSelectedNode(prev => prev?.id === node.id ? null : node)
    setActiveTab('detail')
  }, [])

  // Group nodes for the knowledge tree sidebar
  const treeGroups = useMemo(() => {
    const topics   = graphNodes.filter(n => n.type === 'topic')
    const concepts = graphNodes.filter(n => n.type === 'concept')
    const cards    = graphNodes.filter(n => n.type === 'card')
    return [
      { key: 'topic',   label: 'Topics',   color: '#7c3aed', nodes: topics },
      { key: 'concept', label: 'Concepts', color: '#0891b2', nodes: concepts },
      { key: 'card',    label: 'Cards',    color: '#3b82f6', nodes: cards },
    ]
  }, [graphNodes])

  const hasRecall = recallResult && recallResult.trigger_class !== 'suppress'

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-transparent">
      <div className="flex-1 flex min-h-0 overflow-hidden">

        {/* Icon sidebar — like llm_wiki */}
        <div className="w-12 flex-shrink-0 flex flex-col items-center py-4 gap-5 border-r" style={{ borderColor: 'var(--border)', background: 'var(--sidebar-bg)' }}>
          {/* Logo */}
          <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center text-[11px] font-bold text-white shadow-lg shadow-violet-900/40">X</div>
          <div className="w-full h-px" style={{ background: 'var(--border)' }} />
          {/* Graph icon (active) */}
          <button title="Knowledge Graph" className="w-8 h-8 rounded-lg flex items-center justify-center bg-violet-600/20 text-violet-400">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M12 3v3m0 12v3M3 12h3m12 0h3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/></svg>
          </button>
          {/* Chat icon */}
          <button title="Chat" onClick={() => setActiveTab('chat')} className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors ${activeTab === 'chat' ? 'bg-blue-600/20 text-blue-400' : 'text-slate-600 hover:text-slate-400 hover:bg-white/5'}`}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          </button>
          {/* Recall icon */}
          <button title="Active Recall" onClick={() => setActiveTab('recall')} className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors relative ${activeTab === 'recall' ? 'bg-cyan-600/20 text-cyan-400' : 'text-slate-600 hover:text-slate-400 hover:bg-white/5'}`}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-.09-5"/></svg>
            {hasRecall && <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />}
          </button>

          {/* Bottom: node count */}
          <div className="mt-auto flex flex-col items-center gap-1">
            <span className="text-[10px] text-slate-600 font-mono">{graphNodes.length}</span>
            <span className="text-[9px] text-slate-700">nodes</span>
          </div>
        </div>

        {/* Knowledge Tree sidebar */}
        <KnowledgeTree
          groups={treeGroups}
          selectedId={selectedNode?.id ?? null}
          onSelect={(node) => { setSelectedNode(node); setActiveTab('detail') }}
        />
        <div className="w-px flex-shrink-0" style={{ background: 'var(--border)' }} />

        {/* Graph — fills remaining space, no overflow clipping */}
        <div className="flex-1 relative min-w-0">
          <KnowledgeGraph
            highlightIds={highlightIds}
            bridgeIds={bridgeHighlight.size > 0 ? bridgeHighlight : new Set(bridgeNodes.map(b => b.id))}
            onNodeClick={handleNodeClick}
            onNodesLoaded={handleNodesLoaded}
          />
        </div>

        {/* Right panel divider */}
        <div className="w-px flex-shrink-0" style={{ background: 'var(--border)' }} />

        {/* Right panel */}
        <div className="w-[440px] flex-shrink-0 flex flex-col" style={{ background: 'var(--panel-bg)' }}>

          {/* Panel header with tabs */}
          <div className="flex-shrink-0 flex items-stretch border-b" style={{ borderColor: 'var(--border)' }}>
            {([
              { key: 'chat',     label: 'Ask XKB',       accent: 'border-violet-500' },
              { key: 'recall',   label: 'Recall',         accent: 'border-cyan-500' },
              { key: 'detail',   label: 'Detail',         accent: 'border-orange-500' },
              { key: 'insights', label: 'Insights',       accent: 'border-amber-500' },
            ] as { key: RightTab; label: string; accent: string }[]).map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex-1 px-2 py-3 text-xs font-medium transition-all relative border-r last:border-r-0 ${
                  activeTab === tab.key
                    ? `text-slate-200 border-b-2 ${tab.accent}`
                    : 'text-slate-500 hover:text-slate-400'
                }`}
                style={{ borderRightColor: 'var(--border)' }}
              >
                {tab.label}
                {tab.key === 'recall' && hasRecall && (
                  <span className="absolute top-2.5 right-1.5 w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                )}
                {tab.key === 'detail' && selectedNode && (
                  <span className="absolute top-2.5 right-1.5 w-1.5 h-1.5 rounded-full bg-orange-400" />
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 min-h-0 overflow-hidden relative">
            <div style={{ display: activeTab === 'chat' ? 'flex' : 'none' }} className="absolute inset-0 flex-col">
              <ChatPanel onResult={handleResult} onRecall={handleRecall} />
            </div>
            <div style={{ display: activeTab === 'recall' ? 'flex' : 'none' }} className="absolute inset-0 flex-col overflow-y-auto">
              <EvidencePanel recall={recallResult} />
            </div>
            <div style={{ display: activeTab === 'detail' ? 'flex' : 'none' }} className="absolute inset-0 flex-col overflow-y-auto">
              {selectedNode
                ? <NodeDetail node={selectedNode} onClose={() => { setSelectedNode(null); setActiveTab('chat') }} />
                : <div className="flex items-center justify-center h-full text-slate-600 text-sm">點擊圖譜節點或左側清單查看詳情</div>
              }
            </div>
            <div style={{ display: activeTab === 'insights' ? 'flex' : 'none' }} className="absolute inset-0 flex-col overflow-y-auto">
              <InsightsPanel
                bridgeNodes={bridgeNodes}
                highlightId={bridgeHighlight.size === 1 ? [...bridgeHighlight][0] : null}
                onHighlight={(id) => {
                  setBridgeHighlight(prev => {
                    const next = new Set(prev)
                    if (next.has(id)) { next.delete(id) } else { next.clear(); next.add(id) }
                    return next
                  })
                }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function NodeDetail({ node, onClose }: { node: GraphNode; onClose: () => void }) {
  const typeBadge =
    node.type === 'topic'   ? 'bg-violet-950/60 border-violet-700 text-violet-300' :
    node.type === 'concept' ? 'bg-cyan-950/60 border-cyan-700 text-cyan-300' :
                              'bg-blue-950/60 border-blue-700 text-blue-300'

  return (
    <div className="px-4 py-3 space-y-3 overflow-y-auto h-full">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-slate-200 leading-snug">{node.label}</p>
        <button onClick={onClose} className="text-slate-600 hover:text-slate-400 text-xs flex-shrink-0 mt-0.5">✕</button>
      </div>
      <span className={`inline-block text-xs px-2 py-0.5 rounded-full border ${typeBadge}`}>
        {node.type}
      </span>

      {/* Topic: show wiki sections */}
      {node.type === 'topic' && node.wikiSections && node.wikiSections.length > 0 ? (
        <div className="space-y-4 pt-1">
          {node.description && (
            <p className="text-xs text-slate-400 leading-relaxed border-l-2 border-violet-700/50 pl-3">
              {node.description}
            </p>
          )}
          {node.wikiSections.map((sec, si) => (
            <div key={si} className="space-y-2">
              <p className="text-xs font-semibold text-violet-300 uppercase tracking-wide">{sec.heading}</p>
              {sec.items.map((item, ii) => (
                <div key={ii} className="rounded-lg bg-slate-900/60 border border-white/5 px-3 py-2 space-y-1">
                  {item.title && (
                    <p className="text-xs font-medium text-slate-200">{item.title}</p>
                  )}
                  <p className="text-xs text-slate-400 leading-relaxed">{item.body}</p>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <>
          {/* Tags */}
          {node.data?.tags?.length ? (
            <div className="flex flex-wrap gap-1">
              {node.data.tags.map(t => (
                <span key={t} className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-500 border border-slate-700">{t}</span>
              ))}
            </div>
          ) : null}
          {/* Source link */}
          {node.data?.source_url && (
            <a
              href={node.data.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-violet-500 hover:text-violet-300 hover:underline truncate block transition-colors"
            >
              {node.data.source_url.replace(/^https?:\/\//, '')}
            </a>
          )}
          {/* Full body or summary fallback */}
          {node.data?.body ? (
            <div className="md-body mt-2">
              <ReactMarkdown
                components={{
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer" className="text-violet-400 hover:text-violet-300 hover:underline transition-colors">
                      {children}
                    </a>
                  ),
                }}
              >
                {node.data.body}
              </ReactMarkdown>
            </div>
          ) : node.data?.summary ? (
            <p className="text-xs text-slate-400 leading-relaxed mt-2">{node.data.summary}</p>
          ) : null}
        </>
      )}
    </div>
  )
}

function InsightsPanel({
  bridgeNodes,
  highlightId,
  onHighlight,
}: {
  bridgeNodes: BridgeNode[]
  highlightId: string | null
  onHighlight: (id: string) => void
}) {
  if (bridgeNodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-slate-600 text-sm">
        Loading insights…
      </div>
    )
  }

  return (
    <div className="px-4 py-4 space-y-5">
      {/* Header */}
      <div>
        <p className="text-sm font-semibold text-slate-200">橋接節點</p>
        <p className="text-xs text-slate-500 mt-1 leading-relaxed">
          同時連結多個知識領域的關鍵概念。點擊可在圖譜上高亮該節點及其連線。
        </p>
      </div>

      {/* Bar chart + list */}
      <div className="space-y-2">
        {bridgeNodes.map((b, i) => {
          const isActive = highlightId === b.id
          const maxScore = bridgeNodes[0]?.centrality ?? 1
          const pct = Math.round((b.centrality / maxScore) * 100)
          return (
            <button
              key={b.id}
              onClick={() => onHighlight(b.id)}
              className="w-full text-left rounded-xl px-3 py-2.5 space-y-1.5 transition-all"
              style={{
                background: isActive ? 'rgba(245,158,11,0.1)' : 'rgba(255,255,255,0.02)',
                border: `1px solid ${isActive ? 'rgba(245,158,11,0.4)' : 'rgba(255,255,255,0.06)'}`,
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-[10px] text-slate-600 font-mono w-3 flex-shrink-0">{i + 1}</span>
                  <span className={`text-xs font-medium truncate ${isActive ? 'text-amber-300' : 'text-slate-300'}`}>
                    {b.label}
                  </span>
                </div>
                <span className="text-[10px] text-slate-600 flex-shrink-0 font-mono">{b.degree} links</span>
              </div>
              {/* Progress bar */}
              <div className="h-1 rounded-full bg-slate-800 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${pct}%`,
                    background: isActive ? '#f59e0b' : '#475569',
                  }}
                />
              </div>
              {/* Connected topics */}
              {b.topics.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {b.topics.map(t => (
                    <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500">
                      {t.length > 20 ? t.slice(0, 20) + '…' : t}
                    </span>
                  ))}
                </div>
              )}
            </button>
          )
        })}
      </div>

      <p className="text-[10px] text-slate-700 leading-relaxed">
        介數中心性（betweenness centrality）越高，代表越多知識路徑經過此節點。
      </p>
    </div>
  )
}
