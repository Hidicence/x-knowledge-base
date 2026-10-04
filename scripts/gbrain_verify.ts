// Read-only protocol: only a confirmed mismatch permits repair.
import {isDeepStrictEqual} from 'node:util';
import {pathToFileURL} from 'node:url';
const root = process.env.GBRAIN_DIR!;
let sql: any;
try {
  const {loadConfig} = await import(pathToFileURL(root + '/src/core/config.ts').href);
  const {default: postgres} = await import(pathToFileURL(root + '/node_modules/postgres/src/index.js').href);
  sql = postgres(loadConfig().database_url, {max: 1, onnotice: () => {}});
  if (process.argv[2] === '--probe') {
    await sql.begin('read only', async tx => { await tx`SELECT 1`; });
    console.log(JSON.stringify({status: 'ready'}));
  } else {
    const {parseMarkdown} = await import(pathToFileURL(root + '/src/core/markdown.ts').href);
    const {chunkText} = await import(pathToFileURL(root + '/src/core/chunkers/recursive.ts').href);
    const slug = process.argv[3];
    const content = await Bun.stdin.text();
    let expected: any;
    try {
      if (process.argv[2] !== '--stdin' || !slug) throw new Error('arguments');
      expected = parseMarkdown(content, slug + '.md');
    } catch {
      console.log(JSON.stringify({status: 'invalid_card'}));
      process.exitCode = 3;
    }
    if (expected) {
      const verified = await sql.begin('read only', async tx => {
        const p = await tx`SELECT id,compiled_truth,timeline,title,type,frontmatter FROM pages WHERE source_id='default' AND slug=${slug} AND deleted_at IS NULL`;
        if (p.length !== 1 || p[0].compiled_truth !== expected.compiled_truth || p[0].timeline !== expected.timeline) return false;
        // Compare declared metadata, allowing backend-owned extra metadata.
        const frontmatter = content.match(/^---\r?\n([\s\S]*?)\r?\n---/m)?.[1] || '';
        for (const key of ['title', 'type']) {
          if (new RegExp('^' + key + ':', 'm').test(frontmatter) && p[0][key] !== expected[key]) return false;
        }
        for (const [key, value] of Object.entries(expected.frontmatter)) {
          if (!isDeepStrictEqual(p[0].frontmatter[key], JSON.parse(JSON.stringify(value)))) return false;
        }
        const tags = await tx`SELECT tag FROM tags WHERE page_id=${p[0].id} ORDER BY tag`;
        if (!isDeepStrictEqual(tags.map(t => t.tag).sort(), [...new Set(expected.tags)].sort())) return false;
        const chunks = await tx`SELECT chunk_index,chunk_text,chunk_source,embedding IS NOT NULL AS embedded FROM content_chunks WHERE page_id=${p[0].id} ORDER BY chunk_index`;
        if (!chunks.length || chunks.some(c => !c.embedded)) return false;
        // Reproduce canonical text chunking. Extra fenced-code chunks are allowed,
        // but every body/timeline chunk must exist, in order, with matching text.
        const expectedChunks = ['compiled_truth', 'timeline'].flatMap(source =>
          expected[source].trim() ? chunkText(expected[source]).map(c => ({text: c.text, source})) : []);
        const textChunks = chunks.filter(c => ['compiled_truth', 'timeline'].includes(c.chunk_source));
        return textChunks.length === expectedChunks.length && textChunks.every((c, i) =>
          c.chunk_index === i && c.chunk_text === expectedChunks[i].text && c.chunk_source === expectedChunks[i].source);
      });
      console.log(JSON.stringify({status: verified ? 'verified' : 'mismatch'}));
      if (!verified) process.exitCode = 1;
    }
  }
} catch {
  console.log(JSON.stringify({status: 'unavailable'}));
  process.exitCode = 2;
} finally {
  if (sql) await sql.end();
}
