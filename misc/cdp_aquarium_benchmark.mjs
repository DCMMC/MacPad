import process from "node:process";

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && index + 1 < process.argv.length
    ? process.argv[index + 1]
    : fallback;
}

const endpoint = option("--endpoint", "http://127.0.0.1:19222");
const aquariumURL = option(
  "--url", "https://webglsamples.org/aquarium/aquarium.html");
const fish = Number(option("--fish", "5000"));
const warmupSeconds = Number(option("--warmup", "5"));
const durationSeconds = Number(option("--duration", "15"));
const requestTimeoutMilliseconds = Number(option("--request-timeout", "30000"));
const shouldNavigate = process.argv.includes("--navigate");
const shouldTrace = process.argv.includes("--trace");
const shouldIgnoreCertificateErrors =
  process.argv.includes("--ignore-certificate-errors");
const trace = message => {
  if (shouldTrace) console.error(`[aquarium-benchmark] ${message}`);
};

if (!Number.isFinite(fish) || fish < 1 ||
    !Number.isFinite(warmupSeconds) || warmupSeconds < 0 ||
    !Number.isFinite(durationSeconds) || durationSeconds < 1) {
  throw new Error("--fish/--duration must be positive and --warmup nonnegative");
}

let nextID = 1;
const pending = new Map();
let socket;

async function connect() {
  // Chromium validates the Host header on the discovery endpoint.  Keep the
  // device-side listening address even when endpoint is an SSH local forward.
  // The chroot's main thread can take several seconds to service discovery
  // while a renderer or GPU process is starting, so transient empty replies
  // are expected and must not invalidate a benchmark run.
  let target;
  let lastError;
  const deadline = Date.now() + 30_000;
  while (!target && Date.now() < deadline) {
    try {
      const response = await fetch(`${endpoint}/json/list`, {
        headers: {Host: "127.0.0.1:9222"},
      });
      const targets = await response.json();
      target = targets.find(item =>
        item.type === "page" && item.url === aquariumURL) ||
        targets.find(item => item.type === "page");
    } catch (error) {
      lastError = error;
    }
    if (!target) await delay(250);
  }
  if (!target) {
    throw new Error(`no CDP page target found: ${lastError || "timeout"}`);
  }
  const socketURL = target.webSocketDebuggerUrl.replace(
    "ws://127.0.0.1:9222", endpoint.replace(/^http/, "ws"));
  socket = new WebSocket(socketURL);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, {once: true});
    socket.addEventListener("error", reject, {once: true});
  });
  socket.addEventListener("message", event => {
    const message = JSON.parse(String(event.data));
    if (!message.id) return;
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    clearTimeout(waiter.timer);
    if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
    else waiter.resolve(message.result);
  });
  socket.addEventListener("close", () => {
    for (const waiter of pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(new Error("CDP socket closed"));
    }
    pending.clear();
  }, {once: true});
}

await connect();

function call(method, params = {}) {
  const id = nextID++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP request timed out: ${method}`));
    }, requestTimeoutMilliseconds);
    pending.set(id, {resolve, reject, timer});
    socket.send(JSON.stringify({id, method, params}));
  });
}

async function evaluate(expression) {
  const result = await call("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(JSON.stringify(result.exceptionDetails));
  }
  return result.result.value;
}

async function delay(milliseconds) {
  await new Promise(resolve => setTimeout(resolve, milliseconds));
}

await call("Runtime.enable");
await call("Page.enable");
if (shouldIgnoreCertificateErrors) {
  // Benchmark-only transport accommodation.  The macOS Security framework is
  // not reachable from this chroot, and Chromium's network service therefore
  // cannot load the system trust store (runtime log: certificate error 206).
  // Keep this explicit and out of the production VS Code launch arguments.
  await call("Security.setIgnoreCertificateErrors", {ignore: true});
}
trace("CDP runtime/page enabled");
if (shouldNavigate) {
  // Electron destroys the original renderer/CDP execution context while
  // replacing the VS Code workbench with a network page.  Page.navigate's
  // response can disappear with that context, leaving an awaited request
  // unresolved.  Send it as a one-way command, leave enough time for the
  // browser process to consume the frame, then deliberately reconnect to the
  // surviving page target before evaluating anything in the new context.
  socket.send(JSON.stringify({
    id: nextID++, method: "Page.navigate", params: {url: aquariumURL},
  }));
  await delay(1_500);
  // Wait for the old renderer socket to finish closing before connecting the
  // replacement target.  Otherwise its delayed `close` event can reject the
  // new socket's Runtime.enable waiter through the shared pending map.
  const oldSocket = socket;
  if (oldSocket.readyState < WebSocket.CLOSING) {
    const closed = new Promise(resolve =>
      oldSocket.addEventListener("close", resolve, {once: true}));
    try { oldSocket.close(); } catch (_) {}
    await Promise.race([closed, delay(2_000)]);
  }
  await delay(250);
  await connect();
  await call("Runtime.enable");
  await call("Page.enable");
  trace("navigation complete and replacement target connected");
}

const settingIndex = [1, 100, 500, 1000, 5000, 10000, 15000, 20000,
  25000, 30000].indexOf(fish);
if (settingIndex < 0) {
  throw new Error("fish must match one of Aquarium's fixed presets");
}

const loadDeadline = Date.now() + 45_000;
trace("waiting for Aquarium DOM");
while (Date.now() < loadDeadline) {
  try {
    const ready = await evaluate(`({
      state: document.readyState,
      fps: Boolean(document.getElementById("fps")),
      canvas: Boolean(document.getElementById("canvas")),
      preset: Boolean(document.getElementById("setSetting${settingIndex}"))
    })`);
    if (ready.state === "complete" && ready.fps && ready.canvas &&
        ready.preset) break;
  } catch (_) {
    // The old execution context is destroyed during navigation.
    try { socket.close(); } catch (_) {}
    await delay(250);
    await connect();
    await call("Runtime.enable");
    await call("Page.enable");
  }
  await delay(250);
}

trace("Aquarium DOM ready; selecting preset and reading context metadata");
const setup = await evaluate(`(() => {
  const control = document.getElementById("setSetting${settingIndex}");
  if (!control) return {
    error: "fish preset control not found",
    href: location.href,
    title: document.title,
    ids: Array.from(document.querySelectorAll("[id]"), e => e.id).slice(0, 30)
  };
  control.click();
  const canvas = document.getElementById("canvas");
  const benchmark = {
    samples: [],
    frameCallbacks: 0,
    done: false,
    startedAt: null,
    stoppedAt: null,
    measurementStartAt: performance.now() + ${warmupSeconds * 1000},
    nextSampleAt: performance.now() + ${warmupSeconds * 1000 + 1000}
  };
  globalThis.__macwsAquariumBenchmark = benchmark;
  const originalFPSUpdate = g_fpsTimer.update.bind(g_fpsTimer);
  g_fpsTimer.update = elapsedTime => {
    originalFPSUpdate(elapsedTime);
    const now = performance.now();
    if (now < benchmark.measurementStartAt || benchmark.done) return;
    benchmark.frameCallbacks++;
    if (benchmark.startedAt === null) {
      benchmark.startedAt = now;
    }
    while (now >= benchmark.nextSampleAt &&
           benchmark.samples.length < ${durationSeconds}) {
      const value = Number(g_fpsTimer.averageFPS);
      if (Number.isFinite(value)) benchmark.samples.push(value);
      benchmark.nextSampleAt += 1000;
    }
    if (benchmark.samples.length >= ${durationSeconds}) {
      benchmark.stoppedAt = now;
      benchmark.done = true;
      const published = {
        samples: benchmark.samples,
        frameCallbacks: benchmark.frameCallbacks,
        startedAt: benchmark.startedAt,
        stoppedAt: benchmark.stoppedAt,
        done: true
      };
      document.title = "MACWS_AQUARIUM_RESULT:" +
        btoa(JSON.stringify(published));
      // Aquarium schedules its successor after g_fpsTimer.update returns.
      // Replace only that future scheduling call, after the full sample set.
      globalThis.requestAnimationFrame = () => 0;
    }
  };
  // Aquarium declares its page-owned context as global gl. Read that
  // object directly: requesting another context from the canvas contaminates
  // the sample and can make webgl-debug report an incompatible context type.
  const gl = globalThis.gl || null;
  const selectedFishIndex = globalThis.g && g.globals
    ? Number(g.globals.fishSetting) : null;
  const selectedFish = Number.isInteger(selectedFishIndex) &&
    Array.isArray(globalThis.g_numFish)
      ? Number(g_numFish[selectedFishIndex]) : null;
  const perSpeciesFish = Number.isInteger(selectedFishIndex) &&
    Array.isArray(globalThis.g_fishTable)
      ? g_fishTable.map(info => Number(info.num[selectedFishIndex])) : null;
  return {
    title: document.title,
    canvasWidth: canvas.width,
    canvasHeight: canvas.height,
    contextType: gl && typeof WebGL2RenderingContext !== "undefined" &&
      gl instanceof WebGL2RenderingContext ? "webgl2" : gl ? "webgl" : null,
    contextConstructor: gl && gl.constructor ? gl.constructor.name : null,
    selectedFishIndex,
    selectedFish,
    perSpeciesFish,
    totalFishFromDrawTable: perSpeciesFish
      ? perSpeciesFish.reduce((total, value) => total + value, 0) : null,
    // Chromium's synchronous getParameter/getExtension path can wait for a
    // GPU round-trip on this experimental native-AGX stack.  It is outside
    // the workload and must not stall or bias the FPS interval.
    synchronousGPUInfoQuery: "not-run"
  };
})()`);
if (setup.error) throw new Error(JSON.stringify(setup));
trace(`setup complete (${setup.contextType}, ${setup.canvasWidth}x${setup.canvasHeight})`);

// The current iPad workload can saturate the renderer main thread enough to
// starve timers and external CDP evaluates between frames. The setup
// expression above samples from Aquarium's own per-frame g_fpsTimer.update
// callback and publishes through document.title. Browser-target discovery is
// served by the browser process and remains responsive under renderer load.
trace("in-page warm-up and FPS collection running");
await delay((warmupSeconds + durationSeconds) * 1_000 + 2_000);
const discoveryResponse = await fetch(`${endpoint}/json/list`, {
  headers: {Host: "127.0.0.1:9222"},
});
const discoveryTargets = await discoveryResponse.json();
const resultPrefix = "MACWS_AQUARIUM_RESULT:";
const publishedTarget = discoveryTargets.find(target =>
  target.type === "page" && target.title.startsWith(resultPrefix));
const collection = publishedTarget
  ? JSON.parse(Buffer.from(
      publishedTarget.title.slice(resultPrefix.length), "base64").toString("utf8"))
  : null;
if (!collection || !collection.done) {
  let failureProbe = null;
  try {
    failureProbe = await evaluate(`({
      visibilityState: document.visibilityState,
      hasFocus: document.hasFocus(),
      frameCount: typeof frameCount === "number" ? frameCount : null,
      pageFPS: globalThis.g_fpsTimer ? g_fpsTimer.averageFPS : null,
      benchmark: globalThis.__macwsAquariumBenchmark || null,
      title: document.title
    })`);
  } catch (error) {
    failureProbe = {evaluationError: String(error)};
  }
  throw new Error(
    `in-page FPS collection did not publish: targets=${JSON.stringify(discoveryTargets)} ` +
    `probe=${JSON.stringify(failureProbe)}`);
}
const samples = collection.samples;
samples.forEach((value, index) =>
  trace(`sample ${index + 1}/${samples.length}: ${value}`));

const sorted = [...samples].sort((a, b) => a - b);
const sum = samples.reduce((total, value) => total + value, 0);
const result = {
  url: aquariumURL,
  fish,
  warmupSeconds,
  durationSeconds,
  setup,
  samples,
  frameCallbacks: collection.frameCallbacks,
  measuredCallbackFPS: collection.frameCallbacks &&
      collection.stoppedAt > collection.startedAt
    ? collection.frameCallbacks * 1000 /
      (collection.stoppedAt - collection.startedAt) : null,
  sampleCount: samples.length,
  averageFPS: samples.length ? sum / samples.length : null,
  medianFPS: samples.length ? sorted[Math.floor(sorted.length / 2)] : null,
  minimumFPS: samples.length ? sorted[0] : null,
  maximumFPS: samples.length ? sorted[sorted.length - 1] : null,
};
console.log(JSON.stringify(result, null, 2));
socket.close();
