'use client'

import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

interface CardRef { title: string; url: string }
interface WikiRef  { slug: string; title: string }

export interface AskResult {
  query: string
  answer: string
  card_refs: CardRef[]
  wiki_refs: WikiRef[]
}

interface Message {
  role: 'user' | 'assistant'
  text: string
  result?: AskResult
}

interface Props {
  onResult: (result: AskResult) => void
}

const DEMO_QUESTIONS = [
  '醫療 AI 診斷跨器官的挑戰是什麼？',
  'AI agent 記憶系統怎麼設計？',
  'XKB 和一般 ChatGPT 差在哪？',
]

const RECALL_PHASES = [
  { label: 'Searching relevant knowledge…',  icon: '🔍' },
  { label: 'Linking related concepts…',      icon: '🕸️' },
  { label: 'Composing answer…',              icon: '✍️' },
]

export default function ChatPanel({ onResult }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [phase, setPhase] = useState(-1)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, loading])

  async function send(q?: string) {
    const query = (q ?? input).trim()
    if (!query || loading) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: query }])
    setLoading(true)
    setPhase(0)

    // Animate phases
    const phaseDelay = 900
    const t1 = setTimeout(() => setPhase(1), phaseDelay)
    const t2 = setTimeout(() => setPhase(2), phaseDelay * 2)

    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })
      const data: AskResult = await res.json()
      clearTimeout(t1); clearTimeout(t2)
      setPhase(-1)
      setMessages(prev => [...prev, { role: 'assistant', text: data.answer, result: data }])
      onResult(data)
    } catch {
      clearTimeout(t1); clearTimeout(t2)
      setPhase(-1)
      setMessages(prev => [...prev, { role: 'assistant', text: '⚠️ Error calling XKB.' }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4 min-h-0">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-6 text-center">
            <div>
              <p className="text-slate-400 text-sm mb-1">提問，從知識圖譜中召回答案</p>
              <p className="text-slate-600 text-xs">Try a demo question below</p>
            </div>
            <div className="flex flex-col gap-2 w-full max-w-sm">
              {DEMO_QUESTIONS.map(q => (
                <button
                  key={q}
                  onClick={() => send(q)}
                  className="text-left text-xs px-3 py-2 rounded-lg border border-slate-700 hover:border-violet-500 hover:bg-violet-950/30 text-slate-400 hover:text-slate-200 transition-all"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div className={`max-w-[88%] rounded-xl px-4 py-3 text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'bg-violet-600/80 text-white'
                : 'bg-slate-800/80 text-slate-200 border border-slate-700'
            }`}>
              <p className="whitespace-pre-wrap">{msg.text}</p>
              {msg.result && (
                <div className="mt-2 pt-2 border-t border-slate-600 text-xs text-slate-400 flex flex-wrap gap-x-3">
                  <span>📚 {msg.result.card_refs?.length ?? 0} cards</span>
                  <span>🗂️ {msg.result.wiki_refs?.length ?? 0} topics</span>
                </div>
              )}
            </div>
          </motion.div>
        ))}

        {/* Recall timeline */}
        <AnimatePresence>
          {loading && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="flex flex-col gap-2 px-1"
            >
              {RECALL_PHASES.map((p, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: phase >= i ? 1 : 0.2, x: 0 }}
                  transition={{ delay: i * 0.1 }}
                  className={`flex items-center gap-2 text-xs ${phase === i ? 'text-violet-400' : phase > i ? 'text-slate-500' : 'text-slate-700'}`}
                >
                  <span>{p.icon}</span>
                  <span>{p.label}</span>
                  {phase === i && (
                    <motion.span
                      animate={{ opacity: [1, 0, 1] }}
                      transition={{ repeat: Infinity, duration: 1 }}
                      className="w-1.5 h-1.5 rounded-full bg-violet-400"
                    />
                  )}
                </motion.div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex-shrink-0 px-4 pb-4 pt-2 border-t border-slate-800">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
            placeholder="問一個問題…"
            disabled={loading}
            className="flex-1 bg-slate-800 border border-slate-700 focus:border-violet-500 rounded-xl px-4 py-2.5 text-sm text-slate-200 placeholder-slate-500 outline-none transition-colors disabled:opacity-50"
          />
          <button
            onClick={() => send()}
            disabled={loading || !input.trim()}
            className="px-4 py-2.5 rounded-xl bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white text-sm font-medium transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
