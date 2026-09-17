import app from './index.js';
import dataset from './download-manifest.js';

const worker = {
  async fetch(request, env, ctx) {
    if (new URL(request.url).pathname !== '/download') return app.fetch(request, env, ctx);
    if (!['GET', 'HEAD'].includes(request.method)) return new Response('Method not allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
    const headers = {
      'Content-Type': 'application/gzip',
      'Content-Disposition': `attachment; filename="${dataset.filename}"`,
      'Content-Length': String(dataset.bytes),
      'Cache-Control': 'public, max-age=3600',
      ETag: `"${dataset.sha256}"`,
    };
    if (request.method === 'HEAD') return new Response(null, { headers });
    // Native stream piping avoids per-byte JavaScript work within the 10 ms CPU budget.
    const { readable, writable } = new FixedLengthStream(dataset.bytes);
    const transfer = (async () => {
      for (const part of dataset.parts) {
        const response = await env.ASSETS.fetch(new Request(new URL(part.path, request.url)));
        if (!response.ok || !response.body) throw new Error('Dataset part unavailable');
        await response.body.pipeTo(writable, { preventClose: true });
      }
      await writable.getWriter().close();
    })().catch(async error => { await writable.abort(error); });
    ctx.waitUntil(transfer);
    return new Response(readable, { headers });
  },
};

export default worker;
