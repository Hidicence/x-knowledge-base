'use client'

import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import ReactMarkdown from 'react-markdown'

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

export interface RecallResult {
  trigger_class: 'hard' | 'soft' | 'suppress'
  state: string
  delivery_mode: string
  confidence: number
  query: string
  formatted_text: string
  results: Array<{
    source_type: string
    source_file: string
    section: string
    excerpt: string
    score: number
    url?: string
  }>
}

interface Props {
  onResult: (result: AskResult) => void
  onRecall: (result: RecallResult) => void
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

export default function ChatPanel({ onResult, onRecall }: Props) {
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

    // Fire active recall immediately (no LLM, fast) — updates Evidence panel right away
    fetch('/api/recall', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: query }),
    }).then(r => r.json()).then(onRecall).catch(() => {})

    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })
      // Parse JSON regardless of status code
      const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }))
      clearTimeout(t1); clearTimeout(t2)
      setPhase(-1)
      if (!res.ok || !data.answer) {
        const errMsg = data?.error || data?.answer || `伺服器錯誤 (${res.status})，請查看終端機 log`
        setMessages(prev => [...prev, { role: 'assistant', text: `⚠️ ${errMsg}` }])
      } else {
        setMessages(prev => [...prev, { role: 'assistant', text: data.answer, result: data }])
        onResult(data as AskResult)
      }
    } catch (err) {
      clearTimeout(t1); clearTimeout(t2)
      setPhase(-1)
      const msg = err instanceof Error ? err.message : '連線失敗，請確認 dev server 是否正在執行'
      setMessages(prev => [...prev, { role: 'assistant', text: `⚠️ ${msg}` }])
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
            <div className="flex flex-col gap-3 w-full max-w-sm">
              {DEMO_QUESTIONS.map((q, i) => (
                <motion.button
                  key={q}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.1 }}
                  whileHover={{ scale: 1.02, x: 2 }}
                  whileTap={{ scale: 0.98 }}
                  onClick={() => send(q)}
                  className="text-left text-xs px-4 py-3 rounded-xl border border-white/5 bg-slate-900/40 backdrop-blur-sm hover:border-violet-500/50 hover:bg-violet-950/40 text-slate-400 hover:text-slate-200 hover:shadow-[0_0_15px_rgba(124,58,237,0.15)] transition-all flex items-center justify-between group"
                >
                  {q}
                  <span className="text-violet-500 opacity-0 group-hover:opacity-100 transition-opacity">→</span>
                </motion.button>
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
            <div className={`max-w-[92%] rounded-2xl px-5 py-3.5 text-[13px] leading-relaxed shadow-lg ${
              msg.role === 'user'
                ? 'bg-gradient-to-br from-violet-600 to-fuchsia-600 text-white shadow-violet-500/20 rounded-tr-sm'
                : 'bg-slate-900/60 backdrop-blur-md text-slate-200 border border-white/5 shadow-black/20 rounded-tl-sm'
            }`}>
              {msg.role === 'user' ? (
                <p className="whitespace-pre-wrap">{msg.text}</p>
              ) : (
                <div className="md-body">
                  <ReactMarkdown
                    components={{
                      a: ({ href, children }) => (
                        <a href={href} target="_blank" rel="noopener noreferrer" className="text-violet-400 hover:text-violet-300 hover:underline transition-colors">
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {msg.text}
                  </ReactMarkdown>
                </div>
              )}
              {msg.result && (
                <div className="mt-3 pt-3 border-t border-white/10 text-xs text-slate-400 flex flex-wrap gap-x-4">
                  <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span> {msg.result.card_refs?.length ?? 0} cards</span>
                  <span className="flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-violet-500"></span> {msg.result.wiki_refs?.length ?? 0} topics</span>
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
            placeholder="Ask a question..."
            disabled={loading}
            className="flex-1 bg-slate-900/50 backdrop-blur-sm border border-white/10 focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/50 rounded-xl px-4 py-3 text-[13px] text-slate-200 placeholder-slate-500 outline-none transition-all disabled:opacity-50"
          />
          <button
            onClick={() => send()}
            disabled={loading || !input.trim()}
            className="px-5 py-3 rounded-xl bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white text-[13px] font-medium transition-colors hover:shadow-[0_0_15px_rgba(124,58,237,0.3)]"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
