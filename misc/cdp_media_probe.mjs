// Bounded media/animation probe for the HTTP page already hosted by VSCode's
// Simple Browser. It can navigate that page, scroll a text witness into view,
// request muted playback, sample HTMLVideoElement progress, and capture three
// compositor screenshots without opening DevTools or changing browser flags.

import process from "node:process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const args = {
  host: "127.0.0.1",
  port: 9222,
  url: "",
  seconds: 8,
  loadwait: 3,
  play: 1,
  mute: 1,
  evaluate: 1,
  capture: 1,
  single: 0,
  seek: -1,
  output: "/tmp/macws-media-probe.json",
  screenshots: "/tmp/macws-media-frame",
  text: "",
  offset: 0,
  tone: 0,
};
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index]?.replace(/^--/, "");
  const value = process.argv[index + 1];
  if (!(key in args) || value === undefined) {
    throw new Error(`unknown or incomplete argument: ${process.argv[index]}`);
  }
  args[key] = ["host", "url", "output", "screenshots", "text"].includes(key)
    ? value : Number(value);
}
if (!Number.isInteger(args.port) || args.port <= 0 || args.port > 65535) {
  throw new Error(`invalid --port: ${args.port}`);
}
if (!Number.isFinite(args.seconds) || args.seconds < 1 || args.seconds > 60) {
  throw new Error(`invalid --seconds: ${args.seconds}`);
}
if (!Number.isFinite(args.loadwait) || args.loadwait < 0.25 || args.loadwait > 15) {
  throw new Error(`invalid --loadwait: ${args.loadwait}`);
}
for (const name of ["play", "mute", "evaluate", "capture", "single"]) {
  if (![0, 1].includes(args[name])) throw new Error(`invalid --${name}: ${args[name]}`);
}
if (!Number.isFinite(args.offset) || Math.abs(args.offset) > 20000) {
  throw new Error(`invalid --offset: ${args.offset}`);
}
if (!Number.isFinite(args.seek) || args.seek < -1 || args.seek > 86400) {
  throw new Error(`invalid --seek: ${args.seek}`);
}
if (!Number.isFinite(args.tone) || args.tone < -1 || args.tone > 30) {
  throw new Error(`invalid --tone: ${args.tone}`);
}

class CDP {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
    this.listeners = new Map();
    this.socket = new WebSocket(url);
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id === undefined) {
        for (const listener of this.listeners.get(message.method) || []) {
          listener(message.params || {});
        }
        const waiters = this.events.get(message.method) || [];
        this.events.delete(message.method);
        for (const waiter of waiters) {
          clearTimeout(waiter.timer);
          waiter.resolve(message.params || {});
        }
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

  async open() {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("CDP open timeout")), 10000);
      this.socket.addEventListener("open", () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
  }

  send(method, params = {}, timeoutMs = 30000) {
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`timeout waiting for ${method}`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  wait(method, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`timeout waiting for ${method}`)),
                               timeoutMs);
      const waiters = this.events.get(method) || [];
      waiters.push({ resolve, reject, timer });
      this.events.set(method, waiters);
    });
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  close() { this.socket.close(); }
}

const endpoint = `http://${args.host}:${args.port}`;
const targets = await (await fetch(`${endpoint}/json/list`)).json();
const page = targets.find((target) => target.type === "page" &&
  /^https?:/.test(target.url) && target.webSocketDebuggerUrl) ||
  targets.find((target) => target.type === "page" &&
    !/^vscode-file:/.test(target.url) && target.webSocketDebuggerUrl) ||
  // A fresh isolated benchmark profile initially exposes only the VS Code
  // workbench renderer.  When the caller explicitly requested navigation it
  // is safe to reuse that target; Page.navigate replaces only this disposable
  // profile's renderer.  Never do this for an observe-only probe.
  (args.url ? targets.find((target) => target.type === "page" &&
    target.webSocketDebuggerUrl) : null);
if (!page) throw new Error("no Simple Browser page CDP target");
const socketURL = page.webSocketDebuggerUrl.replace(
  /ws:\/\/127\.0\.0\.1:\d+/, `ws://${args.host}:${args.port}`);
const cdp = new CDP(socketURL);
await cdp.open();
await cdp.send("Page.enable");
await cdp.send("Runtime.enable");

const diagnosticEvents = [];
const diagnosticMethods = [
  "Log.entryAdded",
  "Runtime.exceptionThrown",
  "Media.playersCreated",
  "Media.playerPropertiesChanged",
  "Media.playerEventsAdded",
  "Media.playerMessagesLogged",
  "Media.playerErrorsRaised",
];
const diagnosticsStarted = performance.now();
for (const method of diagnosticMethods) {
  cdp.on(method, (params) => {
    diagnosticEvents.push({
      elapsedMilliseconds: Math.round(performance.now() - diagnosticsStarted),
      method,
      params,
    });
    if (diagnosticEvents.length > 300) diagnosticEvents.shift();
  });
}
await cdp.send("Log.enable").catch(() => null);
const mediaDomainEnabled = await cdp.send("Media.enable")
  .then(() => true, () => false);

if (args.url) {
  const loaded = cdp.wait("Page.loadEventFired", 20000).catch(() => null);
  await cdp.send("Page.navigate", { url: args.url });
  await loaded;
  await new Promise((resolve) => setTimeout(resolve, args.loadwait * 1000));
}

if (args.text) {
  const expression = `(() => {
    const needle = ${JSON.stringify(args.text)};
    const walker = document.createTreeWalker(document.body,
      NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.nodeValue || !node.nodeValue.includes(needle)) continue;
      const element = node.parentElement;
      element?.scrollIntoView({block: 'center', inline: 'nearest'});
      window.scrollBy({top: ${Number(args.offset)}, left: 0, behavior: 'instant'});
      return {found: true, tag: element?.tagName || '', text: node.nodeValue.trim()};
    }
    return {found: false};
  })()`;
  const result = await cdp.send("Runtime.evaluate", {
    expression, returnByValue: true,
  });
  console.error(`[cdp-media] text=${JSON.stringify(result.result?.value)}`);
  await new Promise((resolve) => setTimeout(resolve, 1200));
}

const playResult = args.play ? await cdp.send("Runtime.evaluate", {
    expression: `(async () => Promise.all([...document.querySelectorAll('video')]
      .map(async (video, index) => {
        video.muted = ${args.mute ? "true" : "false"};
        try { await video.play(); return {index, ok: true}; }
        catch (error) { return {index, ok: false, error: String(error)}; }
      })))()`,
    awaitPromise: true,
  returnByValue: true,
}) : null;
const toneResult = args.tone !== 0 ? await cdp.send("Runtime.evaluate", {
  expression: `(async () => {
    if (globalThis.__macwsAudioProbe) {
      try { await globalThis.__macwsAudioProbe.context.close(); } catch {}
      delete globalThis.__macwsAudioProbe;
    }
    if (${Number(args.tone)} < 0) return {closed: true};
    const context = new AudioContext({sampleRate: 48000});
    const oscillator = new OscillatorNode(context, {frequency: 440});
    const gain = new GainNode(context, {gain: 0.04});
    oscillator.connect(gain).connect(context.destination);
    await context.resume();
    oscillator.start();
    oscillator.stop(context.currentTime + ${Number(args.tone)});
    globalThis.__macwsAudioProbe = {context, oscillator, gain};
    return {state: context.state, sampleRate: context.sampleRate,
      duration: ${Number(args.tone)}};
  })()`,
  awaitPromise: true,
  returnByValue: true,
  userGesture: true,
}) : null;
if (args.seek >= 0) {
  await cdp.send("Runtime.evaluate", {
    expression: `Promise.all([...document.querySelectorAll('video')].map(
      async (video) => {
        video.currentTime = Math.min(${Number(args.seek)},
          Number.isFinite(video.duration) ? Math.max(0, video.duration - 0.25) : ${Number(args.seek)});
        video.muted = true;
        try { await video.play(); } catch {}
      }))`,
    awaitPromise: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 500));
}

const sampleExpression = `(() => ({
  url: location.href,
  title: document.title,
  visibility: document.visibilityState,
  scrollY,
  viewport: {width: innerWidth, height: innerHeight, dpr: devicePixelRatio},
  codecSupport: (() => {
    const probe = document.createElement('video');
    return {
      h264: probe.canPlayType('video/mp4; codecs="avc1.640028"'),
      hevc: probe.canPlayType('video/mp4; codecs="hvc1.1.6.L123.B0"'),
      vp9: probe.canPlayType('video/webm; codecs="vp09.00.51.08"'),
      av1: probe.canPlayType('video/mp4; codecs="av01.0.08M.08"'),
      mseH264: MediaSource.isTypeSupported('video/mp4; codecs="avc1.64001F"'),
      mseHevc: MediaSource.isTypeSupported('video/mp4; codecs="hev1.1.6.L120.90"'),
      mseVp9: MediaSource.isTypeSupported('video/webm; codecs="vp09.00.51.08"'),
      mseAv1: MediaSource.isTypeSupported('video/mp4; codecs="av01.0.08M.08.0.110.01.01.01.0"'),
    };
  })(),
  bilibili: (() => {
    const play = globalThis.__playinfo__ || globalThis.__INITIAL_PLAYINFO__;
    const dashVideos = play?.data?.dash?.video || play?.dash?.video || [];
    return {
      hasPlayInfo: !!play,
      code: play?.code ?? null,
      message: play?.message ?? null,
      dashVideo: dashVideos.slice(0, 12).map((item) => ({
        id: item.id, codecid: item.codecid, codecs: item.codecs,
        mimeType: item.mimeType, width: item.width, height: item.height,
        host: (() => { try { return new URL(item.baseUrl || item.base_url).host; }
          catch { return ''; } })(),
      })),
      mediaResources: performance.getEntriesByType('resource')
        .filter((entry) => /playurl|\\.m4s|\\.mp4|api\\.bilibili/.test(entry.name))
        .slice(-30).map((entry) => ({
          name: entry.name.slice(0, 240),
          duration: entry.duration,
          transferSize: entry.transferSize,
          responseStatus: entry.responseStatus ?? null,
        })),
    };
  })(),
  canvases: [...document.querySelectorAll('canvas')].map((canvas) => ({
    width: canvas.width, height: canvas.height,
    cssWidth: canvas.clientWidth, cssHeight: canvas.clientHeight,
  })),
  videos: [...document.querySelectorAll('video')].map((video, index) => {
    const quality = video.getVideoPlaybackQuality?.();
    return {
      index, currentTime: video.currentTime, duration: video.duration,
      src: video.getAttribute('src'), currentSrc: video.currentSrc,
      sources: [...video.querySelectorAll('source')].map((source) => ({
        src: source.src, type: source.type,
      })),
      paused: video.paused, ended: video.ended, readyState: video.readyState,
      networkState: video.networkState, width: video.videoWidth,
      height: video.videoHeight, playbackRate: video.playbackRate,
      error: video.error ? {code: video.error.code, message: video.error.message} : null,
      totalFrames: quality?.totalVideoFrames ?? null,
      droppedFrames: quality?.droppedVideoFrames ?? null,
      corruptedFrames: quality?.corruptedVideoFrames ?? null,
    };
  }),
}))()`;

await mkdir(dirname(args.output), { recursive: true });
await mkdir(dirname(args.screenshots), { recursive: true });
const samples = [];
const frameIndexes = args.single
  ? new Set([0])
  : new Set([0, Math.floor(args.seconds), Math.floor(args.seconds * 2)]);
const iterations = args.single ? 1 : Math.floor(args.seconds * 2) + 1;
for (let index = 0; index < iterations; index++) {
  if (args.evaluate) {
    const evaluated = await cdp.send("Runtime.evaluate", {
      expression: sampleExpression,
      returnByValue: true,
    });
    samples.push({elapsedSeconds: index / 2, ...evaluated.result.value});
  } else {
    samples.push({elapsedSeconds: index / 2});
  }
  if (args.capture && frameIndexes.has(index)) {
    const shot = await cdp.send("Page.captureScreenshot", {
      format: "png", fromSurface: true, captureBeyondViewport: false,
    });
    await writeFile(`${args.screenshots}-${String(index).padStart(2, "0")}.png`,
                    Buffer.from(shot.data, "base64"));
  }
  if (index + 1 < iterations)
    await new Promise((resolve) => setTimeout(resolve, 500));
}

const output = {
  capturedAt: new Date().toISOString(),
  requestedURL: args.url || null,
  targetID: page.id,
  mediaDomainEnabled,
  playResult: playResult?.result?.value ?? null,
  toneResult: toneResult?.result?.value ?? null,
  diagnosticEvents,
  samples,
};
await writeFile(args.output, JSON.stringify(output, null, 2));
cdp.close();
console.error(`[cdp-media] samples=${samples.length} output=${args.output}`);
