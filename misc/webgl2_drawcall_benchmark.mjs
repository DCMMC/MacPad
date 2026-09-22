// CDP-driven WebGL2 microbenchmark for separating command-encoding cost from
// GPU execution cost in the exact Chromium process under test.
//
// Example:
//   node misc/webgl2_drawcall_benchmark.mjs \
//     --host 192.168.1.5 --port 9222 --mode draw --draws 1000 --seconds 8

import process from "node:process";
import { writeFile } from "node:fs/promises";

function parseArgs(argv) {
  const args = {
    host: "127.0.0.1",
    port: 9222,
    mode: "draw",
    draws: 1000,
    seconds: 8,
    width: 512,
    height: 512,
    // Keep timer queries opt-in so the control measures the render loop rather
    // than query collection. The private event path itself is now translated
    // to the real iOS IOGPU selector and has a bounded 64/64-query witness.
    timer: 0,
    // `timeout` deliberately removes requestAnimationFrame/presentation
    // scheduling from the producer cadence. Comparing it with `raf` separates
    // command/GPU throughput from Chromium's visible-frame backpressure.
    driver: "raf",
    trace: 0,
    tracefile: "",
    nonce: Date.now(),
    reload: 0,
    settle: 0,
  };
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]?.replace(/^--/, "");
    const value = argv[index + 1];
    if (!(key in args) || value === undefined) {
      throw new Error(`unknown or incomplete argument: ${argv[index]}`);
    }
    args[key] = ["host", "mode", "driver", "tracefile"].includes(key)
      ? value : Number(value);
  }
  if (!["draw", "fill"].includes(args.mode)) {
    throw new Error(`invalid --mode: ${args.mode}`);
  }
  if (!["raf", "timeout"].includes(args.driver)) {
    throw new Error(`invalid --driver: ${args.driver}`);
  }
  for (const key of [
    "port", "draws", "seconds", "width", "height", "nonce",
  ]) {
    if (!Number.isFinite(args[key]) || args[key] <= 0) {
      throw new Error(`invalid --${key}: ${args[key]}`);
    }
  }
  if (![0, 1].includes(args.timer)) {
    throw new Error("--timer must be 0 or 1");
  }
  if (![0, 1].includes(args.trace)) {
    throw new Error("--trace must be 0 or 1");
  }
  if (args.trace && !args.tracefile) {
    throw new Error("--tracefile is required with --trace=1");
  }
  if (![0, 1].includes(args.reload)) {
    throw new Error("--reload must be 0 or 1");
  }
  if (!Number.isFinite(args.settle) || args.settle < 0) {
    throw new Error("--settle must be non-negative");
  }
  return args;
}

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: { Host: "127.0.0.1:9222" },
  });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

class CDP {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.eventWaiters = new Map();
    this.socket = new WebSocket(url);
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id === undefined) {
        const waiter = this.eventWaiters.get(message.method);
        if (!waiter) return;
        this.eventWaiters.delete(message.method);
        clearTimeout(waiter.timer);
        waiter.resolve(message.params || {});
        return;
      }
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      clearTimeout(waiter.timer);
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
      else waiter.resolve(message.result);
    });
  }

  async open(timeoutMs = 10000) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.socket.close();
        reject(new Error("CDP WebSocket open timed out"));
      }, timeoutMs);
      this.socket.addEventListener("open", () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
      this.socket.addEventListener("error", (error) => {
        clearTimeout(timer);
        reject(error);
      }, { once: true });
    });
  }

  send(method, params = {}, timeoutMs = 180000) {
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP request timed out: ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  waitForEvent(method, timeoutMs = 30000) {
    if (this.eventWaiters.has(method)) {
      throw new Error(`already waiting for CDP event: ${method}`);
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.eventWaiters.delete(method);
        reject(new Error(`CDP event timed out: ${method}`));
      }, timeoutMs);
      this.eventWaiters.set(method, { resolve, reject, timer });
    });
  }

  close() {
    this.socket.close();
  }
}

const args = parseArgs(process.argv.slice(2));
const hardWatchdog = setTimeout(() => {
  console.error(`[webgl2-benchmark] hard timeout after ${args.settle + args.seconds + 20}s`);
  process.exit(124);
}, (args.settle + args.seconds + 20) * 1000);
const endpoint = `http://${args.host}:${args.port}`;
const targets = await fetchJson(`${endpoint}/json/list`);
const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
if (!page) throw new Error("no CDP page target found");
console.error(`[webgl2-benchmark] target=${page.id} url=${page.url}`);
const socketUrl = page.webSocketDebuggerUrl.replace(
  "ws://127.0.0.1:9222", `ws://${args.host}:${args.port}`);
const cdp = new CDP(socketUrl);
await cdp.open();
console.error("[webgl2-benchmark] CDP WebSocket open");
await cdp.send("Runtime.enable");
if (args.reload) {
  await cdp.send("Page.enable");
  await cdp.send("Page.reload", { ignoreCache: true });
  console.error(`[webgl2-benchmark] page reloaded; settling ${args.settle}s`);
  await new Promise((resolve) => setTimeout(resolve, args.settle * 1000));
}
console.error("[webgl2-benchmark] Runtime enabled; starting WebGL2 workload");
if (args.trace) {
  const categories = [
    "toplevel", "blink", "cc", "gpu", "viz", "benchmark",
    "disabled-by-default-devtools.timeline",
    "disabled-by-default-devtools.timeline.frame",
    "disabled-by-default-gpu.device",
    "disabled-by-default-viz.quads",
  ].join(",");
  await cdp.send("Tracing.start", {
    categories,
    options: "record-continuously",
    transferMode: "ReturnAsStream",
  });
  console.error(`[webgl2-benchmark] tracing started -> ${args.tracefile}`);
}

const expression = `(async () => {
  const config = ${JSON.stringify(args)};
  const canvas = document.createElement("canvas");
  canvas.width = config.width;
  canvas.height = config.height;
  canvas.style.cssText = "position:fixed;left:0;top:0;width:256px;height:256px;z-index:2147483647";
  document.body.appendChild(canvas);
  const gl = canvas.getContext("webgl2", {
    alpha: false,
    antialias: false,
    depth: false,
    stencil: false,
    preserveDrawingBuffer: false,
  });
  if (!gl) throw new Error("WebGL2 context creation failed");

  // ANGLE removes comments before its Metal compilation/cache key.  Put a
  // tiny, finite literal in the actual shader expression so each run can
  // force a fresh MTLCompilerService request without materially changing the
  // workload.  This is a cache-control witness, not a performance variable.
  const shaderNonce = String((Math.trunc(config.nonce) % 1000000) + 1) + ".0";

  function shader(type, source) {
    const value = gl.createShader(type);
    gl.shaderSource(value, source);
    gl.compileShader(value);
    if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) {
      throw new Error((gl.getShaderInfoLog(value) || "shader compile failed") +
        "\\nSOURCE:\\n" + source);
    }
    return value;
  }

  const vertex = shader(gl.VERTEX_SHADER, \`#version 300 es
    precision highp float;
    uniform float u_phase;
    void main() {
      vec2 p = vec2((gl_VertexID == 1) ? 3.0 : -1.0,
                    (gl_VertexID == 2) ? 3.0 : -1.0);
      gl_Position = vec4(p + vec2(u_phase * 1.0e-7), 0.0, 1.0);
    }\`);
  const fragmentBody = config.mode === "fill"
    ? \`float x = u_phase + gl_FragCoord.x * 0.0001;
       for (int i = 0; i < 32; ++i) x = sin(x * 1.0001 + float(i));
       outColor = vec4(x * 0.5 + 0.5, 0.25, 0.75, 1.0);\`
    : \`outColor = vec4(fract(u_phase), 0.25, 0.75, 1.0);\`;
  const witnessedFragmentBody = fragmentBody +
    "\\noutColor.r += " + shaderNonce + " * 1.0e-12;";
  const fragment = shader(gl.FRAGMENT_SHADER, \`#version 300 es
    precision highp float;
    uniform float u_phase;
    out vec4 outColor;
    void main() { \${witnessedFragmentBody} }
  \`);
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) || "program link failed");
  }
  gl.useProgram(program);
  gl.bindVertexArray(gl.createVertexArray());
  const phase = gl.getUniformLocation(program, "u_phase");
  gl.disable(gl.BLEND);
  gl.disable(gl.DEPTH_TEST);
  if (config.mode === "draw") {
    gl.enable(gl.SCISSOR_TEST);
    gl.scissor(0, 0, 1, 1);
  }
  gl.viewport(0, 0, config.width, config.height);

  // Creating one EXT_disjoint_timer_query_webgl2 query per batch also creates
  // private Metal event traffic. --timer=0 remains the clean scheduling/CPU
  // control; --timer=1 measures real GPU completion through the now-translated
  // IOGPU event constructor.
  const timer = config.timer
    ? gl.getExtension("EXT_disjoint_timer_query_webgl2")
    : null;
  const pendingQueries = [];
  const gpuNanoseconds = [];
  const issueMilliseconds = [];
  const frameStamps = [];
  let totalDraws = 0;
  let phaseValue = 0;
  const started = performance.now();
  const deadline = started + config.seconds * 1000;

  function collectQueries() {
    if (!timer) return;
    while (pendingQueries.length) {
      const query = pendingQueries[0];
      if (!gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)) break;
      const disjoint = gl.getParameter(timer.GPU_DISJOINT_EXT);
      const value = gl.getQueryParameter(query, gl.QUERY_RESULT);
      gl.deleteQuery(query);
      pendingQueries.shift();
      if (!disjoint && Number.isFinite(value)) gpuNanoseconds.push(value);
    }
  }

  const scheduleFrame = (callback) => {
    if (config.driver === "raf") requestAnimationFrame(callback);
    else setTimeout(() => callback(performance.now()), 0);
  };
  await new Promise((resolve) => {
    function frame(now) {
      frameStamps.push(now);
      collectQueries();
      const query = timer ? gl.createQuery() : null;
      if (query) gl.beginQuery(timer.TIME_ELAPSED_EXT, query);
      const issueStart = performance.now();
      for (let index = 0; index < config.draws; ++index) {
        phaseValue += 1;
        // Avoid a constant final color: integer values made fract(u_phase)
        // zero on every batch and contaminated the cadence measurement with
        // Chromium's low-damage scheduling policy.
        gl.uniform1f(phase, phaseValue * 0.001);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      }
      gl.flush();
      issueMilliseconds.push(performance.now() - issueStart);
      if (query) {
        gl.endQuery(timer.TIME_ELAPSED_EXT);
        pendingQueries.push(query);
      }
      totalDraws += config.draws;
      if (performance.now() < deadline) scheduleFrame(frame);
      else resolve();
    }
    scheduleFrame(frame);
  });

  const queryDeadline = performance.now() + 3000;
  while (pendingQueries.length && performance.now() < queryDeadline) {
    collectQueries();
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  const ended = performance.now();
  const sum = (values) => values.reduce((total, value) => total + value, 0);
  const sorted = (values) => [...values].sort((a, b) => a - b);
  const percentile = (values, fraction) => {
    const ordered = sorted(values);
    return ordered.length
      ? ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * fraction))]
      : null;
  };
  const intervals = frameStamps.slice(1).map((stamp, index) => stamp - frameStamps[index]);
  const intervalHistogram10ms = {};
  for (const interval of intervals) {
    const lower = Math.floor(interval / 10) * 10;
    const label = lower + "-" + (lower + 10);
    intervalHistogram10ms[label] = (intervalHistogram10ms[label] || 0) + 1;
  }
  const result = {
    mode: config.mode,
    driver: config.driver,
    drawsPerBatch: config.draws,
    canvas: [config.width, config.height],
    requestedSeconds: config.seconds,
    elapsedSeconds: (ended - started) / 1000,
    batches: issueMilliseconds.length,
    totalDraws,
    wallDrawsPerSecond: totalDraws * 1000 / (ended - started),
    cpuIssueMilliseconds: {
      total: sum(issueMilliseconds),
      average: sum(issueMilliseconds) / issueMilliseconds.length,
      p50: percentile(issueMilliseconds, 0.5),
      p95: percentile(issueMilliseconds, 0.95),
    },
    cpuIssueDrawsPerSecond: totalDraws * 1000 / sum(issueMilliseconds),
    frameIntervalMilliseconds: {
      average: intervals.length ? sum(intervals) / intervals.length : null,
      p10: percentile(intervals, 0.1),
      p50: percentile(intervals, 0.5),
      p95: percentile(intervals, 0.95),
      maximum: intervals.length ? Math.max(...intervals) : null,
      histogram10ms: intervalHistogram10ms,
    },
    gpuTimer: {
      supported: Boolean(timer),
      completedQueries: gpuNanoseconds.length,
      pendingQueries: pendingQueries.length,
      totalMilliseconds: sum(gpuNanoseconds) / 1e6,
      averageMilliseconds: gpuNanoseconds.length
        ? sum(gpuNanoseconds) / gpuNanoseconds.length / 1e6 : null,
      p50Milliseconds: percentile(gpuNanoseconds, 0.5) / 1e6,
      p95Milliseconds: percentile(gpuNanoseconds, 0.95) / 1e6,
    },
    webglVersion: gl.getParameter(gl.VERSION),
    renderer: gl.getParameter(gl.RENDERER),
  };
  canvas.remove();
  return result;
})()`;

let response;
try {
  response = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  }, (args.seconds + 15) * 1000);
} catch (error) {
  cdp.close();
  throw error;
}
if (response.exceptionDetails) {
  throw new Error(JSON.stringify(response.exceptionDetails));
}
if (args.trace) {
  const complete = cdp.waitForEvent("Tracing.tracingComplete", 30000);
  await cdp.send("Tracing.end", {}, 30000);
  const { stream } = await complete;
  if (!stream) throw new Error("Tracing.tracingComplete omitted stream handle");
  let traceData = "";
  for (;;) {
    const chunk = await cdp.send("IO.read", { handle: stream }, 30000);
    traceData += chunk.data || "";
    if (chunk.eof) break;
  }
  await cdp.send("IO.close", { handle: stream }, 30000);
  await writeFile(args.tracefile, traceData);
  console.error(`[webgl2-benchmark] trace bytes=${traceData.length}`);
}
console.log(JSON.stringify({
  timestamp: new Date().toISOString(),
  target: endpoint,
  traceFile: args.trace ? args.tracefile : null,
  result: response.result.value,
}, null, 2));
cdp.close();
clearTimeout(hardWatchdog);
