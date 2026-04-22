'use client'

import { motion, AnimatePresence } from 'framer-motion'
import type { RecallResult } from './ChatPanel'

interface Props {
  recall: RecallResult | null
}

const TRIGGER_META = {
  hard: { label: 'Hard Trigger', color: 'text-amber-300', bg: 'bg-amber-900/30 border-amber-600/40', dot: 'bg-amber-400', desc: '進度 / 定義 / Roadmap 查詢' },
  soft: { label: 'Soft Trigger', color: 'text-emerald-300', bg: 'bg-emerald-900/30 border-emerald-600/40', dot: 'bg-emerald-400', desc: '關聯召回 / 策略 / 案例' },
  suppress: { label: 'Suppressed',   color: 'text-slate-500',  bg: 'bg-slate-800/30 border-slate-700/40',   dot: 'bg-slate-600',  desc: '輕量對話，不召回' },
}

const SOURCE_META: Record<string, { label: string; color: string; border: string }> = {
  wiki:        { label: 'Wiki',       color: 'text-violet-300', border: 'border-violet-500/30' },
  memory:      { label: 'Memory',     color: 'text-blue-300',   border: 'border-blue-500/30'   },
  bookmark:    { label: 'Bookmark',   color: 'text-cyan-300',   border: 'border-cyan-500/30'   },
  contrarian:  { label: '⚠ 反例',    color: 'text-rose-300',   border: 'border-rose-500/40'   },
  action:      { label: '🔧 可復用',  color: 'text-amber-300',  border: 'border-amber-500/30'  },
}

export default function EvidencePanel({ recall }: Props) {
  if (!recall) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-4">
        <div className="w-12 h-12 rounded-full border border-white/10 bg-slate-900/50 backdrop-blur-sm shadow-xl flex items-center justify-center text-slate-500 text-xl overflow-hidden relative">
          <div className="absolute inset-0 bg-violet-500/10 blur-xl" />
          <span className="relative z-10 text-sm font-bold text-slate-600">XKB</span>
        </div>
        <div className="space-y-1">
          <p className="text-slate-400 text-sm font-medium">主動召回待機中</p>
          <p className="text-slate-600 text-xs">輸入任何訊息，系統自動判斷是否召回相關知識</p>
        </div>
      </div>
    )
  }

  const meta = TRIGGER_META[recall.trigger_class] ?? TRIGGER_META.suppress

  // Group results by source_type
  const byType: Record<string, typeof recall.results> = {}
  for (const r of recall.results ?? []) {
    ;(byType[r.source_type] ??= []).push(r)
  }

  const hasResults = (recall.results ?? []).length > 0

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={recall.query}
        initial={{ opacity: 0, x: 12 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0 }}
        className="flex flex-col h-full overflow-y-auto px-4 py-3 space-y-4"
      >
        {/* Trigger badge */}
        <div className={`rounded-xl border px-3 py-2.5 ${meta.bg} space-y-1`}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${meta.dot} animate-pulse`} />
              <span className={`text-xs font-semibold ${meta.color}`}>{meta.label}</span>
            </div>
            <span className="text-xs text-slate-500">{Math.round(recall.confidence * 100)}% conf</span>
          </div>
          <p className="text-xs text-slate-400">{meta.desc}</p>
          {recall.query && recall.trigger_class !== 'suppress' && (
            <p className="text-xs text-slate-500 font-mono truncate">query: {recall.query}</p>
          )}
        </div>

        {/* Suppress state */}
        {recall.trigger_class === 'suppress' && (
          <p className="text-xs text-slate-600 text-center pt-2">
            此訊息為輕量對話，系統判斷不需要召回知識。
          </p>
        )}

        {/* Results grouped by source */}
        {hasResults && Object.entries(byType).map(([srcType, items]) => {
          const sm = SOURCE_META[srcType] ?? { label: srcType, color: 'text-slate-300', border: 'border-slate-700' }
          return (
            <div key={srcType}>
              <p className={`text-xs font-semibold uppercase tracking-wide mb-2 ${sm.color} flex items-center gap-1.5`}>
                <span className={`w-1.5 h-1.5 rounded-full inline-block ${sm.color.replace('text-', 'bg-')}`} />
                {sm.label} · {items.length}
              </p>
              <div className="flex flex-col gap-2">
                {items.map((r, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.06 }}
                    className={`rounded-xl border ${sm.border} bg-slate-900/50 px-3 py-2.5 space-y-1`}
                  >
                    {r.section && (
                      <p className={`text-xs font-medium ${sm.color}`}>{r.section}</p>
                    )}
                    {r.excerpt && (
                      <p className="text-xs text-slate-400 leading-relaxed line-clamp-3">{r.excerpt}</p>
                    )}
                    <p className="text-[10px] text-slate-600 truncate">{r.source_file}</p>
                  </motion.div>
                ))}
              </div>
            </div>
          )
        })}

        {/* No results but triggered */}
        {!hasResults && recall.trigger_class !== 'suppress' && (
          <p className="text-xs text-slate-600 text-center pt-2">
            已觸發召回，但未找到相關知識片段。
          </p>
        )}
      </motion.div>
    </AnimatePresence>
  )
}
