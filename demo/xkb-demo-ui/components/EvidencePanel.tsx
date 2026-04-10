'use client'

import { motion, AnimatePresence } from 'framer-motion'
import type { AskResult } from './ChatPanel'

interface Props {
  result: AskResult | null
}

export default function EvidencePanel({ result }: Props) {
  if (!result) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-4">
        <div className="w-10 h-10 rounded-full border-2 border-slate-700 flex items-center justify-center text-slate-600 text-lg">
          📎
        </div>
        <p className="text-slate-500 text-sm">提問後，召回的知識卡片會顯示在這裡</p>
      </div>
    )
  }

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={result.query}
        initial={{ opacity: 0, x: 12 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0 }}
        className="flex flex-col h-full overflow-y-auto px-4 py-3 space-y-4"
      >
        {/* Query echo */}
        <div>
          <p className="text-xs text-slate-500 mb-1">Query</p>
          <p className="text-sm text-slate-300 font-medium leading-snug">{result.query}</p>
        </div>

        {/* Card refs */}
        {result.card_refs?.length > 0 && (
          <div>
            <p className="text-xs text-slate-500 mb-2 flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-blue-500 inline-block" />
              Knowledge Cards · {result.card_refs.length}
            </p>
            <div className="flex flex-col gap-2">
              {result.card_refs.map((c, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.07 }}
                  className="rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-2.5"
                >
                  <p className="text-xs text-slate-200 leading-snug font-medium line-clamp-2">{c.title}</p>
                  {c.url && (
                    <p className="text-xs text-slate-600 mt-1 truncate">{c.url.replace(/^https?:\/\//, '')}</p>
                  )}
                </motion.div>
              ))}
            </div>
          </div>
        )}

        {/* Wiki refs */}
        {result.wiki_refs?.length > 0 && (
          <div>
            <p className="text-xs text-slate-500 mb-2 flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-violet-500 inline-block" />
              Topics · {result.wiki_refs.length}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {result.wiki_refs.map((w, i) => (
                <motion.span
                  key={i}
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: i * 0.05 }}
                  className="text-xs px-2.5 py-1 rounded-full bg-violet-950/60 border border-violet-800/50 text-violet-300"
                >
                  {w.title}
                </motion.span>
              ))}
            </div>
          </div>
        )}

        {/* Empty state */}
        {(!result.card_refs?.length && !result.wiki_refs?.length) && (
          <p className="text-xs text-slate-600 text-center pt-4">No references returned</p>
        )}
      </motion.div>
    </AnimatePresence>
  )
}
