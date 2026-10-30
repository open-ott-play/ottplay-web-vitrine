import assert from 'node:assert/strict';
import { after, afterEach, before, test } from 'node:test';
import relay from './relay.mjs';

// Exercise the public boundary, without importing implementation constants.
const ORIGIN = 'https://stalker.test.invalid';
const CREDENTIAL = 'f'.repeat(43);
const IP = '198.51.100.7';
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

function request(path = '/stalker/source-1', options = {}) {
  const method = options.method ?? 'POST';
  const headers = { 'CF-Connecting-IP': IP, 'X-OTT-Stalker-Relay': CREDENTIAL, ...options.headers };
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
  const env = { PUBLIC_HOST: 'stalker.test.invalid', BACKEND: { async fetch(input, init) {
    const req = new Request(input, init);
    calls.push({ input, init, request: req });
    return backend(req, calls.length);
  } } };
  for (const name of ['REQUEST_LIMITER']) {
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

test('exact POST routes preserve opaque bytes through one fixed binding', async () => {
  for (const name of ['a', 'source-2', 'x'.repeat(64)]) {
    const body = encoder.encode(' { "text": "fixture 📺", "number": 1.00 }\n');
    const f = fixture();
    const result = await drain(await relay.fetch(request('/stalker/' + name, { body }), f.env));
    assert.equal(result.status, 200);
    assert.equal(f.calls.length, 1);
    assert.deepEqual(f.limits, [{ name: 'REQUEST_LIMITER', value: { key: IP } }]);
    assert.equal(f.calls[0].input, 'http://ottplay-stalker-relay/stalker/' + name);
    assert.equal(f.calls[0].init.redirect, 'manual');
    assert.deepEqual(new Uint8Array(await f.calls[0].request.arrayBuffer()), body);
  }
});

test('only exact public host, POST and non-api opaque route IDs are admitted', async () => {
  const bad = ['/stalker/api', '/stalker/', '/stalker/A', '/stalker/a/', '/stalker/a--b',
    '/stalker/-a', '/stalker/a-', '/stalker/' + 'x'.repeat(65), '/stalker/%61',
    '/stalker/a/b', '/stalker/a?', '/stalker/a?url=https://private.invalid',
    '/stalker/a#fragment', '/health', '/epg/v1/match', '/', '/proxy'];
  for (const path of bad) {
    const f = fixture();
    assert.equal((await drain(await relay.fetch(request(path), f.env))).status, 404, path);
    assert.equal(f.calls.length, 0);
  }
  for (const origin of ['http://stalker.test.invalid', 'https://other.test.invalid', 'https://stalker.test.invalid:444']) {
    const req = new Request(origin + '/stalker/a', request());
    const f = fixture();
    assert.equal((await drain(await relay.fetch(req, f.env))).status, 404);
    assert.equal(f.calls.length, 0);
  }
  for (const method of ['GET', 'PUT', 'DELETE', 'OPTIONS']) {
    const f = fixture();
    assert.equal((await drain(await relay.fetch(request('/stalker/a', { method }), f.env))).status, 404);
    assert.equal(f.calls.length, 0);
  }
});

test('missing, duplicate and malformed credentials never reach the private service', async () => {
  for (const credential of [null, '', 'x'.repeat(31), 'x'.repeat(257), 'secret, secret', 'Bearer fixture', 'a'.repeat(32) + '=']) {
    const req = request();
    if (credential === null) req.headers.delete('X-OTT-Stalker-Relay');
    else req.headers.set('X-OTT-Stalker-Relay', credential);
    const f = fixture();
    assert.equal((await drain(await relay.fetch(req, f.env))).status, 403);
    assert.equal(f.calls.length, 0);
  }
});

test('POST body is opaque JSON media with identity encoding and a 32 KiB wire limit', async () => {
  for (const length of [32 * 1024, 32 * 1024 + 1]) {
    const body = new Uint8Array(length).fill(32);
    body[0] = 91; body[length - 1] = 93;
    const f = fixture();
    const result = await drain(await relay.fetch(request('/stalker/source-1', { body }), f.env));
    assert.equal(result.status, length === 32 * 1024 ? 200 : 413);
    assert.equal(f.calls.length, length === 32 * 1024 ? 1 : 0);
  }
  for (const headers of [{ 'Content-Type': 'text/plain' }, { 'Content-Encoding': 'gzip' },
    { 'Content-Length': String(32 * 1024 + 1) }]) {
    const f = fixture();
    assert.ok((await drain(await relay.fetch(request('/stalker/source-1', { headers }), f.env))).status >= 400);
    assert.equal(f.calls.length, 0);
  }
  // The service, not a second JavaScript parser, owns JSON/schema validation.
  const f = fixture();
  await drain(await relay.fetch(request('/stalker/source-1', { body: 'not-json' }), f.env));
  assert.equal(await f.calls[0].request.text(), 'not-json');
});

test('32 KiB fragmented into distinct one-byte chunks reaches the service byte-for-byte', async () => {
  const size = 32 * 1024;
  const expected = Uint8Array.from({ length: size }, (_, index) =>
    index === 0 || index === size - 1 ? 34 : 65 + index % 26);
  const input = tinyInput({ end: size, byteAt: index => expected[index] });
  const f = fixture();
  const result = await relay.fetch(request('/stalker/source-1', {
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
  const result = await relay.fetch(request('/stalker/source-1', {
    body: input.stream, duplex: 'half',
  }), f.env);
  assert.equal((await drain(result)).status, 413);
  assert.equal(input.produced, 32 * 1024 + 1);
  assert.equal(input.cancelled, true);
  assert.equal(f.calls.length, 0);
});

test('fragmented partial input still obeys the shared deadline and client disconnect', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const mode of ['deadline', 'disconnect']) {
    const input = tinyInput({ stallAt: 4096 });
    const abort = new AbortController();
    const f = fixture();
    const pending = relay.fetch(request('/stalker/source-1', {
      body: input.stream, duplex: 'half', signal: abort.signal,
    }), f.env);
    await settle();
    assert.equal(input.produced, 4096);
    if (mode === 'deadline') t.mock.timers.tick(18001);
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
    f => { f.env.PUBLIC_HOST = 'https://stalker.test.invalid/private'; },
    f => { f.env.BACKEND = {}; },
    f => { delete f.env.REQUEST_LIMITER; },
    f => { f.env.REQUEST_LIMITER.limit = async () => { throw new Error('PRIVATE_LIMITER_FAILURE'); }; },
    f => { f.env.REQUEST_LIMITER.limit = async () => ({}); },
    f => { f.env.REQUEST_LIMITER.limit = async () => ({ success: 'true' }); },
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
  f.env.REQUEST_LIMITER.limit = async () => ({ success: false });
  assert.equal((await drain(await relay.fetch(request(), f.env))).status, 429);
  assert.equal(f.calls.length, 0);
});

test('trusted client identity accepts IPv6 but rejects ambiguous or malformed header values', async () => {
  const valid = ['2001:db8::7', '::1', '::ffff:192.0.2.1'];
  for (const value of [...valid, 'not-an-ip', '198.51.100.7, 203.0.113.5', '999.1.1.1',
    '::::', ':', '1:2:3:4:5:6:7:8:9', 'deadbeef', '1.1.1', 'fe80::1%lo0']) {
    const f = fixture();
    const result = await drain(await relay.fetch(request('/stalker/source-1', {
      headers: { 'CF-Connecting-IP': value },
    }), f.env));
    assert.equal(result.status, valid.includes(value) ? 200 : 503, value);
    if (valid.includes(value)) assert.equal(f.limits[0].value.key, value);
    else assert.equal(f.calls.length, 0);
  }
});

test('only the dedicated credential and fixed JSON headers cross the boundary', async () => {
  const f = fixture(() => response('{"ok":true}', { headers: {
    'Set-Cookie': 'PRIVATE_COOKIE=secret', 'Location': 'https://private.example/',
    'Server': 'PRIVATE_SERVER', 'X-Internal': 'PRIVATE_INTERNAL', 'CF-Ray': 'private-ray',
    'Access-Control-Allow-Origin': '*', 'Retry-After': '99', 'Cache-Control': 'public, max-age=86400',
  } }));
  const result = await relay.fetch(request('/stalker/source-1', { headers: {
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
  assert.deepEqual([...f.calls[0].request.headers.keys()].sort(), ['accept', 'accept-encoding', 'content-type', 'x-ott-stalker-relay']);
  assert.equal(f.calls[0].request.headers.get('x-ott-stalker-relay'), CREDENTIAL);
});

test('success bytes survive, all backend errors become fixed safe JSON without private headers', async () => {
  const bytes = encoder.encode(' { "number": 1e0, "text": "é 📺" }\n');
  const f = fixture(() => response(bytes));
  assert.deepEqual(new Uint8Array(await (await relay.fetch(request(), f.env)).arrayBuffer()), bytes);
  for (const status of [400, 401, 403, 404, 413, 422, 429, 500, 502, 503, 504]) {
    const f = fixture(() => new Response('PRIVATE_PROVIDER_MESSAGE', { status, headers: { 'Set-Cookie': 'private', 'Retry-After': 'private', 'WWW-Authenticate': 'private' } }));
    const result = await relay.fetch(request(), f.env);
    assert.equal(result.status, status);
    assert.equal((await result.json()).error.code, 'STALKER_RELAY');
    assert.equal(result.headers.get('set-cookie'), null);
    assert.equal(result.headers.get('www-authenticate'), null);
    assert.equal(result.headers.get('retry-after'), null);
    assert.equal(result.headers.get('cache-control'), 'no-store');
  }
});

test('redirects, HTML challenges and encoded upstream bodies are never exposed as portal JSON', async () => {
  for (const upstream of [
    new Response('PRIVATE_REDIRECT', { status: 302, headers: { Location: 'https://attacker.example/' } }),
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

test('64 MiB is streamed without accumulation and one excess byte cancels upstream', async () => {
  for (const excess of [false, true]) {
    let produced = 0, cancelled = false;
    const limit = 64 * 1024 * 1024 + Number(excess);
    const f = fixture(() => response(new ReadableStream({
      pull(controller) {
        if (produced === limit) { controller.close(); return; }
        const size = Math.min(65536, limit - produced);
        controller.enqueue(new Uint8Array(size).fill(32)); produced += size;
      },
      cancel() { cancelled = true; },
    }, { highWaterMark: 0 })));
    const result = await relay.fetch(request(), f.env);
    const reader = result.body.getReader();
    let received = 0;
    const consume = async () => { for (;;) { const {value, done} = await reader.read(); if(done) break; received += value.byteLength; } };
    if (excess) { await assert.rejects(consume()); assert.equal(cancelled, true); assert.equal(f.calls[0].request.signal.aborted, true); }
    else { await consume(); assert.equal(received, limit); }
  }
  const f = fixture(() => response('{}', { headers: { 'Content-Length': String(64 * 1024 * 1024 + 1) } }));
  assert.equal((await drain(await relay.fetch(request(), f.env))).status, 502);
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

test('one 18-second deadline covers limiter, request body and upstream headers', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  for (const stage of ['limiter', 'request', 'backend']) {
    let requestCancelled = false;
    const f = fixture(() => new Promise(() => {}));
    let req = request();
    if (stage === 'limiter') f.env.REQUEST_LIMITER.limit = () => new Promise(() => {});
    if (stage === 'request') req = request('/stalker/source-1', {
      body: new ReadableStream({ cancel() { requestCancelled = true; } }), duplex: 'half',
    });
    const pending = relay.fetch(req, f.env);
    await settle();
    t.mock.timers.tick(18001);
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
  t.mock.timers.tick(17000);
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
    const result = await relay.fetch(request('/stalker/source-1', { signal: controller.signal }), f.env);
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
    let req = request('/stalker/source-1', { signal: controller.signal });
    if (stage === 'pre-aborted') controller.abort();
    if (stage === 'limiter') f.env.REQUEST_LIMITER.limit = () => new Promise(() => {});
    if (stage === 'request') req = request('/stalker/source-1', {
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
  await drain(await relay.fetch(request('/stalker/source-1', { signal: controller.signal }), f.env));
  t.mock.timers.tick(20000);
  controller.abort();
  assert.equal(f.calls[0].request.signal.aborted, false);
});

// Even an uncooperative binding can resolve after cancellation; discard its body.
test('late backend completion after deadline is cancelled and never becomes a second result', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolve, cancelled = false;
  const f = fixture(() => new Promise(done => { resolve = done; }));
  const pending = relay.fetch(request(), f.env);
  await settle(); t.mock.timers.tick(18001);
  assert.equal((await drain(await pending)).status, 504);
  resolve(response(new ReadableStream({ cancel() { cancelled = true; } })));
  await settle();
  assert.equal(cancelled, true);
});

test('only bounded integer Retry-After survives safe429/503 errors', async () => {
  for (const status of [400, 429, 503]) {
    for (const value of ['0', '5', '86400', '-1', '1.5', '1e2', '86401', '100000', '5, 10', 'private', 'Wed, 21 Oct 2015 07:28:00 GMT']) {
      const f = fixture(() => response('PRIVATE_DETAIL', { status, headers: { 'Retry-After': value } }));
      const result = await relay.fetch(request(), f.env);
      assert.equal(result.status, status);
      assert.equal(result.headers.get('retry-after'), status !== 400 && ['0', '5', '86400'].includes(value) ? value : null);
      assert.doesNotMatch(await result.text(), /PRIVATE_DETAIL/);
    }
  }
});
