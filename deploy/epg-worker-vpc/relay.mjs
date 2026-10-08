const MAX_REQUEST_BYTES = 512 * 1024;
const DEADLINE_MS = 12000;
const DNS_LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const PUBLIC_HOST_PATTERN = new RegExp(`^${DNS_LABEL}(?:\\.${DNS_LABEL})+$`);
const ROUTES = {
  "/epg/v1/match": { method: "POST", limiter: "MATCH_LIMITER", bytes: 4 * 1024 * 1024 },
  "/epg/v1/current": { method: "POST", limiter: "CURRENT_LIMITER", bytes: 2 * 1024 * 1024 },
  "/epg/v1/programmes": { method: "GET", limiter: "PROGRAMME_LIMITER", bytes: 32 * 1024 * 1024 },
};
const RESPONSE_HEADERS = {
  "Content-Type": "application/json",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};
const ERROR_BODY = JSON.stringify({
  version: 1,
  source: "epg-one",
  error: { code: "EPG_RELAY", message: "EPG request could not be completed" },
});

class RelayError extends Error {
  constructor(status) {
    super("EPG_RELAY");
    this.status = status;
  }
}

function mediaType(value) {
  return value?.split(";", 1)[0].trim().toLowerCase();
}

function contentLength(headers, limit) {
  const value = headers.get("Content-Length");
  if (value === null) return null;
  if (!/^(0|[1-9][0-9]*)$/.test(value)) throw new RelayError(400);
  if (Number(value) > limit) throw new RelayError(413);
  return Number(value);
}

function cancel(stream) {
  try { void stream?.cancel().catch(() => {}); } catch { /* Already locked or closed. */ }
}

function validClientIP(value) {
  if (!value || value.length > 45) return false;
  const ipv4 = (ip) => {
    const parts = ip.split(".");
    return parts.length === 4 && parts.every((part) => /^(0|[1-9][0-9]{0,2})$/.test(part) && Number(part) <= 255);
  };
  if (!value.includes(":")) return ipv4(value);
  if (!/^[0-9a-f:.]+$/i.test(value)) return false;
  if (value.includes(".")) {
    const at = value.lastIndexOf(":");
    if (!ipv4(value.slice(at + 1))) return false;
    value = `${value.slice(0, at)}:0:0`;
  }
  const sides = value.split("::");
  if (sides.length > 2) return false;
  const groups = sides.flatMap((side) => side ? side.split(":") : []);
  return groups.every((group) => /^[0-9a-f]{1,4}$/i.test(group))
    && (sides.length === 2 ? groups.length < 8 : groups.length === 8);
}

function route(request, env) {
  if (typeof env?.PUBLIC_HOST !== "string" || env.PUBLIC_HOST.length > 253
      || !PUBLIC_HOST_PATTERN.test(env.PUBLIC_HOST)) throw new RelayError(503);
  const url = new URL(request.url);
  const selected = Object.hasOwn(ROUTES, url.pathname) ? ROUTES[url.pathname] : null;
  if (!selected || selected.method !== request.method || url.protocol !== "https:"
      || url.host !== env.PUBLIC_HOST || url.username || url.password
      || request.url.includes("#")) throw new RelayError(404);
  if (selected.method === "POST") {
    if (request.url.includes("?")) throw new RelayError(404);
  } else {
    const keys = ["channelId", "shift", "hours", "generation"];
    const params = url.searchParams;
    if ([...params].length !== keys.length || keys.some((key) => params.getAll(key).length !== 1)) {
      throw new RelayError(400);
    }
    const channel = params.get("channelId"), generation = params.get("generation");
    const shift = params.get("shift"), hours = params.get("hours");
    if (!channel || channel.length > 512 || !generation
        || new TextEncoder().encode(generation).byteLength > 80
        || !/^[+-]?[0-9]+$/.test(shift) || !/^\+?[0-9]+$/.test(hours)
        || !Number.isInteger(Number(shift)) || Math.abs(Number(shift)) > 86400
        || Number(shift) % 3600 !== 0 || !Number.isInteger(Number(hours))
        || Number(hours) > 8784) throw new RelayError(400);
  }
  return { ...selected, url: `http://ottplay-epg${url.pathname}${url.search}` };
}

async function admit(request, env, selected, signal) {
  const limiter = env[selected.limiter];
  // Cloudflare supplies this header. Never substitute caller-controlled XFF.
  const ip = request.headers.get("CF-Connecting-IP");
  if (!validClientIP(ip)
      || typeof limiter?.limit !== "function" || typeof env.BACKEND?.fetch !== "function") {
    // Missing platform identity is unavailable admission, not an anonymous bucket.
    throw new RelayError(503);
  }
  let result;
  try { result = await limiter.limit({ key: ip }); } catch { throw new RelayError(503); }
  signal.throwIfAborted();
  if (typeof result?.success !== "boolean") throw new RelayError(503);
  if (!result.success) throw new RelayError(429);
}

async function readRequest(request, signal) {
  if (request.method === "GET") {
    if (request.body || (request.headers.has("Content-Length")
        && request.headers.get("Content-Length") !== "0")) throw new RelayError(400);
    return undefined;
  }
  const type = request.headers.get("Content-Type");
  const encoding = request.headers.get("Content-Encoding");
  if (!type || type.length > 256 || mediaType(type) !== "application/json"
      || /[\u0000-\u001f\u007f]/.test(type)
      || (encoding && encoding.toLowerCase() !== "identity")) throw new RelayError(400);
  const expected = contentLength(request.headers, MAX_REQUEST_BYTES);
  if (!request.body) throw new RelayError(400);
  const reader = request.body.getReader();
  // Bound retained memory even when the caller sends one byte per chunk.
  const body = new Uint8Array(expected ?? MAX_REQUEST_BYTES);
  let size = 0;
  const stop = () => cancel(reader);
  signal.addEventListener("abort", stop, { once: true });
  try {
    while (true) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      const end = size + value.byteLength;
      if (end > MAX_REQUEST_BYTES) throw new RelayError(413);
      if (end > body.byteLength) throw new RelayError(400);
      body.set(value, size);
      size = end;
    }
    if (!size || (expected !== null && expected !== size)) throw new RelayError(400);
    return size === body.byteLength ? body.buffer : body.buffer.slice(0, size);
  } catch (error) {
    stop();
    throw error;
  } finally {
    signal.removeEventListener("abort", stop);
    reader.releaseLock();
  }
}

function exchange(request) {
  const controller = new AbortController();
  let finished = false, rejectInterrupted;
  const interrupted = new Promise((_, reject) => { rejectInterrupted = reject; });
  const interrupt = (status) => {
    if (finished) return;
    const error = new RelayError(status);
    controller.abort(error);
    rejectInterrupted(error);
  };
  const disconnect = () => interrupt(502);
  const timer = setTimeout(() => interrupt(504), DEADLINE_MS);
  request.signal.addEventListener("abort", disconnect, { once: true });
  if (request.signal.aborted) disconnect();
  return {
    signal: controller.signal,
    interrupted,
    abort: () => controller.abort(),
    finish() {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      request.signal.removeEventListener("abort", disconnect);
    },
  };
}

function streamResponse(response, selected, state, expected) {
  const reader = response.body?.getReader();
  let ended = false, size = 0, output;
  const finish = (failed) => {
    if (ended) return;
    ended = true;
    state.signal.removeEventListener("abort", aborted);
    if (failed) { cancel(reader); state.abort(); }
    try { reader?.releaseLock(); } catch { /* A cancelled read may still be settling. */ }
    state.finish();
  };
  const fail = () => {
    if (ended) return;
    output.error(new Error("EPG_RELAY"));
    finish(true);
  };
  const aborted = () => fail();
  const stream = new ReadableStream({
    start(controller) {
      output = controller;
      state.signal.addEventListener("abort", aborted, { once: true });
      if (state.signal.aborted) fail();
    },
    async pull(controller) {
      try {
        state.signal.throwIfAborted();
        const { value, done } = reader ? await reader.read() : { done: true };
        if (ended) return;
        state.signal.throwIfAborted();
        if (done) {
          if (expected !== null && expected !== size) throw new Error("EPG_RELAY");
          controller.close();
          finish(false);
        } else {
          size += value.byteLength;
          if (size > selected.bytes || (expected !== null && size > expected)) throw new Error("EPG_RELAY");
          // Forward the original chunk; programme responses are never accumulated.
          controller.enqueue(value);
        }
      } catch { fail(); }
    },
    cancel() { finish(true); },
  });
  const headers = { ...RESPONSE_HEADERS };
  const retry = response.headers.get("Retry-After");
  if ([429, 503].includes(response.status) && retry && /^[0-9]{1,5}$/.test(retry)
      && Number(retry) <= 86400) headers["Retry-After"] = retry;
  return new Response(stream, { status: response.status, headers });
}

async function relay(request, env, state) {
  const selected = route(request, env);
  state.signal.throwIfAborted();
  await admit(request, env, selected, state.signal);
  const body = await readRequest(request, state.signal);
  state.signal.throwIfAborted();
  const response = await env.BACKEND.fetch(selected.url, {
    method: selected.method,
    headers: { Accept: "application/json", "Content-Type": "application/json", "Accept-Encoding": "identity" },
    body, redirect: "manual", signal: state.signal,
  });
  try {
    state.signal.throwIfAborted();
    if (!(response instanceof Response)
        || ![200, 400, 404, 409, 413, 422, 429, 500, 503, 504].includes(response.status)
        || mediaType(response.headers.get("Content-Type")) !== "application/json") throw new Error("EPG_RELAY");
    const encoding = response.headers.get("Content-Encoding");
    if (encoding && encoding.toLowerCase() !== "identity") throw new Error("EPG_RELAY");
    return streamResponse(response, selected, state, contentLength(response.headers, selected.bytes));
  } catch {
    cancel(response?.body);
    throw new RelayError(502);
  }
}

export default {
  async fetch(request, env) {
    const state = exchange(request);
    try {
      // The stream owns cleanup after headers; keep the deadline until EOF/cancel.
      return await Promise.race([relay(request, env, state), state.interrupted]);
    } catch (error) {
      state.abort();
      state.finish();
      cancel(request.body);
      const status = error instanceof RelayError ? error.status : 502;
      return new Response(ERROR_BODY, { status, headers: RESPONSE_HEADERS });
    }
  },
};
