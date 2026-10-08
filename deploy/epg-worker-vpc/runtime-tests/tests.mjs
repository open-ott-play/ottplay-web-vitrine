import relay from './relay.mjs';

const CAP = 512 * 1024;
const ORIGIN = 'https://epg.test.invalid';
const encoder = new TextEncoder();
const bytes = length => Uint8Array.from({ length }, (_, index) => 32 + index % 95);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function assert(value, message = 'assertion failed') { if (!value) throw new Error(message); }
function equal(actual, expected, label = '') { assert(actual === expected, `${label}: ${actual} !== ${expected}`); }
function byteEqual(actual, expected) {
  equal(actual.byteLength, expected.byteLength, 'byte length');
  for (let i = 0; i < actual.byteLength; i++) if (actual[i] !== expected[i]) throw new Error(`byte mismatch at ${i}`);
}
async function rejected(promise, label) {
  let failed = false;
  try { await promise; } catch { failed = true; }
  assert(failed, label + ' must reject');
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

// Both types are native workerd streams, not JS-source streams or Node shims.
function nativeSource(payload, { fixed = false, chunks = [1, 7, 4093, 65537], gate, close = true, extra } = {}) {
  const transform = fixed ? new FixedLengthStream(payload.byteLength) : new IdentityTransformStream();
  const writer = transform.writable.getWriter();
  const ready = deferred();
  const state = { stream: transform.readable, produced: 0, cancelled: false, completed: false,
    extraWriteAccepted: false, extraWriteRejected: false, ready: ready.promise };
  writer.closed.catch(() => { state.cancelled = true; });
  state.pump = (async () => {
    let part = 0;
    while (state.produced < payload.byteLength) {
      const end = Math.min(payload.byteLength, state.produced + chunks[part++ % chunks.length]);
      await writer.write(payload.slice(state.produced, end));
      state.produced = end;
    }
    ready.resolve();
    if (gate) await gate;
    if (extra) {
      try { await writer.write(extra); state.extraWriteAccepted = true; }
      catch (error) { state.extraWriteRejected = true; throw error; }
    }
    if (close) { await writer.close(); state.completed = true; }
  })().catch(() => { state.cancelled = true; ready.resolve(); });
  return state;
}

// Assert that optimized paths actually use native BYOB readAtLeast. A fallback
// implementation must not silently make this suite green.
function traceNative(stream) {
  const count = { byob: 0, ordinary: 0, atLeast: 0, cancelled: 0, minimums: [] };
  const getReader = stream.getReader;
  Object.defineProperty(stream, 'getReader', { configurable: true, value(options) {
    const reader = getReader.call(this, options);
    const cancel = reader.cancel;
    Object.defineProperty(reader, 'cancel', { configurable: true, value(reason) {
      count.cancelled++;
      return cancel.call(this, reason);
    } });
    if (options?.mode === 'byob') {
      count.byob++;
      assert(typeof reader.readAtLeast === 'function', 'native readAtLeast is unavailable');
      const read = reader.readAtLeast;
      Object.defineProperty(reader, 'readAtLeast', { configurable: true, value(minimum, view) {
        count.atLeast++; count.minimums.push(minimum);
        return read.call(this, minimum, view);
      } });
    } else count.ordinary++;
    return reader;
  } });
  return count;
}
function nativeUsed(count, label) {
  assert(count.byob > 0 && count.atLeast > 0 && count.ordinary === 0, label + ' did not use native BYOB readAtLeast');
}
function request(body = '{}', { length, signal, path = '/epg/v1/match' } = {}) {
  const headers = { 'CF-Connecting-IP': '198.51.100.7', 'Content-Type': 'application/json' };
  if (length !== undefined) headers['Content-Length'] = String(length);
  return new Request(ORIGIN + path, { method: 'POST', body, signal, headers });
}
function response(body = '{}', length) {
  const headers = { 'Content-Type': 'application/json' };
  if (length !== undefined) headers['Content-Length'] = String(length);
  return new Response(body, { headers });
}
function fixture(backend = () => response()) {
  const calls = [];
  const env = { PUBLIC_HOST: 'epg.test.invalid', BACKEND: { async fetch(input, init) {
    equal(new URL(input).origin, 'http://ottplay-epg', 'bound service only');
    const call = { input, init }; calls.push(call);
    return backend(call);
  } } };
  for (const name of ['MATCH_LIMITER', 'CURRENT_LIMITER', 'PROGRAMME_LIMITER']) env[name] = { async limit() { return { success: true }; } };
  return { env, calls };
}
async function expectError(result, status) {
  equal(result.status, status, 'relay status');
  equal((await result.json()).error.code, 'EPG_RELAY');
}
async function settle() { for (let i = 0; i < 8; i++) await Promise.resolve(); await sleep(1); }

const cases = [];
function test(name, run) { cases.push({ name, run }); }

for (const known of [false, true]) test(`native fragmented exact 512KiB request (${known ? 'known' : 'unknown'} length)`, async () => {
  const expected = bytes(CAP);
  const source = nativeSource(expected);
  const req = request(source.stream, { length: known ? CAP : undefined });
  const trace = traceNative(req.body);
  const f = fixture(async call => { byteEqual(new Uint8Array(await new Response(call.init.body).arrayBuffer()), expected); return response(); });
  const result = await relay.fetch(req, f.env);
  equal(result.status, 200); await result.arrayBuffer(); await source.pump;
  equal(f.calls.length, 1); equal(source.produced, CAP); nativeUsed(trace, 'request');
  equal(trace.atLeast, 1, 'one aggregated native request read');
  equal(trace.minimums[0], CAP + 1, 'one overflow sentinel byte');
});

test('native FixedLengthStream request preserves exact bytes', async () => {
  const expected = bytes(CAP), source = nativeSource(expected, { fixed: true, chunks: [3, 65521] });
  const req = request(source.stream, { length: CAP }), trace = traceNative(req.body);
  const f = fixture(async call => { byteEqual(new Uint8Array(await new Response(call.init.body).arrayBuffer()), expected); return response(); });
  const result = await relay.fetch(req, f.env); equal(result.status, 200); await result.arrayBuffer(); await source.pump;
  equal(f.calls.length, 1); nativeUsed(trace, 'fixed request');
});

for (const [label, actual, length, status] of [
  ['unknown cap+1', CAP + 1, undefined, 413],
  ['known cap+1', CAP + 1, CAP, 413],
  ['declared over cap', 1, CAP + 1, 413],
  ['declared short', 13, 20, 400],
  ['declared smaller', 20, 13, 400],
  ['empty unknown', 0, undefined, 400],
  ['empty known', 0, 0, 400],
]) test(`request rejects ${label} before backend`, async () => {
  const source = nativeSource(bytes(actual));
  const f = fixture(); const result = await relay.fetch(request(source.stream, { length }), f.env);
  await expectError(result, status); equal(f.calls.length, 0);
  await source.pump;
  assert(source.cancelled || source.completed, 'input left pending after rejection');
});

for (const fixed of [false, true]) test(`native known-length response is byte-equal (${fixed ? 'FixedLengthStream' : 'IdentityTransformStream'})`, async () => {
  const expected = bytes(4 * 1024 * 1024), source = nativeSource(expected, { fixed });
  const upstream = response(source.stream, expected.length), trace = traceNative(upstream.body);
  const f = fixture(() => upstream), result = await relay.fetch(request(), f.env);
  equal(result.status, 200);
  equal(result.headers.get('content-length'), null, 'outer response must remain EOF-framed');
  byteEqual(new Uint8Array(await result.arrayBuffer()), expected);
  await source.pump; nativeUsed(trace, 'response');
  assert(source.completed); assert(!f.calls[0].init.signal.aborted);
});

for (const [label, actual, length] of [['short', 17, 18], ['overflow', 19, 18]]) test(`known response ${label} errors its body`, async () => {
  const source = nativeSource(bytes(actual)), upstream = response(source.stream, length), trace = traceNative(upstream.body);
  const f = fixture(() => upstream), result = await relay.fetch(request(), f.env);
  equal(result.status, 200);
  await rejected(result.arrayBuffer(), label);
  await source.pump; nativeUsed(trace, 'invalid response');
  assert(f.calls[0].init.signal.aborted, 'invalid response must abort backend');
});

test('unknown response crossing route cap errors and cancels native upstream', async () => {
  const source = nativeSource(bytes(2 * 1024 * 1024 + 1));
  const upstream = response(source.stream), trace = traceNative(upstream.body), f = fixture(() => upstream);
  const result = await relay.fetch(request('{}', { path: '/epg/v1/current' }), f.env);
  await rejected(result.arrayBuffer(), 'unknown response overflow'); await source.pump;
  nativeUsed(trace, 'unknown response'); assert(f.calls[0].init.signal.aborted);
});

for (const mode of ['eof', 'extra']) test(`exact declared bytes wait for delayed ${mode}`, async () => {
  const gate = deferred(), payload = encoder.encode('{"ok":true}');
  const source = nativeSource(payload, { gate: gate.promise, extra: mode === 'extra' ? new Uint8Array([32]) : undefined });
  const f = fixture(() => response(source.stream, payload.length));
  const result = await relay.fetch(request(), f.env);
  let finished = false;
  const reading = result.arrayBuffer(); reading.then(() => { finished = true; }, () => { finished = true; });
  await source.ready; await sleep(25);
  assert(!finished, 'consumer completed from Content-Length before actual upstream EOF');
  gate.resolve();
  if (mode === 'extra') { await rejected(reading, 'delayed extra'); assert(f.calls[0].init.signal.aborted); }
  else byteEqual(new Uint8Array(await reading), payload);
  await source.pump;
});

for (const kind of ['empty', 'extra', 'null']) test(`known CL0 response (${kind})`, async () => {
  const source = kind === 'null' ? null : nativeSource(new Uint8Array(kind === 'extra' ? [32] : []));
  const f = fixture(() => response(source?.stream ?? null, 0)), result = await relay.fetch(request(), f.env);
  if (kind === 'extra') await rejected(result.arrayBuffer(), 'CL0 extra');
  else equal((await result.arrayBuffer()).byteLength, 0);
  if (source) await source.pump;
});

async function interrupted(stage, mode) {
  const abort = new AbortController(), gate = deferred(), reached = deferred();
  let source, trace;
  const f = fixture(async call => {
    reached.resolve();
    if (stage === 'backend') return new Promise((resolve, reject) => {
      call.init.signal.addEventListener('abort', () => reject(new Error('cancelled backend')), { once: true });
    });
    source = nativeSource(encoder.encode('{}'), { gate: gate.promise, extra: new Uint8Array([32]) });
    const upstream = response(source.stream, 2);
    trace = traceNative(upstream.body);
    return upstream;
  });
  let req;
  if (stage === 'input') {
    source = nativeSource(bytes(64), { gate: gate.promise, extra: new Uint8Array([32]) });
    req = request(source.stream, { signal: abort.signal });
    trace = traceNative(req.body);
  } else req = request('{}', { signal: abort.signal });
  const pending = relay.fetch(req, f.env);
  let result, reading;
  if (stage === 'input') await source.ready;
  else if (stage === 'backend') await reached.promise;
  else { result = await pending; reading = result.arrayBuffer(); reading.catch(() => {}); await source.ready; }
  if (mode === 'abort') abort.abort();
  if (stage === 'output') await rejected(reading, mode + ' after headers');
  else await expectError(await pending, mode === 'deadline' ? 504 : 502);
  await settle();
  if (stage === 'input') equal(f.calls.length, 0);
  else assert(f.calls[0].init.signal.aborted, stage + ' signal not cancelled');
  if (source) {
    // Native IdentityTransformStream reports cancellation to a paused producer
    // on its next write, not necessarily through writer.closed before it resumes.
    assert(trace.cancelled > 0, stage + ' reader was not cancelled');
    gate.resolve(); await source.pump;
    assert(source.extraWriteRejected === true && source.extraWriteAccepted !== true,
      stage + ' cancelled stream did not reject its next write');
  }
}
for (const stage of ['input', 'backend', 'output']) test(`client abort cancels ${stage}`, () => interrupted(stage, 'abort'));

test('consumer cancel aborts backend and unread native response', async () => {
  const gate = deferred(), source = nativeSource(bytes(65537), { gate: gate.promise });
  const f = fixture(() => response(source.stream, 65537)), result = await relay.fetch(request(), f.env);
  await result.body.cancel(); await settle();
  assert(f.calls[0].init.signal.aborted); assert(source.cancelled);
  gate.resolve(); await source.pump;
});

test('shared real 12s deadline cancels input, backend and exact-length stalled output', async () => {
  await Promise.all(['input', 'backend', 'output'].map(stage => interrupted(stage, 'deadline')));
});

// A real workerd service boundary exercises response framing without opening any
// TCP socket, external host, EPG source or production service binding.
export const wire = {
  async fetch(incoming, env, ctx) {
    const mode = new URL(incoming.url).pathname.slice(1);
    const payload = encoder.encode('{"ok":true}');
    const source = nativeSource(payload, { gate: sleep(120), extra: mode === 'extra' ? new Uint8Array([32]) : undefined });
    const f = fixture(() => response(source.stream, payload.length));
    ctx.waitUntil(source.pump);
    return relay.fetch(request(), f.env);
  },
};

export default {
  async test(ctrl, env, ctx) {
    assert(typeof IdentityTransformStream === 'function' && typeof FixedLengthStream === 'function', 'native workerd stream APIs required');
    let publicCalls = 0;
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => { publicCalls++; throw new Error('Unexpected public fetch'); };
    const failures = [];
    try {
      for (const item of cases) {
        try { await item.run(); console.log('PASS native: ' + item.name); }
        catch (error) { failures.push(item.name + ': ' + error.message); console.error('FAIL native: ' + item.name + ': ' + error.stack); }
      }
      for (const mode of ['eof', 'extra']) {
        try {
          const result = await env.WIRE.fetch('https://fixture.invalid/' + mode);
          let finished = false;
          const body = result.arrayBuffer(); body.then(() => { finished = true; }, () => { finished = true; });
          await sleep(25); assert(!finished, 'service boundary accepted declared length before EOF');
          if (mode === 'extra') await rejected(body, 'service delayed overflow');
          else byteEqual(new Uint8Array(await body), encoder.encode('{"ok":true}'));
          console.log('PASS native: service boundary delayed ' + mode);
        } catch (error) { failures.push('service ' + mode + ': ' + error.message); }
      }
      equal(publicCalls, 0, 'public fetch calls');
      assert(failures.length === 0, failures.join('\n'));
      console.log(`PASS native workerd relay: ${cases.length + 2} cases, no external fetch`);
    } finally { globalThis.fetch = originalFetch; }
  },
};
