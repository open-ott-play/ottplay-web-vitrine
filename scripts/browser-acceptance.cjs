'use strict';
// Exercise the exact staged website with synthetic local inputs only.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const playwright = require(process.env.CI_PLAYWRIGHT_MODULE);
const root = fs.realpathSync(process.env.CI_VITRINE_SITE || 'dist');
const diagnostics = process.env.CI_BROWSER_DIAGNOSTICS;
const runtime = JSON.parse(fs.readFileSync(path.join(diagnostics, 'runtime.json'), 'utf8'));
const scenarios = [], errors = [], requests = [], blockedRequests = [];
const mime = {'.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.json':'application/json',
  '.svg':'image/svg+xml', '.png':'image/png', '.jpg':'image/jpeg', '.mp4':'video/mp4', '.ts':'video/mp2t',
  '.m3u8':'application/vnd.apple.mpegurl', '.woff2':'font/woff2', '.woff':'font/woff'};
// Resolve files once from the verified tree; request URLs never become filesystem paths.
const assets = new Map();
function preload(directory, prefix = '') {
  for (const entry of fs.readdirSync(directory, {withFileTypes:true})) {
    const file = path.join(directory, entry.name), key = prefix + '/' + entry.name;
    if (entry.isDirectory()) preload(file, key);
    else if (entry.isFile()) assets.set(key, fs.readFileSync(file));
    else throw new Error('Non-regular staged asset');
  }
}
preload(root);
const server = http.createServer((request, response) => {
  let route;
  try {route = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);}
  catch {return response.writeHead(400).end();}
  requests.push({method:request.method, path:route});
  // No local application API or provider is running in this test server.
  if (!['GET','HEAD'].includes(request.method)) return response.writeHead(405).end();
  const key = route.endsWith('/') ? route + 'index.html' : route;
  const bytes = assets.get(key);
  if (!bytes) return response.writeHead(404).end();
  const size = bytes.length;
  let start = 0, end = size - 1, status = 200;
  const headers = {'Content-Type':mime[path.extname(key)] || 'application/octet-stream',
    'Cache-Control':'no-store', 'Accept-Ranges':'bytes'};
  if (request.headers.range) {
    const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range);
    if (!range) return response.writeHead(416, {'Content-Range':`bytes */${size}`}).end();
    start = Number(range[1]); end = range[2] ? Number(range[2]) : size - 1;
    if (start > end || end >= size) return response.writeHead(416, {'Content-Range':`bytes */${size}`}).end();
    status = 206; headers['Content-Range'] = `bytes ${start}-${end}/${size}`;
  }
  headers['Content-Length'] = end - start + 1;
  response.writeHead(status, headers);
  if (request.method === 'HEAD') return response.end();
  response.end(bytes.subarray(start, end + 1));
});
async function scenario(name, test) {
  await test(); scenarios.push(name); console.log('PASS ' + name);
}
async function main() {
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser, context, page;
  try {
    const engine = process.env.CI_BROWSER_ENGINE, channel = process.env.CI_BROWSER_CHANNEL;
    browser = await playwright[engine].launch({headless:true, ...(channel ? {channel} : {})});
    assert.equal(browser.browserType().name(), runtime.engine);
    assert.equal(browser.version(), runtime.browserVersion);
    assert.equal(require(path.join(process.env.CI_PLAYWRIGHT_MODULE,'package.json')).version, runtime.playwrightVersion);
    context = await browser.newContext({locale:'en-US',viewport:{width:1280,height:900},serviceWorkers:'block'});
    await context.tracing.start({screenshots:true,snapshots:true});
    await context.route('**/*', async route => {
      if (new URL(route.request().url()).origin !== origin) {
        const url = new URL(route.request().url());
        blockedRequests.push({method:route.request().method(),url:url.origin + url.pathname});
        return route.abort();
      }
      return route.continue();
    });
    for (const route of ['/package.json','/.git/config','/%2e%2e%2fpackage.json']) {
      assert.equal((await context.request.get(origin + route)).status(),404);
    }
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await scenario('staged-player-boots-with-hosted-configuration', async () => {
      await page.goto(origin, {waitUntil:'load'});
      await page.waitForFunction(() => window.__OTTPLAY_HOSTED__ && !document.body.classList.contains('booting'));
      assert.match(await page.title(), /OTT.play/i);
      const text = await page.locator('body').innerText();
      assert(text.trim().length > 20, 'Player must render a visible interface');
      assert.deepEqual(errors, []);
    });
    await scenario('versioned-hosted-resources-load-from-the-staged-site', async () => {
      const config = await page.evaluate(() => window.__OTTPLAY_HOSTED__);
      assert.equal(config.epg.mode, 'server');
      assert.equal(config.epg.apiBase, '/epg/v1');
      assert.equal(config.swop.entryUrl, '/swop-input/');
      for (const field of ['serverWorkerUrl','workerUrl','diagnosticsUrl']) {
        assert.match(config.epg[field], /^\/hosted-runtime\/[0-9a-f]{64}\//);
        const response = await context.request.get(origin + config.epg[field]);
        assert.equal(response.status(), 200);
        assert.match(response.headers()['content-type'], /javascript/);
      }
    });
    await scenario('staged-epg-worker-executes-and-closes-cleanly', async () => {
      const response = await page.evaluate(() => new Promise((resolve,reject) => {
        const worker = new Worker(window.__OTTPLAY_HOSTED__.epg.serverWorkerUrl);
        const timer = setTimeout(() => {worker.terminate();reject(new Error('Worker close timeout'));}, 10000);
        worker.onerror = event => {clearTimeout(timer);worker.terminate();reject(new Error(event.message));};
        worker.onmessage = event => {if(event.data.type === 'closed'){clearTimeout(timer);worker.terminate();resolve(event.data.type);}};
        worker.postMessage({type:'close'});
      }));
      assert.equal(response, 'closed');
    });
    await scenario('staged-demo-video-decodes-and-advances', async () => {
      const result = await page.evaluate(async () => {
        const video = document.createElement('video');
        video.muted = true; video.playsInline = true; video.src = '/demo/pattern.mp4';
        document.body.append(video);
        try {
          return await Promise.race([new Promise((resolve,reject) => {
            video.onerror = () => reject(new Error('Staged demo decode failed: ' + video.error?.code));
            video.ontimeupdate = () => {if(video.currentTime > 0.1 && video.videoWidth > 0) resolve({width:video.videoWidth,time:video.currentTime});};
            video.play().catch(reject);
          }), new Promise((_,reject) => setTimeout(() => reject(new Error('Video decode timeout')),15000))]);
        } finally {video.pause();video.removeAttribute('src');video.load();video.remove();}
      });
      assert(result.width > 0 && result.time > 0.1);
    });
    await scenario('remote-input-rejects-invalid-pairing-without-a-network-write', async () => {
      await page.goto(origin + '/swop-input/', {waitUntil:'load'});
      const outsideBefore = blockedRequests.length;
      await page.locator('#link').fill('invalid synthetic pairing link');
      await page.locator('#pairing button[type=submit]').click();
      await page.waitForFunction(() => document.querySelector('#status').textContent.includes('unavailable or expired'));
      assert.equal(await page.locator('#entry').isVisible(), false);
      assert.equal(blockedRequests.length, outsideBefore, 'Invalid pairing must not contact an external service');
      assert(!requests.some(request => request.path.startsWith('/.herenow/data/')), 'Invalid input must not contact pairing storage');
    });
    await scenario('remote-input-supports-mobile-layout-and-keyboard', async () => {
      await page.setViewportSize({width:390,height:844});
      const width = await page.evaluate(() => ({viewport:innerWidth,content:document.documentElement.scrollWidth}));
      assert(width.content <= width.viewport, 'Mobile page must not overflow horizontally');
      await page.locator('#link').focus();
      // WebKit uses Option+Tab when full keyboard access is disabled by the OS.
      await page.keyboard.press(engine === 'webkit' ? 'Alt+Tab' : 'Tab');
      assert.equal(await page.locator(':focus').textContent(), 'Connect to TV');
    });
    assert.deepEqual(errors, []);
    assert.equal(scenarios.length, 6);
    fs.writeFileSync(path.join(diagnostics,'blocked-requests.json'),JSON.stringify(blockedRequests,null,2));
    const discovery = new URL(await page.evaluate(() => window.__OTT_CONTROL_DISCOVERY_URL__));
    // The player may probe its configured discovery endpoint on startup. The
    // request stays blocked; all other external attempts are unexpected here.
    assert(blockedRequests.every(request => request.method === 'GET'
      && request.url === discovery.origin + discovery.pathname), 'Unexpected external request');
    fs.writeFileSync(process.env.CI_BROWSER_EVIDENCE, JSON.stringify({...runtime, scenarios,
      testsPassed:scenarios.length, testsFailed:0,
      indexSha256:crypto.createHash('sha256').update(assets.get('/index.html')).digest('hex')},null,2)+'\n');
  } catch(error) {
    if(page) await page.screenshot({path:path.join(diagnostics,'failure.png'),fullPage:true}).catch(()=>{});
    throw error;
  } finally {
    if(context) await context.tracing.stop({path:path.join(diagnostics,'trace.zip')}).catch(()=>{});
    if(browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => {console.error(error);process.exitCode=1;});
