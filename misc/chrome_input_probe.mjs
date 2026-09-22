// CDP observer for a controlled VNC mouse + keyboard test in Chromium.
//
// The page state is changed only by DOM input handlers.  CDP installs the
// controls and reads their state; it does not synthesize input.  This keeps a
// successful result attributable to the VNC/AppInput path under test.

import process from "node:process";

const host = process.argv[2] || "127.0.0.1";
const port = Number(process.argv[3] || 9223);
const expectedText = process.argv[4] || "vncinput150187";
const timeoutSeconds = Number(process.argv[5] || 25);

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

class CDP {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(url);
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id === undefined) return;
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
      else waiter.resolve(message.result);
    });
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  close() {
    this.socket.close();
  }
}

const targets = await fetchJson(`http://${host}:${port}/json/list`);
const page = targets.find((target) => target.type === "page" &&
  target.webSocketDebuggerUrl);
if (!page) throw new Error("no page target");
const cdp = new CDP(page.webSocketDebuggerUrl);
await cdp.open();
await cdp.send("Runtime.enable");
await cdp.send("Page.enable");
await cdp.send("Page.navigate", {
  url: "data:text/html,<title>MacWS%20VNC%20Input%20Probe</title><body></body>",
});
await new Promise((resolve) => setTimeout(resolve, 1000));

const setup = await cdp.send("Runtime.evaluate", {
  expression: `(() => {
    document.documentElement.innerHTML = \`<head><title>MacWS VNC Input Probe</title></head>
      <body>
        <button id="target">CLICK TARGET</button>
        <input id="field" autocomplete="off" spellcheck="false"
          placeholder="TYPE HERE">
        <pre id="status">clicks=0 text=</pre>
      </body>\`;
    const style = document.createElement("style");
    style.textContent = \`
      html, body { margin: 0; width: 100%; height: 100%; background: #18233a;
        color: white; font: 32px -apple-system, sans-serif; }
      #target { position: fixed; left: 160px; top: 180px; width: 360px;
        height: 160px; border: 8px solid #fff; border-radius: 24px;
        background: #1565ff; color: white; font: bold 34px sans-serif; }
      #field { position: fixed; left: 160px; top: 400px; width: 700px;
        height: 90px; box-sizing: border-box; border: 8px solid #fff;
        border-radius: 20px; padding: 12px; font: 34px monospace; }
      #status { position: fixed; left: 160px; top: 540px; margin: 0;
        color: #75ff9c; font: bold 34px monospace; }
    \`;
    document.head.appendChild(style);
    globalThis.macwsInputProbe = { clicks: 0, text: "" };
    const render = () => {
      document.querySelector("#status").textContent =
        \`clicks=\${globalThis.macwsInputProbe.clicks} text=\${globalThis.macwsInputProbe.text}\`;
    };
    document.querySelector("#target").addEventListener("click", () => {
      globalThis.macwsInputProbe.clicks++;
      document.querySelector("#target").textContent =
        \`CLICKED \${globalThis.macwsInputProbe.clicks}\`;
      render();
    });
    document.querySelector("#field").addEventListener("input", (event) => {
      globalThis.macwsInputProbe.text = event.target.value;
      render();
    });
    return { viewport: [innerWidth, innerHeight], devicePixelRatio };
  })()`,
  returnByValue: true,
});
if (setup.exceptionDetails) {
  throw new Error(`setup failed: ${JSON.stringify(setup.exceptionDetails)}`);
}
console.error(`[chrome-input-probe] setup ${JSON.stringify(setup.result.value)}`);

const deadline = Date.now() + timeoutSeconds * 1000;
let observed = { clicks: 0, text: "", active: "" };
while (Date.now() < deadline) {
  const response = await cdp.send("Runtime.evaluate", {
    expression: `(() => ({
      clicks: globalThis.macwsInputProbe?.clicks ?? -1,
      text: globalThis.macwsInputProbe?.text ?? "",
      active: document.activeElement?.id ?? ""
    }))()`,
    returnByValue: true,
  });
  observed = response.result.value;
  if (observed.clicks >= 1 && observed.text === expectedText) break;
  await new Promise((resolve) => setTimeout(resolve, 250));
}

console.log(JSON.stringify({
  browser: (await fetchJson(`http://${host}:${port}/json/version`)).Browser,
  expectedText,
  observed,
  success: observed.clicks >= 1 && observed.text === expectedText,
}, null, 2));
cdp.close();
if (!(observed.clicks >= 1 && observed.text === expectedText)) process.exitCode = 1;
