import { NextRequest, NextResponse } from 'next/server'
import { exec } from 'child_process'
import { promisify } from 'util'
import { join } from 'path'

const execAsync = promisify(exec)

export async function POST(req: NextRequest) {
  try {
    const { message } = await req.json()
    if (!message || typeof message !== 'string') {
      return NextResponse.json({ error: 'message required' }, { status: 400 })
    }

    const workspaceDir = process.env.OPENCLAW_WORKSPACE || join(process.cwd(), '..', '..')
    const script = join(workspaceDir, 'scripts', 'recall_router.py')

    // recall_router.py is fast (no LLM), 15s timeout is plenty
    const escaped = message.replace(/"/g, '\\"')
    const { stdout, stderr } = await execAsync(
      `python "${script}" "${escaped}" --json`,
      { env: { ...process.env, OPENCLAW_WORKSPACE: workspaceDir, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }, cwd: workspaceDir, timeout: 15000 }
    )

    if (stderr) console.warn('recall_router stderr:', stderr.slice(0, 300))

    const jsonStart = stdout.indexOf('{')
    const jsonEnd   = stdout.lastIndexOf('}')
    if (jsonStart < 0 || jsonEnd < 0) {
      return NextResponse.json({ trigger_class: 'suppress', results: [], formatted_text: '', query: '', confidence: 0, state: 'suppress', delivery_mode: 'none' })
    }

    const result = JSON.parse(stdout.slice(jsonStart, jsonEnd + 1))
    return NextResponse.json(result)
  } catch (err: any) {
    console.error('Recall API error:', err)
    return NextResponse.json({ error: err.message }, { status: 500 })
  }
}
