import { NextRequest, NextResponse } from 'next/server'
import { execFile } from 'child_process'
import { promisify } from 'util'
import { join } from 'path'

const execFileAsync = promisify(execFile)

export async function POST(req: NextRequest) {
  try {
    const { query } = await req.json()
    if (!query || typeof query !== 'string') {
      return NextResponse.json({ error: 'query required' }, { status: 400 })
    }

    // Default to workspace root assuming we run NPM from demo/xkb-demo-ui
    const workspaceDir = process.env.OPENCLAW_WORKSPACE || join(process.cwd(), '..', '..')
    const pythonScript = join(workspaceDir, 'scripts', 'xkb_ask.py')

    console.log(`Executing: python ${pythonScript} "${query}" --json`)

    // We pass any LLM API keys via environment variables
    const env = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', OPENCLAW_WORKSPACE: workspaceDir }

    // Use execFile with args array to avoid shell quoting issues on Windows
    const { stdout, stderr } = await execFileAsync(
      'python',
      [pythonScript, query, '--json'],
      { env, cwd: workspaceDir, timeout: 60000 }
    )

    if (stderr) {
      console.warn('xkb_ask.py stderr:', stderr.slice(0, 500))
    }

    // Try to parse the JSON output from the python script
    const jsonStart = stdout.indexOf('{')
    const jsonEnd = stdout.lastIndexOf('}')
    if (jsonStart >= 0 && jsonEnd >= 0) {
      try {
        const result = JSON.parse(stdout.slice(jsonStart, jsonEnd + 1))
        return NextResponse.json(result)
      } catch (parseError) {
        console.error("Python script output was not valid JSON:", stdout.slice(0, 300))
        return NextResponse.json({
          query,
          answer: '⚠️ Error parsing backend response. Please check terminal logs.',
          card_refs: [],
          wiki_refs: []
        }, { status: 500 })
      }
    } else {
      console.error("No JSON found in xkb_ask output:", stdout.slice(0, 300))
      return NextResponse.json({
        query,
        answer: '⚠️ No response from knowledge base. Please check terminal logs.',
        card_refs: [],
        wiki_refs: []
      }, { status: 500 })
    }

  } catch (err: any) {
    console.error("Ask API error:", err?.message || err)
    if (err?.stdout) console.error("stdout:", String(err.stdout).slice(0, 300))
    if (err?.stderr) console.error("stderr:", String(err.stderr).slice(0, 500))
    return NextResponse.json({ error: err?.message || 'Unknown error' }, { status: 500 })
  }
}
