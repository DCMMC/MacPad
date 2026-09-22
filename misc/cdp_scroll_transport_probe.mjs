// Observe a real Chromium renderer while MacWS Host injects scroll records.
// CDP only installs/reads page state; it never synthesizes the input under
// test. This keeps delivery, delta units and momentum attributable to the
// Host -> AppInputBridge -> WindowServer path.

import process from "node:process";

const endpoint = process.argv[2] || "http://127.0.0.1:19223";
const observeMilliseconds = Number(process.argv[3] || 8000);

const targets = await (await fetch(`${endpoint}/json/list`, {
  headers: {Host: "127.0.0.1:9222"},
})).json();
const target = targets.find((item) => item.type === "page" &&
  !item.url.startsWith("vscode-file:") && item.webSocketDebuggerUrl) ||
  // The package's production benchmark uses an isolated /tmp profile. A cold
  // launch may expose only its workbench renderer; this probe immediately
  // replaces that disposable page with controlled content.
  targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
if (!target) throw new Error("no VSCode renderer target");

const socketURL = target.webSocketDebuggerUrl.replace(
  "ws://127.0.0.1:9222", endpoint.replace(/^http/, "ws"));
const socket = new WebSocket(socketURL);
let nextID = 1;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data));
  if (message.id === undefined) return;
  const waiter = pending.get(message.id);
  if (!waiter) return;
  pending.delete(message.id);
  message.error ? waiter.reject(new Error(JSON.stringify(message.error)))
                : waiter.resolve(message.result);
});
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, {once: true});
  socket.addEventListener("error", reject, {once: true});
});
function send(method, params = {}) {
  const id = nextID++;
  const result = new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
  });
  socket.send(JSON.stringify({id, method, params}));
  return result;
}

await send("Page.enable");
await send("Runtime.enable");
await send("Page.navigate", {
  url: "data:text/html,<title>MacWS%20Scroll%20Transport</title><body></body>",
});
await new Promise((resolve) => setTimeout(resolve, 800));
const setup = await send("Runtime.evaluate", {
  expression: `(() => {
    document.body.innerHTML = '<div id="readout"></div><main></main>';
    const style = document.createElement('style');
    style.textContent = \`html,body{margin:0;background:#152238;color:white}
      #readout{position:fixed;z-index:2;top:30px;left:30px;padding:20px;
        background:#000b;font:28px monospace;border-radius:14px}
      main{height:12000px;background:repeating-linear-gradient(
        #17345a 0 180px,#285b82 180px 360px)}\`;
    document.head.appendChild(style);
    globalThis.macwsScrollProbe = {events: [], startedAt: performance.now()};
    const render = () => {
      document.querySelector('#readout').textContent =
        'scrollY=' + Math.round(scrollY) + ' events=' +
        globalThis.macwsScrollProbe.events.length;
    };
    addEventListener('wheel', (event) => {
      globalThis.macwsScrollProbe.events.push({
        t: performance.now() - globalThis.macwsScrollProbe.startedAt,
        dx: event.deltaX, dy: event.deltaY, mode: event.deltaMode,
      });
      render();
    }, {passive: true});
    addEventListener('scroll', render, {passive: true});
    scrollTo(0, 4000);
    render();
    return {innerWidth, innerHeight, devicePixelRatio, scrollY};
  })()`,
  returnByValue: true,
});
console.log(`READY ${JSON.stringify(setup.result.value)}`);

await new Promise((resolve) => setTimeout(resolve, observeMilliseconds));
const observed = await send("Runtime.evaluate", {
  expression: `(() => ({
    scrollY,
    events: globalThis.macwsScrollProbe?.events || [],
  }))()`,
  returnByValue: true,
});
console.log(JSON.stringify(observed.result.value, null, 2));
socket.close();
