// Records the bot viewer (headless Chrome screencast) into JPEG frames.
// Usage: node scripts/v3-clip.js <seconds> <out-dir> [viewer-url]; then ffmpeg -framerate 15 -pattern_type glob -i "<out-dir>/*.jpg" clip.mp4
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const WebSocket = require("../mc/bot/node_modules/ws");

const [seconds, outDir, url = "http://127.0.0.1:3207"] = [Number(process.argv[2]), process.argv[3], process.argv[4]];
const debugPort = 9333;
fs.mkdirSync(outDir, { recursive: true });

const chrome = spawn("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", [
  "--headless=new", `--remote-debugging-port=${debugPort}`, "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
  "--window-size=1280,720", `--user-data-dir=/tmp/clip-chrome-profile`, "about:blank",
], { stdio: "ignore" });

const getJson = (path) => new Promise((resolve, reject) => {
  http.get({ host: "127.0.0.1", port: debugPort, path }, (res) => {
    let body = ""; res.on("data", (c) => (body += c)); res.on("end", () => resolve(JSON.parse(body)));
  }).on("error", reject);
});

async function main() {
  let targets;
  for (let i = 0; i < 50 && !targets; i++) {
    targets = await getJson("/json").catch(() => null);
    if (!targets) await new Promise((r) => setTimeout(r, 200));
  }
  const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r) => ws.on("open", r));
  let id = 0, frames = 0;
  const send = (method, params = {}) => ws.send(JSON.stringify({ id: ++id, method, params }));
  ws.on("message", (raw) => {
    const msg = JSON.parse(raw);
    if (msg.method !== "Page.screencastFrame") return;
    fs.writeFileSync(`${outDir}/f${String(frames++).padStart(5, "0")}.jpg`, Buffer.from(msg.params.data, "base64"));
    send("Page.screencastFrameAck", { sessionId: msg.params.sessionId });
  });
  send("Page.enable");
  send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
  send("Page.navigate", { url });
  await new Promise((r) => setTimeout(r, 5000));
  send("Page.startScreencast", { format: "jpeg", quality: 70, maxWidth: 1280, maxHeight: 720, everyNthFrame: 1 });
  await new Promise((r) => setTimeout(r, seconds * 1000));
  send("Page.stopScreencast");
  console.log(`frames: ${frames}`);
  ws.close(); chrome.kill(); process.exit(0);
}
main().catch((e) => { console.error(e); chrome.kill(); process.exit(1); });
