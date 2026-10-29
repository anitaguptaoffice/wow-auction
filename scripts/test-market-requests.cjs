const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');

const source = fs.readFileSync(`${__dirname}/../frontend/src/lib/api.ts`, 'utf8').replaceAll('import.meta.env', 'ENV');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const timers = new Map();
const requests = [];
let timerId = 0;
let pending = false;
const context = {
  exports: {}, require: () => ({}), ENV: {}, URL, URLSearchParams, AbortController, DOMException,
  window: {
    location: { href: 'https://example.test/' },
    setTimeout: (fn, ms) => { assert.equal(ms, 12000); timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
  },
  fetch: (url, options) => {
    requests.push({ url: new URL(url), options });
    if (!pending) return Promise.resolve({ ok: true, json: async () => ({ items: [], total: 3421, page: 137 }) });
    return new Promise((resolve, reject) => {
      const cancel = () => reject(new DOMException('Aborted', 'AbortError'));
      if (options.signal.aborted) cancel();
      else options.signal.addEventListener('abort', cancel, { once: true });
    });
  },
};
vm.runInNewContext(compiled, context);

(async () => {
  const item = { marketKey: '[243963,0,null,2,""]', scanId: 8,
    variantFilters: { difficulty: '6', stats: '49-40', socket: 'yes', tertiary: 'avoidance' } };
  await context.exports.fetchItemListings(item, 137, 25);
  const params = requests.at(-1).url.searchParams;
  assert.equal(params.get('page'), '137');
  assert.equal(params.get('page_size'), '25');
  assert.equal(params.get('scan_id'), '8');
  assert.equal(params.get('market_key'), item.marketKey);
  assert.equal(params.get('stats'), '49-40');
  assert.equal(params.get('tertiary'), 'avoidance');
  assert.equal(timers.size, 0);

  await context.exports.fetchMarketItems({ q: '', page: 1, pageSize: 20, sort: 'price_asc', collection: '', scanId: 9 });
  assert.equal(requests.at(-1).url.searchParams.get('scan_id'), '9');
  assert.equal(requests.at(-1).url.searchParams.get('group_markets'), 'false');
  await context.exports.fetchMarketItems({ q: '', page: 1, pageSize: 20, sort: 'price_asc', collection: '' });
  assert.equal(requests.at(-1).url.searchParams.get('group_markets'), 'true');
  await context.exports.fetchItemHistory(item, undefined, 1700000000);
  assert.equal(requests.at(-1).url.searchParams.get('start'), '1700000000');
  assert.equal(requests.at(-1).url.searchParams.get('socket'), 'yes');
  assert.equal(requests.at(-1).url.searchParams.get('max_points'), '400');
  await context.exports.fetchItemComparison(item);
  assert.equal(requests.at(-1).url.searchParams.get('stats'), '49-40');

  pending = true;
  const controller = new AbortController();
  const request = context.exports.fetchItemListings(item, 2, 50, controller.signal);
  controller.abort();
  await assert.rejects(request, error => error.name === 'AbortError');
  assert.equal(requests.at(-1).options.signal.aborted, true);
  assert.equal(timers.size, 0);

  const timedOut = context.exports.fetchItemListings(item, 3, 100);
  for (const timer of [...timers.values()]) timer();
  await assert.rejects(timedOut, /请求超时，请重试/);
  assert.equal(timers.size, 0);

  const historyController = new AbortController();
  const history = context.exports.fetchItemHistory(item, historyController.signal);
  historyController.abort();
  await assert.rejects(history, error => error.name === 'AbortError');
  assert.equal(timers.size, 0);
  console.log('PASS: page/size/scan/filter parameters, cancellation of listing/history requests, bounded timeout, timer cleanup.');
})().catch(error => { console.error(error); process.exitCode = 1; });
