// Read-only protocol: only a confirmed mismatch permits repair.
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
    const slug = process.argv[3];
    let expected: any;
    try {
      if (process.argv[2] !== '--stdin' || !slug) throw new Error('arguments');
      expected = parseMarkdown(await Bun.stdin.text(), slug + '.md');
    } catch {
      console.log(JSON.stringify({status: 'invalid_card'}));
      process.exitCode = 3;
    }
    if (expected) {
      const verified = await sql.begin('read only', async tx => {
        const p = await tx`SELECT id,compiled_truth,frontmatter FROM pages WHERE source_id='default' AND slug=${slug} AND deleted_at IS NULL`;
        if (p.length !== 1 || p[0].compiled_truth !== expected.compiled_truth) return false;
        for (const key of ['id', 'tweet_id', 'post_id', 'source_url']) {
          if (expected.frontmatter[key] !== undefined && String(p[0].frontmatter[key]) !== String(expected.frontmatter[key])) return false;
        }
        const c = await tx`SELECT count(*)::int AS n,count(*) FILTER(WHERE embedding IS NOT NULL)::int AS e FROM content_chunks WHERE page_id=${p[0].id}`;
        return c[0].n > 0 && c[0].n === c[0].e;
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
