import assert from 'node:assert/strict';
import { after, afterEach, before, test } from 'node:test';
import relay from './relay.mjs';

// Exercise the public boundary, without importing implementation constants.
const ORIGIN = 'https://epg.2560801.xyz';
const IP = '198.51.100.7';
const QUERY = 'channelId=ren%2Ftv&shift=0&hours=0&generation=snapshot-1';
const encoder = new TextEncoder();
const originalFetch = globalThis.fetch;
let publicFetchCalls = 0;
before(() => {
  globalThis.fetch = async () => {
    publicFetchCalls += 1;
    throw new Error('Public fetch must never be used by the relay');
  };
});
afterEach(() => assert.equal(publicFetchCalls, 0, 'no public-network fallback'));
after(() => { globalThis.fetch = originalFetch; });

function request(path = '/epg/v1/match', options = {}) {
  const method = options.method ?? (path.startsWith('/epg/v1/programmes') ? 'GET' : 'POST');
  const headers = { 'CF-Connecting-IP': IP, ...options.headers };
  if (method === 'POST' && !Object.hasOwn(headers, 'Content-Type')) headers['Content-Type'] = 'application/json';
  return new Request(ORIGIN + path, {
    ...options, method, headers,
    ...(method === 'POST' && !Object.hasOwn(options, 'body') ? { body: '{"version":1}' } : {}),
  });
}

function response(body = '{"ok":true}', options = {}) {
  return new Response(body, { status: 200, ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers } });
}

function fixture(backend = () => response()) {
  const calls = [];
  const limits = [];
  const env = { PUBLIC_HOST: 'epg.2560801.xyz', BACKEND: { async fetch(input, init) {
    const req = new Request(input, init);
    calls.push({ input, init, request: req });
    return backend(req, calls.length);
  } } };
  for (const name of ['MATCH_LIMITER', 'PROGRAMME_LIMITER', 'CURRENT_LIMITER']) {
    env[name] = { async limit(value) { limits.push({ name, value }); return { success: true }; } };
  }
  return { env, calls, limits };
}

async function drain(value) { await value.arrayBuffer(); return value; }
async function settle() {
  // Let Web Streams and the promise chain run without advancing the deadline.
  await new Promise(resolve => setImmediate(resolve));
}

function tinyInput({ end = Infinity, stallAt = Infinity, byteAt = index => 65 + index % 26 } = {}) {
  const state = { produced: 0, cancelled: false };
  state.stream = new ReadableStream({
    pull(controller) {
      if (state.produced === end) { controller.close(); return; }
      if (state.produced === stallAt) return new Promise(() => {});
      // Each pull owns a distinct allocation: transport fragmentation must not
      // change either the byte bound or the body forwarded to the service.
      controller.enqueue(new Uint8Array([byteAt(state.produced++)]));
    },
    cancel() { state.cancelled = true; },
  }, { highWaterMark: 0 });
  return state;
}

test('only the three public routes reach the bound service; bytes and trusted limiter key survive', async () => {
  for (const [path, name] of [
    ['/epg/v1/match', 'MATCH_LIMITER'], ['/epg/v1/current', 'CURRENT_LIMITER'],
    ['/epg/v1/programmes?' + QUERY, 'PROGRAMME_LIMITER'],
  ]) {
    const body = encoder.encode(' { "name": "РЕН ТВ 📺", "number": 1.00 }\n');
    const f = fixture();
    const options = path.includes('programmes') ? {} : { body };
    assert.equal((await drain(await relay.fetch(request(path, options), f.env))).status, 200);
    assert.equal(f.calls.length, 1);
    assert.deepEqual(f.limits, [{ name, value: { key: IP } }]);
    const outbound = f.calls[0].request;
    assert.equal(outbound.method, path.includes('programmes') ? 'GET' : 'POST');
    assert.equal(new URL(outbound.url).pathname, path.split('?')[0]);
    assert.equal(f.calls[0].init.redirect, 'manual');
    if (options.body) assert.deepEqual(new Uint8Array(await outbound.arrayBuffer()), body);
  }
});

test('host, scheme, port, route, method and POST query/fragment violations never reach backend', async () => {
  const badOrigin = url => new Request(url, { method: 'POST', body: '{}',
    headers: { 'CF-Connecting-IP': IP, 'Content-Type': 'application/json' } });
  const invalid = [
    badOrigin('http://epg.2560801.xyz/epg/v1/match'),
    badOrigin('https://attacker.example/epg/v1/match'),
    badOrigin('https://epg.2560801.xyz:444/epg/v1/match'),
    request('/epg/v1/health'), request('/epg/v1/match/'), request('/epg/v1/%6datch'),
    request('/epg/v1/match?source=other'), request('/epg/v1/current#fragment'),
    request('/epg/v1/programmes?' + QUERY + '#fragment'),
    request('/epg/v1/match', { method: 'GET' }),
    request('/epg/v1/programmes?' + QUERY, { method: 'POST' }),
    request('/internal'), request('/'),
  ];
  for (const req of invalid) {
    const f = fixture();
    const result = await drain(await relay.fetch(req, f.env));
    assert.equal(result.status, 404, req.method + ' ' + req.url);
    assert.equal(f.calls.length, 0, req.url);
  }
});

test('programme query admits encoded values and exact Rust integer boundaries', async () => {
  for (const [shift, hours] of [['-86400', '0'], ['+86400', '+8784'], ['0003600', '0001']]) {
    const query = new URLSearchParams({ channelId: 'РЕН / + 📺', shift, hours, generation: 'gen/+ 😀' });
    const f = fixture();
    assert.equal((await drain(await relay.fetch(request('/epg/v1/programmes?' + query), f.env))).status, 200);
    const sent = new URL(f.calls[0].request.url).searchParams;
    for (const [key, value] of query) assert.equal(sent.get(key), value);
  }
});

test('programme query rejects duplicate decoded keys, unknown/missing fields and invalid integers', async () => {
  const invalid = [QUERY + '&channelId=another', QUERY + '&%63hannelId=another', QUERY + '&extra=1',
    QUERY.replace('&generation=snapshot-1', ''), QUERY.replace('channelId=ren%2Ftv', 'channelId='),
    QUERY.replace('generation=snapshot-1', 'generation=')];
  for (const value of ['86401', '-90000', '1', '1.0', '1e3', '0x0', ' 0', '0 ', '--0', 'NaN']) {
    invalid.push(QUERY.replace('shift=0', 'shift=' + encodeURIComponent(value)));
  }
  for (const value of ['8785', '-1', '-0', '1.0', '1e3', '0x0', ' 0', 'Infinity']) {
    invalid.push(QUERY.replace('hours=0', 'hours=' + encodeURIComponent(value)));
  }
  for (const query of invalid) {
    const f = fixture();
    assert.equal((await drain(await relay.fetch(request('/epg/v1/programmes?' + query), f.env))).status, 400, query);
    assert.equal(f.calls.length, 0, query);
  }
});

test('channelId is bounded in UTF-16 units, generation in UTF-8 bytes', async () => {
  for (const [key, atLimit, above] of [
    ['channelId', '📺'.repeat(256), '📺'.repeat(256) + 'x'],
    ['generation', '📺'.repeat(20), '📺'.repeat(20) + 'x'],
  ]) {
    for (const [value, status] of [[atLimit, 200], [above, 400]]) {
      const query = new URLSearchParams(QUERY);
      query.set(key, value);
      const f = fixture();
      assert.equal((await drain(await relay.fetch(request('/epg/v1/programmes?' + query), f.env))).status, status);
      assert.equal(f.calls.length, status === 200 ? 1 : 0);
    }
  }
});

test('POST body is opaque JSON media with identity encoding and a 512 KiB wire limit', async () => {
  for (const length of [512 * 1024, 512 * 1024 + 1]) {
    const body = new Uint8Array(length).fill(32);
    body[0] = 91; body[length - 1] = 93;
    const f = fixture();
    const result = await drain(await relay.fetch(request('/epg/v1/match', { body }), f.env));
    assert.equal(result.status, length === 512 * 1024 ? 200 : 413);
    assert.equal(f.calls.length, length === 512 * 1024 ? 1 : 0);
  }
  for (const headers of [{ 'Content-Type': 'text/plain' }, { 'Content-Encoding': 'gzip' },
    { 'Content-Length': String(512 * 1024 + 1) }]) {
    const f = fixture();
    assert.ok((await drain(await relay.fetch(request('/epg/v1/current', { headers }), f.env))).status >= 400);
    assert.equal(f.calls.length, 0);
  }
  // The service, not a second JavaScript parser, owns JSON/schema validation.
  const f = fixture();
  await drain(await relay.fetch(request('/epg/v1/match', { body: 'not-json' }), f.env));
  assert.equal(await f.calls[0].request.text(), 'not-json');
});

test('512 KiB fragmented into distinct one-byte chunks reaches the service byte-for-byte', async () => {
  const size = 512 * 1024;
  const expected = Uint8Array.from({ length: size }, (_, index) =>
    index === 0 || index === size - 1 ? 34 : 65 + index % 26);
  const input = tinyInput({ end: size, byteAt: index => expected[index] });
  const f = fixture();
  const result = await relay.fetch(request('/epg/v1/match', {
    body: input.stream, duplex: 'half',
  }), f.env);
  assert.equal((await drain(result)).status, 200);
  assert.equal(input.produced, size);
  assert.equal(f.calls.length, 1);
  assert.deepEqual(new Uint8Array(await f.calls[0].request.arrayBuffer()), expected);
});

test('one-byte chunk overflow cancels input at the wire limit and never reaches the service', async () => {
  const input = tinyInput();
  const f = fixture();
  const result = await relay.fetch(request('/epg/v1/current', {
    body: input.stream, duplex: 'half',
  }), f.env);
  assert.equal((await drain(result)).status, 413);
  assert.equal(input.produced, 512 * 1024 + 1);
  assert.equal(input.cancelled, true);
  assert.equal(f.calls.length, 0);
});

test('fragmented partial input still obeys the shared deadline and client disconnect', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const mode of ['deadline', 'disconnect']) {
    const input = tinyInput({ stallAt: 4096 });
    const abort = new AbortController();
    const f = fixture();
    const pending = relay.fetch(request('/epg/v1/match', {
      body: input.stream, duplex: 'half', signal: abort.signal,
    }), f.env);
    await settle();
    assert.equal(input.produced, 4096);
    if (mode === 'deadline') t.mock.timers.tick(12001);
    else abort.abort();
    const result = await pending;
    assert.equal((await drain(result)).status, mode === 'deadline' ? 504 : 502);
    assert.equal(input.cancelled, true, mode);
    assert.equal(f.calls.length, 0, mode);
  }
});

test('configuration, identity and limiter failures are closed before backend access', async () => {
  for (const mutate of [
    f => { delete f.env.BACKEND; }, f => { delete f.env.PUBLIC_HOST; },
    f => { f.env.PUBLIC_HOST = 'https://epg.2560801.xyz/private'; },
    f => { f.env.BACKEND = {}; },
    f => { delete f.env.MATCH_LIMITER; },
    f => { f.env.MATCH_LIMITER.limit = async () => { throw new Error('PRIVATE_LIMITER_FAILURE'); }; },
    f => { f.env.MATCH_LIMITER.limit = async () => ({}); },
    f => { f.env.MATCH_LIMITER.limit = async () => ({ success: 'true' }); },
  ]) {
    const f = fixture(); mutate(f);
    const result = await relay.fetch(request(), f.env);
    assert.equal(result.status, 503);
    assert.doesNotMatch(await result.text(), /PRIVATE_LIMITER_FAILURE/);
    assert.equal(f.calls.length, 0);
  }
  const f = fixture();
  const missing = request(); missing.headers.delete('CF-Connecting-IP');
  assert.equal((await drain(await relay.fetch(missing, f.env))).status, 503);
  assert.equal(f.calls.length, 0);
  f.env.MATCH_LIMITER.limit = async () => ({ success: false });
  assert.equal((await drain(await relay.fetch(request(), f.env))).status, 429);
  assert.equal(f.calls.length, 0);
});

test('trusted client identity accepts IPv6 but rejects ambiguous or malformed header values', async () => {
  const valid = ['2001:db8::7', '::1', '::ffff:192.0.2.1'];
  for (const value of [...valid, 'not-an-ip', '198.51.100.7, 203.0.113.5', '999.1.1.1',
    '::::', ':', '1:2:3:4:5:6:7:8:9', 'deadbeef', '1.1.1', 'fe80::1%lo0']) {
    const f = fixture();
    const result = await drain(await relay.fetch(request('/epg/v1/match', {
      headers: { 'CF-Connecting-IP': value },
    }), f.env));
    assert.equal(result.status, valid.includes(value) ? 200 : 503, value);
    if (valid.includes(value)) assert.equal(f.limits[0].value.key, value);
    else assert.equal(f.calls.length, 0);
  }
});

test('neither credentials nor arbitrary request/response headers cross the boundary', async () => {
  const f = fixture(() => response('{"ok":true}', { headers: {
    'Set-Cookie': 'PRIVATE_COOKIE=secret', 'Location': 'https://private.example/',
    'Server': 'PRIVATE_SERVER', 'X-Internal': 'PRIVATE_INTERNAL', 'CF-Ray': 'private-ray',
    'Access-Control-Allow-Origin': '*', 'Retry-After': '99', 'Cache-Control': 'public, max-age=86400',
  } }));
  const result = await relay.fetch(request('/epg/v1/match', { headers: {
    'Authorization': 'Bearer PRIVATE_TOKEN', 'Cookie': 'PRIVATE_COOKIE=secret',
    'X-Forwarded-For': '203.0.113.1', 'Forwarded': 'for=203.0.113.1',
    'X-Internal': 'PRIVATE_INTERNAL', 'Origin': 'https://attacker.example',
  } }), f.env);
  await drain(result);
  for (const name of ['authorization', 'cookie', 'x-forwarded-for', 'forwarded', 'x-internal', 'origin']) {
    assert.equal(f.calls[0].request.headers.get(name), null, name);
  }
  for (const name of ['set-cookie', 'location', 'server', 'x-internal', 'cf-ray', 'access-control-allow-origin', 'retry-after']) {
    assert.equal(result.headers.get(name), null, name);
  }
  assert.equal(result.headers.get('cache-control'), 'no-store');
});

test('allowed status and raw JSON bytes survive; Retry-After is narrow and numeric', async () => {
  const bytes = encoder.encode(' { "number": 1e0, "text": "é 📺", "repeat": 1, "repeat": 2 }\n');
  for (const status of [200, 400, 404, 409, 413, 422, 429, 500, 503, 504]) {
    const f = fixture(() => response(bytes, { status, headers: { 'Retry-After': '5' } }));
    const result = await relay.fetch(request(), f.env);
    assert.equal(result.status, status);
    assert.deepEqual(new Uint8Array(await result.arrayBuffer()), bytes);
    assert.equal(result.headers.get('retry-after'), status === 429 || status === 503 ? '5' : null);
  }
  for (const retry of ['-1', '1.5', '1e2', '999999999999999999999', 'Wed, 21 Oct 2015 07:28:00 GMT', '5, 10']) {
    const f = fixture(() => response('{}', { status: 503, headers: { 'Retry-After': retry } }));
    const result = await relay.fetch(request(), f.env);
    await drain(result);
    assert.equal(result.headers.get('retry-after'), null, retry);
  }
});

test('redirects, HTML challenges and encoded upstream bodies are never exposed as EPG', async () => {
  for (const upstream of [
    new Response('PRIVATE_REDIRECT', { status: 302, headers: { Location: 'https://attacker.example/' } }),
    new Response('PRIVATE_CHALLENGE', { status: 403, headers: { 'Content-Type': 'text/html', 'CF-Mitigated': 'challenge' } }),
    response('PRIVATE_COMPRESSED', { headers: { 'Content-Encoding': 'gzip' } }),
    response('PRIVATE_MEDIA', { headers: { 'Content-Type': 'application/json-evil' } }),
    response('{}', { headers: { 'Content-Length': '-1' } }),
    response('{}', { headers: { 'Content-Length': '2, 2' } }),
    response('{}', { status: 201 }),
  ]) {
    const f = fixture(() => upstream);
    const result = await relay.fetch(request(), f.env);
    assert.equal(result.status, 502);
    assert.doesNotMatch(await result.text(), /PRIVATE_|attacker/);
    assert.equal(f.calls.length, 1);
  }
});

test('route-specific response limits accept exact boundaries and cancel overflowing streams', async () => {
  for (const [path, limit] of [['/epg/v1/match', 4 * 1024 * 1024],
    ['/epg/v1/current', 2 * 1024 * 1024], ['/epg/v1/programmes?' + QUERY, 32 * 1024 * 1024]]) {
    const bytes = new Uint8Array(limit).fill(32); bytes[0] = 91; bytes[limit - 1] = 93;
    const f = fixture(() => response(bytes));
    const result = await relay.fetch(request(path), f.env);
    assert.equal(result.status, 200);
    assert.deepEqual(new Uint8Array(await result.arrayBuffer()), bytes);
    let cancelled = false;
    const over = fixture(() => response(new ReadableStream({
      start(controller) { controller.enqueue(bytes); controller.enqueue(new Uint8Array([32])); },
      cancel() { cancelled = true; },
    })));
    const stream = await relay.fetch(request(path), over.env);
    await assert.rejects(stream.arrayBuffer());
    await settle();
    assert.equal(cancelled, true);
    assert.equal(over.calls[0].request.signal.aborted, true);
    const declared = fixture(() => response('{}', { headers: { 'Content-Length': String(limit + 1) } }));
    assert.equal((await drain(await relay.fetch(request(path), declared.env))).status, 502);
  }
});

test('declared response length cannot hide truncation or excess bytes', async () => {
  for (const length of ['1', '100']) {
    const f = fixture(() => response('{}', { headers: { 'Content-Length': length } }));
    const result = await relay.fetch(request(), f.env);
    await assert.rejects(result.arrayBuffer());
    assert.equal(f.calls[0].request.signal.aborted, true);
  }
});

test('response is genuinely streamed: fetch resolves and first chunk arrives before upstream EOF', async () => {
  let source;
  const f = fixture(() => response(new ReadableStream({ start(controller) { source = controller; } })));
  const result = await relay.fetch(request(), f.env);
  const reader = result.body.getReader();
  source.enqueue(encoder.encode('{"ok":'));
  assert.deepEqual((await reader.read()).value, encoder.encode('{"ok":'));
  source.enqueue(encoder.encode('true}')); source.close();
  assert.deepEqual((await reader.read()).value, encoder.encode('true}'));
  assert.equal((await reader.read()).done, true);
});

test('one 12-second deadline covers limiter, request body and upstream headers', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const stage of ['limiter', 'request', 'backend']) {
    let requestCancelled = false;
    const f = fixture(() => new Promise(() => {}));
    let req = request();
    if (stage === 'limiter') f.env.MATCH_LIMITER.limit = () => new Promise(() => {});
    if (stage === 'request') req = request('/epg/v1/match', {
      body: new ReadableStream({ cancel() { requestCancelled = true; } }), duplex: 'half',
    });
    const pending = relay.fetch(req, f.env);
    await settle();
    t.mock.timers.tick(12001);
    const result = await pending;
    assert.equal(result.status, 504, stage);
    await drain(result);
    if (stage === 'request') assert.equal(requestCancelled, true);
    if (stage === 'backend') assert.equal(f.calls[0].request.signal.aborted, true);
    else assert.equal(f.calls.length, 0);
  }
});

test('deadline is not restarted at response headers and cancels a stalled streamed body', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolveHeaders;
  let cancelled = false;
  const f = fixture(() => new Promise(resolve => { resolveHeaders = resolve; }));
  const pending = relay.fetch(request(), f.env);
  await settle();
  t.mock.timers.tick(11000);
  resolveHeaders(response(new ReadableStream({ cancel() { cancelled = true; } })));
  const result = await pending;
  const body = result.arrayBuffer();
  const rejected = assert.rejects(body);
  t.mock.timers.tick(1001);
  await rejected;
  await settle();
  assert.equal(cancelled, true);
  assert.equal(f.calls[0].request.signal.aborted, true);
});

test('client disconnect and response cancellation abort the upstream stream', async () => {
  for (const mode of ['disconnect', 'cancel']) {
    let cancelled = false;
    const controller = new AbortController();
    const f = fixture(() => response(new ReadableStream({ cancel() { cancelled = true; } })));
    const result = await relay.fetch(request('/epg/v1/match', { signal: controller.signal }), f.env);
    if (mode === 'disconnect') {
      const body = assert.rejects(result.arrayBuffer());
      controller.abort();
      await body;
    } else await result.body.cancel();
    await settle();
    assert.equal(cancelled, true, mode);
    assert.equal(f.calls[0].request.signal.aborted, true, mode);
  }
});

test('disconnect before headers cancels pending work without waiting for the deadline', async () => {
  for (const stage of ['pre-aborted', 'limiter', 'request', 'backend']) {
    const controller = new AbortController();
    let cancelled = false;
    const f = fixture(() => new Promise(() => {}));
    let req = request('/epg/v1/match', { signal: controller.signal });
    if (stage === 'pre-aborted') controller.abort();
    if (stage === 'limiter') f.env.MATCH_LIMITER.limit = () => new Promise(() => {});
    if (stage === 'request') req = request('/epg/v1/match', {
      signal: controller.signal, duplex: 'half',
      body: new ReadableStream({ cancel() { cancelled = true; } }),
    });
    const pending = relay.fetch(req, f.env);
    await settle();
    controller.abort();
    const result = await pending;
    assert.equal(result.status, 502, stage);
    await drain(result);
    if (stage === 'request') assert.equal(cancelled, true);
    if (stage === 'backend') assert.equal(f.calls[0].request.signal.aborted, true);
    else assert.equal(f.calls.length, 0);
  }
});

test('successful EOF releases the deadline and disconnect listener', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const controller = new AbortController();
  const f = fixture();
  await drain(await relay.fetch(request('/epg/v1/match', { signal: controller.signal }), f.env));
  t.mock.timers.tick(20000);
  controller.abort();
  assert.equal(f.calls[0].request.signal.aborted, false);
});
