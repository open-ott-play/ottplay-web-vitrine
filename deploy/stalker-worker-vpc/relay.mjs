// Dedicated Stalker policy; bounded stream transport adapted from the EPG gateway.
const MAX_REQUEST_BYTES = 32 * 1024;
const MAX_RESPONSE_BYTES = 64 * 1024 * 1024;
const DEADLINE_MS = 18000; // Complete before the hosted client's 20-second timeout.
const DNS_LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const PUBLIC_HOST_PATTERN = new RegExp(`^${DNS_LABEL}(?:\\.${DNS_LABEL})+$`);
const RESPONSE_HEADERS = {
  "Content-Type": "application/json",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};
const ERROR_BODY = JSON.stringify({ error: { code: "STALKER_RELAY", message: "Stalker request could not be completed" } });

class RelayError extends Error {
  constructor(status, retry) {
    super("STALKER_RELAY");
    this.status = status;
    this.retry = retry;
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

function nativeByteReader(stream) {
  try {
    const reader = stream.getReader({ mode: "byob" });
    if (typeof reader.readAtLeast === "function") return reader;
    reader.releaseLock();
  } catch (error) {
    if (stream.locked) throw error;
  }
  return undefined;
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
  const match = /^\/stalker\/([a-z0-9]+(?:-[a-z0-9]+)*)$/.exec(url.pathname);
  if (!match || match[1] === "api" || match[1].length > 64 || request.method !== "POST"
      || url.protocol !== "https:" || url.host !== env.PUBLIC_HOST || url.username || url.password
      || request.url.includes("?") || request.url.includes("#")) throw new RelayError(404);
  const credential = request.headers.get("X-OTT-Stalker-Relay");
  // Merged duplicate headers contain a comma and therefore fail closed.
  if (!credential || !/^[A-Za-z0-9_-]{32,256}$/.test(credential)) throw new RelayError(403);
  // BACKEND is a VPC Service binding. The URL never selects an origin or port.
  return { url: `http://ottplay-stalker-relay${url.pathname}`, credential, bytes: MAX_RESPONSE_BYTES };
}

async function admit(request, env, signal) {
  const limiter = env.REQUEST_LIMITER;
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
  const type = request.headers.get("Content-Type");
  const encoding = request.headers.get("Content-Encoding");
  if (!type || type.length > 256 || mediaType(type) !== "application/json"
      || /[\u0000-\u001f\u007f]/.test(type)
      || (encoding && encoding.toLowerCase() !== "identity")) throw new RelayError(400);
  const expected = contentLength(request.headers, MAX_REQUEST_BYTES);
  if (!request.body) throw new RelayError(400);
  // Native Workers byte streams can fill a bounded buffer without re-entering
  // JavaScript for each transport chunk. One extra byte detects overflow.
  const nativeReader = nativeByteReader(request.body);
  if (nativeReader) {
    const stop = () => cancel(nativeReader);
    signal.addEventListener("abort", stop, { once: true });
    try {
      signal.throwIfAborted();
      const capacity = (expected ?? MAX_REQUEST_BYTES) + 1;
      const { value } = await nativeReader.readAtLeast(capacity, new Uint8Array(capacity));
      signal.throwIfAborted();
      const size = value?.byteLength ?? 0;
      if (size > MAX_REQUEST_BYTES) throw new RelayError(413);
      if (!size || (expected !== null && expected !== size)) throw new RelayError(400);
      // A short readAtLeast is possible only at EOF. Return its exact view:
      // the original buffer is detached and the extra capacity is not payload.
      return value;
    } catch (error) {
      stop();
      throw error;
    } finally {
      signal.removeEventListener("abort", stop);
      nativeReader.releaseLock();
    }
  }
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
  const nativeReader = response.body && nativeByteReader(response.body);
  const reader = nativeReader || response.body?.getReader();
  let ended = false, size = 0, output, firstRead = true;
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
    output.error(new Error("STALKER_RELAY"));
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
        let next;
        if (nativeReader) {
          const capacity = Math.min(64 * 1024, selected.bytes - size + 1,
            expected === null ? Infinity : expected - size + 1);
          // Deliver the first available bytes promptly, then coalesce transport
          // fragments natively. Keep a chunked outer stream until verified EOF;
          // a Content-Length response could hide a delayed excess byte.
          next = await nativeReader.readAtLeast(firstRead ? 1 : capacity, new Uint8Array(capacity));
          firstRead = false;
        } else {
          next = reader ? await reader.read() : { done: true };
        }
        const { value, done } = next;
        if (ended) return;
        state.signal.throwIfAborted();
        if (done) {
          if (expected !== null && expected !== size) throw new Error("STALKER_RELAY");
          controller.close();
          finish(false);
        } else {
          size += value.byteLength;
          if (size > selected.bytes || (expected !== null && size > expected)) throw new Error("STALKER_RELAY");
          // Portal responses are never accumulated or reserialized.
          controller.enqueue(value);
        }
      } catch { fail(); }
    },
    cancel() { finish(true); },
  });
  return new Response(stream, { status: response.status, headers: RESPONSE_HEADERS });
}

async function relay(request, env, state) {
  const selected = route(request, env);
  state.signal.throwIfAborted();
  await admit(request, env, state.signal);
  const body = await readRequest(request, state.signal);
  state.signal.throwIfAborted();
  const response = await env.BACKEND.fetch(selected.url, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json", "Accept-Encoding": "identity",
      "X-OTT-Stalker-Relay": selected.credential },
    body, redirect: "manual", signal: state.signal,
  });
  try {
    state.signal.throwIfAborted();
    if (!(response instanceof Response)) throw new RelayError(502);
    // Backend/protocol errors can be plain text. Preserve the status, never its
    // details (which might include a portal response), cookies or auth headers.
    if (response.status >= 400 && response.status <= 599) {
      const retry = response.headers.get("Retry-After");
      const safeRetry = [429, 503].includes(response.status) && retry
        && /^[0-9]{1,5}$/.test(retry) && Number(retry) <= 86400 ? retry : undefined;
      throw new RelayError(response.status, safeRetry);
    }
    if (response.status !== 200 || mediaType(response.headers.get("Content-Type")) !== "application/json") {
      throw new RelayError(502);
    }
    const encoding = response.headers.get("Content-Encoding");
    if (encoding && encoding.toLowerCase() !== "identity") throw new Error("STALKER_RELAY");
    let expected;
    try { expected = contentLength(response.headers, selected.bytes); }
    catch { throw new RelayError(502); }
    return streamResponse(response, selected, state, expected);
  } catch (error) {
    cancel(response?.body);
    throw error instanceof RelayError ? error : new RelayError(502);
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
      const headers = { ...RESPONSE_HEADERS };
      if (error instanceof RelayError && error.retry !== undefined) headers["Retry-After"] = error.retry;
      return new Response(ERROR_BODY, { status, headers });
    }
  },
};
