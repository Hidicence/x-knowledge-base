import { NextRequest, NextResponse } from 'next/server'
import { exec } from 'child_process'
import { promisify } from 'util'
import { join } from 'path'

const execAsync = promisify(exec)

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
    
    const { stdout, stderr } = await execAsync(`python "${pythonScript}" "${query}" --json`, {
      env,
      cwd: workspaceDir,
    })

    if (stderr) {
      console.warn('xkb_ask.py stderr:', stderr)
    }

    // Try to parse the JSON output from the python script
    try {
      // Find where the JSON starts in case of debug outputs
      const jsonStart = stdout.indexOf('{')
      const jsonEnd = stdout.lastIndexOf('}')
      if (jsonStart >= 0 && jsonEnd >= 0) {
        const jsonStr = stdout.slice(jsonStart, jsonEnd + 1)
        const result = JSON.parse(jsonStr)
        return NextResponse.json(result)
      } else {
        throw new Error("No JSON found in output")
      }
    } catch (parseError) {
      console.error("Python script output was not valid JSON:", stdout)
      return NextResponse.json({ 
        query, 
        answer: '⚠️ Error parsing backend response. Please check terminal logs.',
        card_refs: [],
        wiki_refs: []
      }, { status: 500 })
    }

  } catch (err: any) {
    console.error("Ask API error:", err)
    return NextResponse.json({ error: err.message }, { status: 500 })
  }
}
