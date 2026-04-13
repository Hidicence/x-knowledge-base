'use client'

import { useState } from 'react'

interface GraphNode {
  id: string
  type: 'topic' | 'concept' | 'card'
  label: string
  val: number
  color: string
  description?: string
  wikiSlug?: string
  wikiSections?: Array<{ heading: string; items: Array<{ title: string; body: string }> }>
  data?: {
    title: string
    summary: string
    tags: string[]
    category: string
    source_url: string
  }
}

interface Group {
  key: string
  label: string
  color: string
  nodes: GraphNode[]
}

interface Props {
  groups: Group[]
  selectedId: string | null
  onSelect: (node: GraphNode) => void
}

export default function KnowledgeTree({ groups, selectedId, onSelect }: Props) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const toggle = (key: string) =>
    setCollapsed(prev => ({ ...prev, [key]: !prev[key] }))

  const total = groups.reduce((s, g) => s + g.nodes.length, 0)

  return (
    <div
      className="w-[200px] flex-shrink-0 flex flex-col overflow-hidden text-xs"
      style={{ background: 'var(--sidebar-bg)', borderRight: '1px solid var(--border)' }}
    >
      {/* Header */}
      <div className="flex-shrink-0 px-3 py-2.5 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
        <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Knowledge</span>
        <span className="text-[10px] text-slate-600 font-mono">{total}</span>
      </div>

      {/* Groups */}
      <div className="flex-1 overflow-y-auto py-1 space-y-0.5 scrollbar-thin">
        {groups.map(group => {
          const isCollapsed = collapsed[group.key]
          return (
            <div key={group.key}>
              {/* Group header */}
              <button
                onClick={() => toggle(group.key)}
                className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-white/[0.03] transition-colors text-left"
              >
                <svg
                  width="10" height="10" viewBox="0 0 10 10"
                  className="flex-shrink-0 transition-transform"
                  style={{ transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)', color: '#475569' }}
                >
                  <path d="M2 3 L5 7 L8 3" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: group.color }} />
                <span className="text-[11px] font-medium text-slate-400 flex-1">{group.label}</span>
                <span className="text-[10px] text-slate-600 font-mono">{group.nodes.length}</span>
              </button>

              {/* Items */}
              {!isCollapsed && (
                <div className="pb-1">
                  {group.nodes.map(node => {
                    const isSelected = node.id === selectedId
                    return (
                      <button
                        key={node.id}
                        onClick={() => onSelect(node)}
                        className="w-full text-left px-3 py-1 flex items-center gap-2 transition-colors group"
                        style={{
                          background: isSelected ? `${group.color}18` : 'transparent',
                        }}
                        onMouseEnter={e => {
                          if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.03)'
                        }}
                        onMouseLeave={e => {
                          if (!isSelected) (e.currentTarget as HTMLElement).style.background = 'transparent'
                        }}
                      >
                        <span
                          className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                          style={{ background: isSelected ? group.color : '#334155' }}
                        />
                        <span
                          className="truncate text-[11px] leading-snug"
                          style={{ color: isSelected ? '#e2e8f0' : '#64748b' }}
                        >
                          {node.label}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}

        {total === 0 && (
          <div className="px-3 py-4 text-center text-slate-700 text-[11px]">
            Loading…
          </div>
        )}
      </div>
    </div>
  )
}
