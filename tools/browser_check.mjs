// Checks the built page in headless Chrome over the DevTools protocol, and can take screenshots.
// Needs Node 22+ (built-in WebSocket) and Google Chrome. Serve docs/ first:
//   python3 -m http.server 8741 --bind 127.0.0.1 --directory docs
//   node tools/browser_check.mjs --url http://127.0.0.1:8741/ [--shots DIR] [--profile DIR]
// Uses its own throwaway Chrome profile (never your browser's). Exits 1 if a check fails.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const URL_ = opt("--url", "http://127.0.0.1:8741/");
const SHOTS = opt("--shots", null);
const PROFILE = opt("--profile", fs.mkdtempSync(path.join(os.tmpdir(), "eatsranked-chrome-")));
const CHROME = opt("--chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
const ORIGIN = new URL(URL_).origin;

fs.mkdirSync(PROFILE, { recursive: true });
try { fs.unlinkSync(path.join(PROFILE, "DevToolsActivePort")); } catch {}
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
  `--user-data-dir=${PROFILE}`, "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
  "--disable-extensions", "--disable-background-networking", "--disable-component-update", "about:blank"], { stdio: "ignore" });
const killer = setTimeout(() => { chrome.kill("SIGKILL"); console.error("timeout"); process.exit(2); }, 240000);

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function devtoolsPort() {
  for (let i = 0; i < 150; i++) {
    try { const t = fs.readFileSync(path.join(PROFILE, "DevToolsActivePort"), "utf8").trim().split("\n"); if (t.length >= 2) return t[0]; } catch {}
    await sleep(100);
  }
  throw new Error("Chrome did not start");
}
const port = await devtoolsPort();
const target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page");
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r, { once: true }));
let id = 0; const pending = new Map(); const waiters = []; let requests = []; let consoleErrors = [];
ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); return; }
  if (m.method === "Network.requestWillBeSent") requests.push(m.params.request.url);
  if (m.method === "Runtime.exceptionThrown") consoleErrors.push(m.params.exceptionDetails.text + " " + JSON.stringify(m.params.exceptionDetails.exception?.description || ""));
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") consoleErrors.push(m.params.args.map(a => a.value).join(" "));
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error") consoleErrors.push(m.params.entry.text + " " + (m.params.entry.url || ""));
  for (const w of waiters.filter(w => w.method === m.method)) { waiters.splice(waiters.indexOf(w), 1); w.res(m.params); }
});
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
const once = (method) => new Promise(res => waiters.push({ method, res }));
const js = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(expr.slice(0, 80) + " -> " + JSON.stringify(r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
};

await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable"); await send("Log.enable");
await send("Network.setCacheDisabled", { cacheDisabled: true });

let mobile = false;
async function open(width, height, { dark = false, still = false, nojs = false } = {}) {
  mobile = width < 600;
  await send("Emulation.setScriptExecutionDisabled", { value: nojs });
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await send("Emulation.setTouchEmulationEnabled", { enabled: mobile, maxTouchPoints: mobile ? 5 : 1 });
  await send("Emulation.setEmulatedMedia", { features: [
    { name: "prefers-color-scheme", value: dark ? "dark" : "light" },
    { name: "prefers-reduced-motion", value: still ? "reduce" : "no-preference" }] });
  requests = []; consoleErrors = [];
  const loaded = once("Page.loadEventFired");
  await send("Page.navigate", { url: URL_ });
  await loaded; await sleep(350);
}
async function fullHeight(width) {
  const h = await js("Math.ceil(document.documentElement.scrollHeight)");
  await send("Emulation.setDeviceMetricsOverride", { width, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await sleep(250);
  return h;
}
async function mouse(x, y) {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x - 12, y: y - 6 });
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await sleep(300);
}
async function tap(x, y) {
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(450);
}
async function key(k, shift = false) {
  const codes = { Tab: 9, Enter: 13, Escape: 27, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Home: 36, End: 35 };
  const base = { key: k, code: k, windowsVirtualKeyCode: codes[k], modifiers: shift ? 8 : 0 };
  await send("Input.dispatchKeyEvent", { type: "rawKeyDown", ...base });
  if (k === "Enter") await send("Input.dispatchKeyEvent", { type: "char", text: "\r", ...base });
  await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
  await sleep(120);
}
// centre of a state's interior point, in page coordinates
const statePoint = (abbr) => js(`(function(){var el=document.getElementById('st-${abbr}'),svg=el.ownerSVGElement,vb=svg.viewBox.baseVal,r=svg.getBoundingClientRect();
  return [r.left+(+el.dataset.x-vb.x)/vb.width*r.width, r.top+(+el.dataset.y-vb.y)/vb.height*r.height+window.scrollY];})()`);
const rectOf = (sel, pad = 0) => js(`(function(){var r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();
  return [Math.max(0,r.left-${pad}), Math.max(0,r.top+window.scrollY-${pad}), r.width+${2 * pad}, r.height+${2 * pad}];})()`);
async function shot(name, clip) {
  if (!SHOTS) return;
  await sleep(400);
  const w = await js("window.innerWidth");
  const h = await js("Math.ceil(document.documentElement.scrollHeight)");
  const c = clip || [0, 0, w, h];
  const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false,
    clip: { x: c[0], y: c[1], width: Math.min(c[2], w - c[0]), height: c[3], scale: 1 } });
  const out = path.join(SHOTS, name + ".png");
  fs.writeFileSync(out, Buffer.from(r.data, "base64"));
  console.log("shot", out);
}

const fails = [];
function check(label, ok, detail = "") {
  console.log(`${ok ? "OK " : "BAD"} ${label}${detail ? "  " + detail : ""}`);
  if (!ok) fails.push(label);
}

try {
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

  // ---------------------------------------------------------------- layout at every width
  for (const w of [360, 390, 414, 600, 768, 900, 1024, 1280, 1440, 1600]) {
    await open(w, 900);
    const m = await js("({sw:document.documentElement.scrollWidth, iw:window.innerWidth, cookie:document.cookie})");
    check(`no horizontal scroll at ${w}px`, m.sw <= m.iw, `scrollWidth=${m.sw}`);
    const ext = requests.filter(u => !u.startsWith(ORIGIN) && !u.startsWith("data:"));
    check(`no requests to other hosts at ${w}px`, ext.length === 0, ext.join(" "));
    check(`no cookies at ${w}px`, m.cookie === "");
    check(`no script errors at ${w}px`, consoleErrors.length === 0, consoleErrors.join(" | "));
    if (SHOTS && [360, 768, 1024, 1600].includes(w)) { await fullHeight(w); await shot(`layout-${w}-full`); }
  }
  const reqs = [...new Set(requests.map(u => u.replace(ORIGIN, "")))];
  console.log("    requests at 1600px:", reqs.join(" "));

  // ---------------------------------------------------------------- desktop interactions
  await open(1440, 900);
  await fullHeight(1440);
  if (SHOTS) await shot("desktop-1440-full");
  const [ix, iy] = await statePoint("IL");
  await mouse(ix, iy + 30);
  let st = await js("({open:!document.getElementById('pop-il').hidden, exp:document.getElementById('st-IL').getAttribute('aria-expanded')})");
  check("hovering Illinois opens its card", st.open && st.exp === "true");
  const pr = await js("(function(){var p=document.getElementById('pop-il').getBoundingClientRect(),m=document.querySelector('.map').getBoundingClientRect();return {l:p.left-m.left,t:p.top-m.top,r:m.right-p.right,b:m.bottom-p.bottom}})()");
  check("Illinois card sits inside the map area", pr.l >= -1 && pr.t >= -1 && pr.r >= -1, JSON.stringify(pr));
  if (SHOTS) await shot("desktop-1440-illinois", await rectOf("#states", 24));
  const openPop = () => js("(document.querySelector('.pop:not([hidden])') || {}).id || null");
  await mouse(5, 5); await sleep(450);
  st = await openPop();
  check("a card opened by pointing closes once the pointer leaves", st === null, String(st));
  await mouse(ix, iy + 30);
  const pc = await js("(function(){var r=document.getElementById('pop-il').getBoundingClientRect();return [r.left+r.width/2,r.top+window.scrollY+r.height/2]})()");
  await mouse((ix + pc[0]) / 2, iy + 30); await mouse(pc[0], pc[1]); await sleep(500);
  st = await openPop();
  check("...but stays open while the pointer is on the card", st === "pop-il", String(st));
  await mouse(5, 5); await sleep(450);
  check("...and closes when the pointer leaves the card", (await openPop()) === null);
  await mouse(ix, iy + 30);
  await key("Escape");
  st = await js("({open:(document.querySelector('.pop:not([hidden])') || {}).id || null, active:document.activeElement.id || document.activeElement.tagName})");
  check("Escape on a card opened by pointing closes it without moving focus into the map", st.open === null && !/^st-/.test(st.active), JSON.stringify(st));
  const [tx0, ty0] = await statePoint("TX");
  await mouse(tx0, ty0); await mouse(5, 5);
  st = await js("document.querySelector('.map .tip').classList.contains('show')");
  check("the hover label goes when the pointer leaves the map", st === false);
  const [wx, wy] = await statePoint("WI");
  await mouse(wx - 20, wy + 34);
  st = await js("({il:!document.getElementById('pop-il').hidden, wi:!document.getElementById('pop-wi').hidden})");
  check("hovering Wisconsin swaps to its card", st.wi && !st.il, JSON.stringify(st));
  if (SHOTS) await shot("desktop-1440-wisconsin", await rectOf("#atlas", 24));
  const [tx, ty] = await statePoint("TX");
  await mouse(tx, ty);
  st = await js("({tip:document.querySelector('.map .tip').textContent, show:document.querySelector('.map .tip').classList.contains('show')})");
  check("hovering Texas shows 'Coming soon'", st.show && st.tip === "Texas · Coming soon", JSON.stringify(st));
  // a clicked card stays open after the pointer leaves; a click outside closes it
  await mouse(ix, iy + 30);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: ix, y: iy + 30, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: ix, y: iy + 30, button: "left", clickCount: 1 });
  await mouse(5, 5); await sleep(600);
  check("a card opened by a click stays open after the pointer leaves", (await openPop()) === "pop-il");
  const [ox, oy] = await js("(function(){var r=document.querySelector('#states .h2').getBoundingClientRect();return [r.left+20,r.top+window.scrollY+20]})()");
  await mouse(ox, oy);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: ox, y: oy, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: ox, y: oy, button: "left", clickCount: 1 });
  await sleep(200);
  st = await js("!document.getElementById('pop-il').hidden");
  check("a click outside the card closes it", st === false);
  await mouse(tx, ty);
  if (SHOTS) await shot("desktop-1440-texas-hover", await rectOf("#atlas", 24));
  await mouse(5, 5);

  // every live state comes before the first coming-soon one (how many are live depends on site-data.json)
  st = await js("(function(){var s=[].slice.call(document.querySelectorAll('#atlas svg .st')),n=document.querySelectorAll('#atlas svg .st.live').length;return [n,s.slice(0,n).every(function(e){return e.classList.contains('live')}),s[n]?s[n].classList.contains('live'):false]})()");
  check("screen readers meet the live states before the coming-soon ones", st[0] >= 2 && st[1] === true && st[2] === false, JSON.stringify(st));
  st = await js("(function(){var h=document.querySelector('.hint'),v=[].filter.call(h.children,function(c){return getComputedStyle(c).display!=='none'});return {n:v.length,text:v.map(function(c){return c.textContent}).join('|'),op:getComputedStyle(h).opacity}})()");
  check("the map says how to use it without keyboard focus", st.n === 1 && /^Point to or tap a state/.test(st.text) && st.op === "1", JSON.stringify(st));

  // keyboard: tab into the map from the link before it
  await js("document.querySelector('#states .lede a').focus()");
  await key("Tab");
  st = await js("({id:document.activeElement.id, tip:document.querySelector('.map .tip').textContent, kbd:document.querySelector('.map').classList.contains('kbd'), ring:!!document.querySelector('.ring').getAttribute('d')})");
  check("Tab lands on the first live state", st.id === "st-IL" && st.kbd && st.ring, JSON.stringify(st));
  const liveWord = await js("document.querySelector('.map').dataset.live");
  check("focused live state is labelled", st.tip === `Illinois · ${liveWord}` && ["Live now", "On the web now"].includes(liveWord), st.tip);
  if (SHOTS) await shot("desktop-1440-keyboard-illinois", await rectOf("#atlas", 56));
  st = await js("[].filter.call(document.querySelectorAll('.hint span'),function(c){return getComputedStyle(c).display!=='none'}).map(function(c){return c.textContent}).join('|')");
  check("with keyboard focus the caption explains the keys", /^Arrow keys/.test(st), st);
  const pos = (idv) => js(`(function(){var e=document.getElementById('${idv}');return [+e.dataset.x,+e.dataset.y]})()`);
  const arrows = [];
  for (const [k, want] of [["ArrowUp", "st-WI"], ["ArrowDown", "st-IL"], ["ArrowRight", "st-IN"], ["ArrowLeft", "st-IL"]]) {
    await key(k); const got = await js("document.activeElement.id"); arrows.push(`${k}->${got}`);
    if (got !== want) { arrows.push(`(wanted ${want})`); break; }
  }
  check("arrow keys follow the map: Illinois Up is Wisconsin, Wisconsin Down is Illinois, Right is Indiana",
    !arrows.some(a => a.startsWith("(wanted")), arrows.join(" "));
  await key("ArrowLeft");
  const w1 = await js("document.activeElement.id");
  check("ArrowLeft moves to a western neighbour", ["st-IA", "st-MO"].includes(w1), w1);
  await key("ArrowDown");
  const s1 = await js("document.activeElement.id");
  const [p0, p1] = [await pos(w1), await pos(s1)];
  check("ArrowDown moves south", s1 !== w1 && p1[1] > p0[1], `${w1} -> ${s1}`);
  st = await js("({tip:document.querySelector('.map .tip').textContent, show:document.querySelector('.map .tip').classList.contains('show')})");
  check("a focused coming-soon state says so", st.show && / · Coming soon$/.test(st.tip), st.tip);
  if (SHOTS) await shot("desktop-1440-keyboard-arrow", await rectOf("#atlas", 56));
  await key("Tab", true);
  await key("Tab");
  st = await js("document.activeElement.id");
  check("the map is one tab stop that remembers the last state", st === s1, st);
  const firstLive = await js("document.querySelector('#atlas svg .st.live').id");   // st-CO once Colorado is live: the map reads west to east
  const firstPop = "pop-" + firstLive.slice(3).toLowerCase();
  await key("Home");
  st = await js("document.activeElement.id");
  check("Home jumps back to the first live state", st === firstLive, st);
  await key("Enter");
  st = await js(`({open:!document.getElementById('${firstPop}').hidden, focus:document.activeElement.id})`);
  check("Enter on the first live state opens its card and moves focus into it", st.open && st.focus === firstPop, JSON.stringify(st));
  await key("Tab");
  st = await js("document.activeElement.className + '|' + document.activeElement.getAttribute('href')");
  check("Tab from the card reaches its close button", /(^|\s)x(\s|\|)/.test(st), st);
  await key("Escape");
  st = await js(`({open:!document.getElementById('${firstPop}').hidden, focus:document.activeElement.id})`);
  check("Escape closes the card and returns focus to its state", !st.open && st.focus === firstLive, JSON.stringify(st));

  // chart: keyboard readout for every month equals the embedded data
  const bad = await js(`(function(){
    var D=JSON.parse(document.getElementById('chart-data').textContent), plot=document.getElementById('plot'), out=[];
    plot.focus();
    function k(key){plot.dispatchEvent(new KeyboardEvent('keydown',{key:key,bubbles:true}));}
    k('Home');
    for(var i=0;i<D.m.length;i++){
      var t=document.querySelector('.ctip').innerText.replace(/\\s+/g,' ').trim(), want;
      if(D.s[0].v[i]==null) want=D.m[i]+' Not published by BLS';
      else want=[D.m[i]].concat(D.s.map(function(s){return s.n+' '+s.v[i];})).join(' ');
      if(t!==want) out.push(D.m[i]+': '+t+' != '+want);
      k('ArrowRight');
    }
    plot.blur();
    return out;})()`);
  check("chart readout matches the data for every month", bad.length === 0, bad.slice(0, 3).join(" | "));
  const gapClear = `(function(){var zero=[].filter.call(document.querySelectorAll('.yax span'),function(s){return s.textContent==='0%'})[0],
    y0=zero.getBoundingClientRect().top+zero.getBoundingClientRect().height/2;
    return [].map.call(document.querySelectorAll('.gapnote'),function(n){var r=n.getBoundingClientRect();return Math.round((y0-r.bottom)*10)/10;});})()`;
  st = await js(gapClear);
  check("gap note sits clear of the 0% line (1440)", st.length > 0 && st.every(d => d >= 2), JSON.stringify(st));

  // dark mode, reduced motion
  await open(1440, 900, { dark: true, still: true });
  await fullHeight(1440);
  if (SHOTS) await shot("desktop-1440-dark-full");
  check("no script errors in dark mode", consoleErrors.length === 0, consoleErrors.join(" | "));

  // ---------------------------------------------------------------- tablet: the card opens below the map
  await open(768, 1024);
  await fullHeight(768);
  const [tix, tiy] = await statePoint("IL");
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: tix, y: tiy + 30 });
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: tix, y: tiy + 30, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: tix, y: tiy + 30, button: "left", clickCount: 1 });
  await sleep(300);
  st = await js(`(function(){var p=document.getElementById('pop-il').getBoundingClientRect(),h=document.querySelector('.hint').getBoundingClientRect(),l=document.querySelector('.legend').getBoundingClientRect();
    function hit(a,b){return a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;}
    return {open:!document.getElementById('pop-il').hidden, hintHitsCard:hit(h,p), legendHitsHint:hit(l,h)};})()`);
  check("768px: the card opens below the map without covering the caption or legend", st.open && !st.hintHitsCard && !st.legendHitsHint, JSON.stringify(st));
  if (SHOTS) await shot("tablet-768-illinois", await rectOf("#states", 0));

  // ---------------------------------------------------------------- phone
  await open(390, 844);
  await fullHeight(390);
  if (SHOTS) await shot("phone-390-full");
  const vis = await js("({plist:getComputedStyle(document.querySelector('.plist')).display, legend:getComputedStyle(document.querySelector('.legend')).position})");
  check("phone shows the compact list under the map", vis.plist === "block", JSON.stringify(vis));
  st = await js("[].filter.call(document.querySelectorAll('.hint span'),function(c){return getComputedStyle(c).display!=='none'}).map(function(c){return c.textContent}).join('|')");
  check("phone: the map caption points to the highlighted states and the list", /^Tap a highlighted state/.test(st), st);
  st = await js(`['IL','WI'].map(function(ab){var el=document.getElementById('st-'+ab),svg=el.ownerSVGElement,p=svg.createSVGPoint();p.x=+el.dataset.x;p.y=+el.dataset.y;var q=p.matrixTransform(svg.getScreenCTM()),
    r=document.querySelector('.pin[data-abbr='+ab+']').getBoundingClientRect(),s=el.getBoundingClientRect();
    return {ab:ab,w:r.width,off:Math.round(Math.hypot(r.left+r.width/2-q.x,r.top+r.height/2-q.y)*10)/10,share:Math.round(r.width*r.height/(s.width*s.height)*100)/100};})`);
  check("phone: small icons centred on their states, so the state colours show", st.every(p => p.w <= 22 && p.off <= 1 && p.share <= 0.4), JSON.stringify(st));
  const [pix, piy] = await statePoint("IL");
  await tap(pix, piy + 18);
  st = await js("({open:!document.getElementById('pop-il').hidden, pos:getComputedStyle(document.getElementById('pop-il')).position})");
  check("tapping Illinois on a phone opens its card below the map", st.open && st.pos === "relative", JSON.stringify(st));
  if (SHOTS) await shot("phone-390-illinois", await rectOf("#states", 0));
  const small = await js(`(function(){var out=[];document.querySelectorAll('.nav a, details.tbl summary, .foot ul a, .foot p a, .wordmark, .pop:not([hidden]) .x').forEach(function(el){
    if(getComputedStyle(el).display==='none')return;var r=el.getBoundingClientRect();if(r.width<44||r.height<44)out.push((el.textContent||el.getAttribute('aria-label')||'').trim().slice(0,24)+' '+Math.round(r.width)+'x'+Math.round(r.height));});return out;})()`);
  check("phone: header, table toggle, footer links and card close button are at least 44x44", small.length === 0, small.join(", "));
  await js("document.getElementById('pop-il').querySelector('.x').click()");
  const [ptx, pty] = await statePoint("TX");
  await tap(ptx, pty);
  st = await js("({tip:document.querySelector('.map .tip').textContent, show:document.querySelector('.map .tip').classList.contains('show')})");
  check("tapping Texas on a phone shows 'Coming soon'", st.show && st.tip === "Texas · Coming soon", JSON.stringify(st));
  if (SHOTS) await shot("phone-390-texas-tap", await rectOf("#atlas", 0));
  await sleep(1900);
  await js("document.querySelector('#states .lede a').focus()");
  await key("Tab");
  st = await js("({id:document.activeElement.id, tip:document.querySelector('.map .tip').textContent})");
  check("phone: Tab lands on the map (the state last touched)", st.id === "st-TX" && st.tip === "Texas · Coming soon", JSON.stringify(st));
  await key("ArrowRight");
  if (SHOTS) await shot("phone-390-keyboard", await rectOf("#atlas", 0));
  await js("document.activeElement.blur()");
  // the chart readout is a strip above the plot, never over the lines
  await js("document.getElementById('plot').scrollIntoView({block:'center'})"); await sleep(200);
  const pl = await js("(function(){var r=document.getElementById('plot').getBoundingClientRect();return [r.left,r.top,r.width,r.height]})()");
  for (const fr of [0.3, 0.72]) {
    const cx = pl[0] + pl[2] * fr, cy = pl[1] + pl[3] * 0.5;
    await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: cx, y: cy }] });
    await send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: cx + 3, y: cy }] });
    await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] }); await sleep(200);
    st = await js("(function(){var c=document.querySelector('.ctip').getBoundingClientRect(),p=document.getElementById('plot').getBoundingClientRect();return {gap:Math.round(p.top-c.bottom),h:Math.round(c.height),text:document.querySelector('.ctip').innerText.replace(/\\s+/g,' ')}})()");
    check(`phone: chart readout (${Math.round(fr * 100)}% across) sits above the plot`, st.gap >= 0 && st.h > 20 && /Restaurants .*Groceries .*All prices/.test(st.text), JSON.stringify(st));
  }
  if (SHOTS) await shot("phone-390-chart-touch", await rectOf(".chart", 8));
  await js("document.getElementById('plot').blur()");
  await tap(10, 10);
  st = await js(gapClear);
  check("gap note sits clear of the 0% line (390)", st.length > 0 && st.every(d => d >= 2), JSON.stringify(st));
  await js("document.querySelector('details.tbl').open = true");
  if (SHOTS) await shot("phone-390-table", await rectOf("details.tbl", 8));
  const tw = await js("({sw:document.documentElement.scrollWidth, iw:window.innerWidth})");
  check("phone: the open table scrolls inside its box, not the page", tw.sw <= tw.iw, JSON.stringify(tw));
  await js("document.querySelector('details.tbl').open = false");
  const pk = await js("document.querySelector('.pick[data-abbr=WI]').getBoundingClientRect().top + window.scrollY + 20");
  await tap(100, pk);
  st = await js("({open:!document.getElementById('pop-wi').hidden})");
  check("tapping Wisconsin in the list opens its card", st.open);

  // ---------------------------------------------------------------- without JavaScript
  await open(1440, 900, { nojs: true });
  await fullHeight(1440);
  st = await js("({pops:getComputedStyle(document.querySelector('.pops')).display, il:document.getElementById('st-IL').getAttribute('href'), sw:document.documentElement.scrollWidth})");
  check("without JavaScript: no pop-ups, live states link to their cards, no sideways scroll",
    st.pops === "none" && st.il === "#app-il" && st.sw <= 1440, JSON.stringify(st));
  if (SHOTS) await shot("nojs-1440-map", await rectOf("#states", 0));
} catch (e) {
  console.error(e);
  fails.push("exception: " + e.message);
} finally {
  clearTimeout(killer);
  try { await send("Browser.close"); } catch {}
  setTimeout(() => { chrome.kill("SIGKILL"); console.log(fails.length ? `\nFAILED (${fails.length}): ${fails.join("; ")}` : "\nALL BROWSER CHECKS PASS"); process.exit(fails.length ? 1 : 0); }, 300);
}
