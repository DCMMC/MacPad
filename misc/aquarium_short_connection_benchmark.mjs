// Measure an already-loaded WebGL Aquarium target without keeping a CDP
// websocket open while the native-AGX renderer is saturated. The page records
// its own g_fpsTimer.update timestamps; the host disconnects, waits, and uses
// bounded short connections to read the result.

import process from "node:process";

const args = {host: "127.0.0.1", port: 9222, seconds: 15, warmup: 8};
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index]?.replace(/^--/, "");
  const value = process.argv[index + 1];
  if (!(key in args) || value === undefined) {
    throw new Error(`unknown or incomplete argument: ${process.argv[index]}`);
  }
  args[key] = key === "host" ? value : Number(value);
}
if (!Number.isInteger(args.port) || args.port <= 0 || args.port > 65535 ||
    !Number.isFinite(args.seconds) || args.seconds <= 0 ||
    !Number.isFinite(args.warmup) || args.warmup < 0) {
  throw new Error(`invalid arguments: ${JSON.stringify(args)}`);
}

const endpoint = `http://${args.host}:${args.port}`;
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function target() {
  const response = await fetch(`${endpoint}/json/list`);
  if (!response.ok) throw new Error(`CDP discovery HTTP ${response.status}`);
  const targets = await response.json();
  const page = targets.find(item => item.type === "page" &&
    item.url.includes("webglsamples.org/aquarium/aquarium.html") &&
    item.webSocketDebuggerUrl);
  if (!page) throw new Error("no live Aquarium CDP target");
  return page;
}

async function evaluate(expression, timeoutMilliseconds = 10_000) {
  const page = await target();
  const socketURL = page.webSocketDebuggerUrl
    .replace("ws://127.0.0.1:9222", `ws://${args.host}:${args.port}`);
  const socket = new WebSocket(socketURL);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("CDP open timeout")),
      timeoutMilliseconds);
    socket.addEventListener("open", () => {
      clearTimeout(timer);
      resolve();
    }, {once: true});
    socket.addEventListener("error", reject, {once: true});
  });
  const id = 1;
  try {
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("CDP evaluate timeout")),
        timeoutMilliseconds);
      socket.addEventListener("message", event => {
        const message = JSON.parse(String(event.data));
        if (message.id !== id) return;
        clearTimeout(timer);
        if (message.error) reject(new Error(JSON.stringify(message.error)));
        else if (message.result?.exceptionDetails) {
          reject(new Error(JSON.stringify(message.result.exceptionDetails)));
        } else resolve(message.result?.result?.value);
      });
      socket.addEventListener("close", () => {
        clearTimeout(timer);
        reject(new Error("CDP closed before evaluate reply"));
      }, {once: true});
      socket.send(JSON.stringify({
        id,
        method: "Runtime.evaluate",
        params: {expression, returnByValue: true},
      }));
    });
  } finally {
    try { socket.close(); } catch (_) {}
  }
}

const setup = await evaluate(`(() => {
  const canvas = document.querySelector('#canvas');
  const timer = globalThis.g_fpsTimer;
  if (!canvas || !timer || typeof timer.update !== 'function') {
    return {ready:false, canvas:Boolean(canvas), timer:Boolean(timer)};
  }
  const prior = globalThis.__macwsShortConnectionBenchmark;
  if (prior && typeof prior.originalUpdate === 'function') {
    timer.update = prior.originalUpdate;
  }
  const state = {
    version: 1,
    warmupMilliseconds: ${args.warmup * 1000},
    durationMilliseconds: ${args.seconds * 1000},
    installedAt: performance.now(),
    startedAt: null,
    endedAt: null,
    stamps: [],
    done: false,
    originalUpdate: timer.update,
  };
  timer.update = function(...callArgs) {
    const now = performance.now();
    const result = state.originalUpdate.apply(this, callArgs);
    const measurementStart = state.installedAt + state.warmupMilliseconds;
    if (now >= measurementStart && !state.done) {
      if (state.startedAt === null) state.startedAt = now;
      state.stamps.push(now);
      if (now - measurementStart >= state.durationMilliseconds) {
        state.done = true;
        state.endedAt = now;
        timer.update = state.originalUpdate;
      }
    }
    return result;
  };
  globalThis.__macwsShortConnectionBenchmark = state;
  return {
    ready: true,
    url: location.href,
    canvas: [canvas.width, canvas.height],
    fish: Array.isArray(globalThis.g_numFish) && globalThis.g?.globals
      ? g_numFish[g.globals.fishSetting] : null,
    modelFish: Array.isArray(globalThis.g_fishTable) && globalThis.g?.globals
      ? g_fishTable.reduce((sum, item) =>
          sum + (item?.num?.[g.globals.fishSetting] ?? 0), 0) : null,
    context: globalThis.gl?.constructor?.name ?? null,
    contextLost: typeof globalThis.gl?.isContextLost === 'function'
      ? gl.isContextLost() : null,
  };
})()`);
if (!setup?.ready) throw new Error(`Aquarium not ready: ${JSON.stringify(setup)}`);

const deadline = Date.now() + (args.warmup + args.seconds + 20) * 1000;
let state;
let lastError;
await delay((args.warmup + args.seconds) * 1000);
while (Date.now() < deadline) {
  try {
    state = await evaluate(`(() => {
      const source = globalThis.__macwsShortConnectionBenchmark;
      if (!source) return null;
      return {
        version: source.version,
        startedAt: source.startedAt,
        endedAt: source.endedAt,
        stamps: source.stamps,
        done: source.done,
        contextLost: typeof globalThis.gl?.isContextLost === 'function'
          ? gl.isContextLost() : null,
      };
    })()`);
    if (state?.done) break;
  } catch (error) {
    lastError = error;
  }
  await delay(500);
}
if (!state?.done || !Array.isArray(state.stamps) || state.stamps.length < 2) {
  throw new Error(`benchmark did not finish: state=${JSON.stringify(state)} ` +
    `lastError=${String(lastError || "none")}`);
}

const intervals = state.stamps.slice(1).map((stamp, index) =>
  stamp - state.stamps[index]);
const sorted = [...intervals].sort((left, right) => left - right);
const percentile = fraction => sorted[Math.min(sorted.length - 1,
  Math.floor(sorted.length * fraction))];
const elapsedSeconds = (state.endedAt - state.startedAt) / 1000;
console.log(JSON.stringify({
  timestamp: new Date().toISOString(),
  target: endpoint,
  setup,
  callbacks: state.stamps.length,
  elapsedSeconds,
  callbackFps: state.stamps.length / elapsedSeconds,
  intervalMilliseconds: {
    minimum: sorted[0],
    p50: percentile(0.50),
    p95: percentile(0.95),
    maximum: sorted.at(-1),
  },
  contextLost: state.contextLost,
}, null, 2));
