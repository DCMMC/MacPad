// Repeatable Chrome DevTools Protocol runner for the WebGL Aquarium workload.
//
// Usage:
//   node misc/aquarium_cdp_benchmark.mjs \
//     --host 192.168.1.5 --port 9222 --fish 30000 --seconds 15

import process from "node:process";

function parseArgs(argv) {
  const args = {
    host: "127.0.0.1",
    port: 9222,
    fish: 30000,
    seconds: 15,
    width: 1024,
    height: 1024,
    warmup: 8,
    mode: "full",
    driver: "raf",
    directFrames: 12,
    url: "https://webglsamples.org/aquarium/aquarium.html",
  };
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]?.replace(/^--/, "");
    const value = argv[index + 1];
    if (!(key in args) || value === undefined) {
      throw new Error(`unknown or incomplete argument: ${argv[index]}`);
    }
    args[key] = ["host", "mode", "driver", "url"].includes(key)
      ? value : Number(value);
  }
  for (const key of ["port", "fish", "seconds", "width", "height", "warmup"]) {
    if (!Number.isFinite(args[key]) || args[key] <= 0) {
      throw new Error(`invalid --${key}: ${args[key]}`);
    }
  }
  if (!["full", "no-draw", "no-uniforms", "js-only"].includes(args.mode)) {
    throw new Error(`invalid --mode: ${args.mode}`);
  }
  if (!["raf", "direct"].includes(args.driver)) {
    throw new Error(`invalid --driver: ${args.driver}`);
  }
  if (!Number.isInteger(args.directFrames) || args.directFrames <= 0) {
    throw new Error(`invalid --directFrames: ${args.directFrames}`);
  }
  return args;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${options.method ?? "GET"} ${url}: HTTP ${response.status}`);
  }
  return response.json();
}

class CDP {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
    this.closedError = null;
    this.socket = new WebSocket(url);
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id !== undefined) {
        const waiter = this.pending.get(message.id);
        if (!waiter) return;
        this.pending.delete(message.id);
        clearTimeout(waiter.timer);
        if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
        else waiter.resolve(message.result);
        return;
      }
      const waiters = this.events.get(message.method);
      if (!waiters) return;
      this.events.delete(message.method);
      for (const waiter of waiters) waiter.resolve(message.params);
    });
    const failPending = (error) => {
      if (this.closedError) return;
      this.closedError = error;
      for (const waiter of this.pending.values()) {
        clearTimeout(waiter.timer);
        waiter.reject(error);
      }
      this.pending.clear();
      for (const waiters of this.events.values()) {
        for (const waiter of waiters) {
          clearTimeout(waiter.timer);
          waiter.reject(error);
        }
      }
      this.events.clear();
    };
    this.socket.addEventListener("error", () => {
      failPending(new Error(`CDP WebSocket error: ${url}`));
    });
    this.socket.addEventListener("close", (event) => {
      failPending(new Error(
        `CDP WebSocket closed: code=${event.code} reason=${event.reason || "none"}`,
      ));
    });
  }

  async open() {
    if (this.socket.readyState === WebSocket.OPEN) return;
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
  }

  send(method, params = {}, timeoutMs = 30000) {
    if (this.closedError) return Promise.reject(this.closedError);
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!this.pending.delete(id)) return;
        reject(new Error(`timeout waiting for CDP ${method} (${timeoutMs} ms)`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });
    try {
      this.socket.send(JSON.stringify({ id, method, params }));
    } catch (error) {
      const waiter = this.pending.get(id);
      if (waiter) {
        this.pending.delete(id);
        clearTimeout(waiter.timer);
        waiter.reject(error);
      }
    }
    return result;
  }

  waitFor(method, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      if (this.closedError) {
        reject(this.closedError);
        return;
      }
      const waiters = this.events.get(method) ?? [];
      const waiter = {
        timer: null,
        resolve: (params) => {
          clearTimeout(waiter.timer);
          resolve(params);
        },
        reject,
      };
      waiter.timer = setTimeout(() => {
        const current = this.events.get(method);
        if (current) {
          const index = current.indexOf(waiter);
          if (index !== -1) current.splice(index, 1);
          if (current.length === 0) this.events.delete(method);
        }
        reject(new Error(`timeout waiting for ${method}`));
      }, timeoutMs);
      waiters.push(waiter);
      this.events.set(method, waiters);
    });
  }

  close() {
    this.socket.close();
  }
}

function percentile(sorted, fraction) {
  if (sorted.length === 0) return null;
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))];
}

const args = parseArgs(process.argv.slice(2));
const endpoint = `http://${args.host}:${args.port}`;
let page;
try {
  page = await fetchJson(`${endpoint}/json/new?about:blank`, { method: "PUT" });
} catch (error) {
  // Electron/VS Code exposes page targets but rejects the browser-level
  // /json/new endpoint. Reuse its only page in that case; this benchmark runs
  // from a disposable VS Code profile and intentionally replaces the workbench.
  const pages = await fetchJson(`${endpoint}/json/list`);
  page = pages.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
  if (!page) throw error;
}
const cdp = new CDP(page.webSocketDebuggerUrl);
await cdp.open();
await cdp.send("Page.enable");
await cdp.send("Runtime.enable");

const aquariumUrl = new URL(args.url);
aquariumUrl.searchParams.set("numFish", String(args.fish));
aquariumUrl.searchParams.set("canvasWidth", String(args.width));
aquariumUrl.searchParams.set("canvasHeight", String(args.height));
aquariumUrl.searchParams.set("fitWindow", "false");

// A VS Code Simple Browser page is a dedicated DevTools target, but navigating
// that target destroys and recreates the webview guest even when the URL is
// unchanged.  Reuse an already-correct target so its WebSocket and renderer
// remain stable; ordinary Chrome/blank targets still take the navigation path.
if (page.url !== aquariumUrl.href) {
  const loaded = cdp.waitFor("Page.loadEventFired", 60000);
  await cdp.send("Page.navigate", { url: aquariumUrl.href });
  await loaded;
}

const deadline = Date.now() + 90000;
let readiness;
while (Date.now() < deadline) {
  const response = await cdp.send("Runtime.evaluate", {
    expression: `(() => {
      const canvas = document.querySelector('#canvas');
      const fps = document.querySelector('#fps')?.textContent?.trim() || '';
      if (!canvas || !fps) return {ready:false, fps};
      return {
        // Do not call getContext/getParameter here. On the cross-version AGX
        // stack those synchronous GPU IPC calls can starve CDP even while the
        // page is visibly animating. Canvas + the page's live FPS element are
        // sufficient readiness witnesses; command-error logs and VNC pixels
        // are collected independently.
        ready: true,
        fps,
        contextType: typeof gl !== 'undefined' ? gl?.constructor?.name || null : null,
        contextLost: typeof gl !== 'undefined' && typeof gl?.isContextLost === 'function'
          ? gl.isContextLost() : null,
        canvas: [canvas.width, canvas.height],
        fishSettingIndex: typeof g !== 'undefined' ? g?.globals?.fishSetting ?? null : null,
        fishCount: typeof g_numFish !== 'undefined' && Array.isArray(g_numFish) &&
          typeof g !== 'undefined' ? g_numFish[g?.globals?.fishSetting] ?? null : null,
        finalUrl: location.href,
        title: document.title,
      };
    })()`,
    returnByValue: true,
  });
  readiness = response.result.value;
  if (readiness?.ready) break;
  await new Promise((resolve) => setTimeout(resolve, 500));
}
if (!readiness?.ready) throw new Error(`Aquarium did not become ready: ${JSON.stringify(readiness)}`);

// aquarium.js consumes numFish by replacing g_numFish[0], then
// setupCountButtons() selects fishSetting index 0.  g_numFish must remain an
// array: replacing it with the requested count would only corrupt the page
// after its per-species tables had already been built and could create a false
// benchmark witness.  Validate both the selected entry and the independently
// precomputed per-species totals before accepting a run.
const configuredResponse = await cdp.send("Runtime.evaluate", {
  expression: `(() => {
    const canvas = document.querySelector('#canvas');
    const fishSettingIndex = typeof g !== 'undefined' ? g?.globals?.fishSetting ?? null : null;
    const fishCount = typeof g_numFish !== 'undefined' && Array.isArray(g_numFish)
      ? g_numFish[fishSettingIndex] ?? null : null;
    const modelFishCount = typeof g_fishTable !== 'undefined' && Array.isArray(g_fishTable)
      ? g_fishTable.reduce((total, fishInfo) =>
          total + (fishInfo?.num?.[fishSettingIndex] ?? 0), 0)
      : null;
    const uniformMethods = [
      'uniform1f', 'uniform1fv', 'uniform1i', 'uniform1iv',
      'uniform2f', 'uniform2fv', 'uniform2i', 'uniform2iv',
      'uniform3f', 'uniform3fv', 'uniform3i', 'uniform3iv',
      'uniform4f', 'uniform4fv', 'uniform4i', 'uniform4iv',
      'uniformMatrix2fv', 'uniformMatrix3fv', 'uniformMatrix4fv',
    ];
    const methodsByMode = {
      'full': [],
      'no-draw': ['drawArrays', 'drawElements'],
      'no-uniforms': uniformMethods,
      'js-only': [...uniformMethods, 'drawArrays', 'drawElements'],
    };
    const requestedMethods = methodsByMode[${JSON.stringify(args.mode)}];
    const patchedMethods = [];
    for (const name of requestedMethods) {
      if (typeof gl?.[name] !== 'function') continue;
      try {
        gl[name] = () => undefined;
        if (Object.prototype.hasOwnProperty.call(gl, name)) patchedMethods.push(name);
      } catch (_) {}
    }
    return {
      fishSettingIndex,
      fishCount,
      modelFishCount,
      canvas: canvas ? [canvas.width, canvas.height] : null,
      contextType: typeof gl !== 'undefined' ? gl?.constructor?.name || null : null,
      contextLost: typeof gl !== 'undefined' && typeof gl?.isContextLost === 'function'
        ? gl.isContextLost() : null,
      mode: ${JSON.stringify(args.mode)},
      requestedPatchCount: requestedMethods.length,
      patchedMethods,
    };
  })()`,
  returnByValue: true,
});
const configured = configuredResponse.result.value;
if (configured?.fishCount !== args.fish ||
    configured?.modelFishCount !== args.fish ||
    configured?.canvas?.[0] !== args.width ||
    configured?.canvas?.[1] !== args.height ||
    configured?.contextLost !== false ||
    configured?.patchedMethods?.length !== configured?.requestedPatchCount) {
  throw new Error(`Aquarium fish count was not applied: ${JSON.stringify(configured)}`);
}

await new Promise((resolve) => setTimeout(resolve, args.warmup * 1000));
const benchmarkExpression = args.driver === "direct" ? `(() => {
    if (typeof g_onAnimationFrame !== 'function') {
      return {error: 'missing g_onAnimationFrame', durations: []};
    }
    cancelAnimationFrame(g_requestId);
    const durations = [];
    const started = performance.now();
    for (let index = 0; index < ${args.directFrames}; ++index) {
      const frameStarted = performance.now();
      g_onAnimationFrame();
      cancelAnimationFrame(g_requestId);
      durations.push(performance.now() - frameStarted);
    }
    const ended = performance.now();
    g_requestId = requestAnimationFrame(g_onAnimationFrame);
    return {started, ended, durations, timedOut: false};
  })()` : `new Promise((resolve) => {
    const durationMs = ${args.seconds * 1000};
    const started = performance.now();
    const stamps = [];
    const timer = globalThis.g_fpsTimer;
    if (!timer || typeof timer.update !== 'function') {
      resolve({started, ended: performance.now(), stamps, error: 'missing g_fpsTimer'});
      return;
    }
    const originalUpdate = timer.update;
    let settled = false;
    function finish(timedOut) {
      if (settled) return;
      settled = true;
      timer.update = originalUpdate;
      resolve({started, ended: performance.now(), stamps, timedOut});
    }
    timer.update = function(...callArgs) {
      const now = performance.now();
      stamps.push(now);
      const result = originalUpdate.apply(this, callArgs);
      if (now - started >= durationMs) finish(false);
      return result;
    };
    setTimeout(() => finish(true), durationMs + 10000);
  })`;
const benchmark = await cdp.send("Runtime.evaluate", {
  expression: benchmarkExpression,
  awaitPromise: true,
  returnByValue: true,
}, (args.seconds + 30) * 1000);

const raw = benchmark.result.value;
if (raw.error) throw new Error(`Aquarium frame instrumentation failed: ${raw.error}`);
const frameSamples = args.driver === "direct" ? raw.durations : raw.stamps;
if (raw.timedOut || !Array.isArray(frameSamples) || frameSamples.length < 2) {
  throw new Error(`Aquarium render loop stalled: ${JSON.stringify(raw)}`);
}
const intervals = args.driver === "direct"
  ? raw.durations
  : raw.stamps.slice(1).map((stamp, index) => stamp - raw.stamps[index]);
const sortedIntervals = [...intervals].sort((left, right) => left - right);
const elapsedSeconds = (raw.ended - raw.started) / 1000;
const callbackFps = frameSamples.length / elapsedSeconds;
const instantaneousFps = intervals.map((interval) => 1000 / interval).filter(Number.isFinite);
const sortedFps = [...instantaneousFps].sort((left, right) => left - right);

const result = {
  timestamp: new Date().toISOString(),
  target: endpoint,
  url: aquariumUrl.href,
  fish: args.fish,
  mode: args.mode,
  driver: args.driver,
  requestedSeconds: args.seconds,
  elapsedSeconds,
  pageFrames: frameSamples.length,
  pageFrameFps: callbackFps,
  frameIntervalMs: {
    min: sortedIntervals[0] ?? null,
    p50: percentile(sortedIntervals, 0.50),
    p95: percentile(sortedIntervals, 0.95),
    max: sortedIntervals.at(-1) ?? null,
  },
  instantaneousFps: {
    min: sortedFps[0] ?? null,
    p50: percentile(sortedFps, 0.50),
    p95: percentile(sortedFps, 0.95),
    max: sortedFps.at(-1) ?? null,
    average: instantaneousFps.reduce((sum, value) => sum + value, 0) / instantaneousFps.length,
  },
  webgl: { ...readiness, ...configured },
};

console.log(JSON.stringify(result, null, 2));
cdp.close();
