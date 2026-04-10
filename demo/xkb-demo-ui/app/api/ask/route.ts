import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'

const XKB_DIR = '/root/.openclaw/workspace/skills/x-knowledge-base'

function runAsk(query: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn('python3', ['scripts/xkb_ask.py', query, '--json'], {
      cwd: XKB_DIR,
      env: {
        ...process.env,
        OPENCLAW_WORKSPACE: '/root/.openclaw/workspace',
        PATH: process.env.PATH || '/usr/bin:/bin',
      },
    })

    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (d) => (stdout += d))
    child.stderr.on('data', (d) => (stderr += d))
    child.on('close', (code) => {
      // xkb_ask prints "[搜尋結果]..." to stderr or stdout before JSON
      // extract the JSON block
      const jsonMatch = stdout.match(/(\{[\s\S]*\})\s*$/)
      if (jsonMatch) resolve(jsonMatch[1])
      else reject(new Error(`xkb_ask failed (code ${code}): ${stderr}`))
    })
  })
}

export async function POST(req: NextRequest) {
  try {
    const { query } = await req.json()
    if (!query || typeof query !== 'string') {
      return NextResponse.json({ error: 'query required' }, { status: 400 })
    }
    // Basic sanitization — strip shell metacharacters for safety
    const safeQuery = query.replace(/[`$\\|;&><]/g, '').slice(0, 300)
    const raw = await runAsk(safeQuery)
    const result = JSON.parse(raw)
    return NextResponse.json(result)
  } catch (err: any) {
    return NextResponse.json({ error: err.message }, { status: 500 })
  }
}
