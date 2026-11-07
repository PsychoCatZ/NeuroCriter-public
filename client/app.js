/* Недоделок: клиент PWA. Весь динамический текст выводится через textContent. */
(() => {
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* без хранилища тоже работаем */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ничего */ } },
};

let token = store.get("nc_token") || "";
let view = null;
let fx = null;                  // короткий эффект над зверьком
let jumpAt = 0;
let busy = false;

// ------------------------------------------------------------------ сеть
function banner(text) {
  const b = $("#banner");
  b.hidden = !text;
  b.textContent = text || "";
}

async function api(path, opts = {}) {
  let resp;
  try {
    resp = await fetch(path, {
      method: opts.method || "GET",
      headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    banner("Нет связи с сервером. Зверёк без вас живёт дальше.");
    throw e;
  }
  if (resp.status === 401) { showLogin(); throw new Error("401"); }
  let data = {};
  try { data = await resp.json(); } catch (e) { /* пустой ответ */ }
  if (!resp.ok) {
    banner(data.error || ("Ошибка " + resp.status));
    throw new Error(data.error || String(resp.status));
  }
  banner("");
  return data;
}

// ------------------------------------------------------------------ DOM
function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids) node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return node;
}

function show(id, on = true) { $("#" + id).hidden = !on; }
function closeSheets() {
  for (const id of ["food-sheet", "say-sheet", "screen"]) show(id, false);
}

function vibrate(pattern) {
  try { if (navigator.vibrate) navigator.vibrate(pattern); } catch (e) { /* нет вибрации */ }
}

// ------------------------------------------------------------------ вход и рождение
function showLogin() {
  show("hatch", false);
  show("login", true);
  $("#token-input").focus();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  token = $("#token-input").value.trim();
  try {
    await refresh();
    store.set("nc_token", token);
    show("login", false);
  } catch (err) {
    const box = $("#login-error");
    box.hidden = false;
    box.textContent = "Токен не подошёл.";
  }
});

$("#hatch-btn").addEventListener("click", async () => {
  const tz = -new Date().getTimezoneOffset();
  try {
    await api("/api/hatch", { method: "POST", body: { tz_offset_min: tz } });
    show("hatch", false);
    await refresh();
    openNameScreen();
  } catch (e) { /* баннер уже показан */ }
});

// ------------------------------------------------------------------ состояние
const STAGE_WORDS = ["", "звуки", "слова", "фразы", "речь"];
const BAND_CHIPS = [
  ["hunger", 2, "🍖 хочет есть"], ["hunger", 3, "🍖 очень голоден"],
  ["tiredness", 2, "😴 хочет спать"], ["dirt", 2, "🫧 грязный"],
  ["boredom", 2, "🥱 скучает"], ["loneliness", 2, "💔 одиноко"],
  ["walk", 2, "🚪 просится гулять"],
];

async function refresh() {
  const data = await api("/api/state");
  if (!data.hatched) { view = null; show("hatch", true); return; }
  show("hatch", false);
  view = data;
  render();
}

// ключевые шкалы: good = какое значение хорошее (низкое или высокое), по нему выбирается цвет
const METERS = [
  { key: "hunger", label: "Голод", good: "low" },
  { key: "mood", label: "Настроение", good: "high" },
  { key: "tiredness", label: "Усталость", good: "low" },
  { key: "health", label: "Здоровье", good: "high" },
  { key: "boredom", label: "Скука", good: "low" },
];

const METER_KEYS = new Set(["hunger", "tiredness", "boredom"]);

function meterTone(value, good) {
  if (good === "low") return value < 40 ? "ok" : value < 70 ? "warn" : "bad";
  return value >= 60 ? "ok" : value >= 35 ? "warn" : "bad";
}

function metersEnabled() { return store.get("nc_meters") !== "off"; }

function renderMeters(v) {
  const wrap = $("#meters-wrap");
  const box = $("#meters");
  wrap.hidden = !v.meters;
  if (wrap.hidden) return;
  const open = metersEnabled();
  box.hidden = !open;
  const toggle = $("#meters-toggle");
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", open ? "Скрыть шкалы потребностей" : "Показать шкалы потребностей");
  // свёрнутая панель: пять точек в цвете шкал, чтобы состояние было видно с одного взгляда
  const dots = $("#meters-dots");
  dots.replaceChildren(...METERS.map((m) => {
    const tone = meterTone(v.meters[m.key], m.good);
    return el("i", { class: tone === "ok" ? "" : tone, title: `${m.label}: ${v.meters[m.key]}` });
  }));
  if (!open) return;
  if (!box.children.length) {                      // строим один раз, потом только обновляем
    for (const m of METERS) {
      box.append(el("div", { class: "meter", "data-key": m.key },
        el("span", { class: "name" }, m.label),
        el("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" },
          el("div", { class: "fill" })),
        el("span", { class: "val" }, "")));
    }
  }
  for (const m of METERS) {
    const value = v.meters[m.key];
    const row = box.querySelector(`[data-key="${m.key}"]`);
    const bar = row.querySelector(".bar");
    const fill = row.querySelector(".fill");
    fill.style.width = Math.max(0, Math.min(100, value)) + "%";
    fill.className = "fill " + (meterTone(value, m.good) === "ok" ? "" : meterTone(value, m.good));
    row.querySelector(".val").textContent = String(value);
    bar.setAttribute("aria-valuenow", String(value));
    bar.setAttribute("aria-label", `${m.label}: ${value} из 100`);
  }
}

function render() {
  const v = view;
  renderMeters(v);
  $("#pet-title").textContent = v.name || v.id;
  const hh = String(v.clock.hour).padStart(2, "0");
  const mm = String(v.clock.minute).padStart(2, "0");
  $("#pet-meta").textContent = `${v.name ? v.id + " · " : ""}день ${v.day} · ${hh}:${mm} · ${STAGE_WORDS[v.stage]}`;

  const chips = $("#chips");
  chips.replaceChildren();
  const s = v.state;
  const add = (t) => chips.append(el("li", {}, t));
  if (s.mode === "hibernating") add("❄️ в карантине лаборатории");
  if (s.sleeping) add("💤 спит");
  if (s.illness) add("🤒 болеет: " + s.illness);
  if (s.sulking) add("😠 обижен");
  const metersOn = metersEnabled() && !!v.meters;
  for (const [key, min, text] of BAND_CHIPS) {
    if (metersOn && METER_KEYS.has(key)) continue;          // это уже видно на шкалах
    if (s.sleeping && key === "tiredness") continue;        // во сне «хочет спать» лишнее
    const show = key === "hunger" ? s[key] === min : s[key] >= min;
    if (show) add(text);
  }
  if (v.brain === "stub") add("🧠 мозг выключен: отвечает по шаблонам");
  if (s.tired_budget) add("💬 устал разговаривать");
  if (v.form.favorite) add("⭐ любит: " + v.form.favorite);

  const box = $("#speech");
  box.replaceChildren();
  for (const line of v.speech.slice(-4)) {
    const cls = line.kind === "event" ? "bubble event" : line.kind === "owner" ? "bubble owner" : "bubble";
    const b = el("div", { class: cls }, line.say || "…");
    if (line.kind === "reaction" && line.tone) b.append(el("small", {}, line.tone));
    box.append(b);
  }

  for (const btn of document.querySelectorAll("#actions button")) {
    const a = btn.dataset.act;
    btn.disabled = busy || !v.can[a] || (a === "sleep" && s.sleeping);
  }
}

// ------------------------------------------------------------------ действия
async function doAct(action, extra = {}) {
  if (busy) return;
  busy = true;
  render();
  try {
    const res = await api("/api/act", { method: "POST", body: { action, ...extra } });
    view = res.state;
    jumpAt = performance.now();
    const ok = res.outcome === "accept";
    fx = { text: ok ? "♥" : res.outcome.startsWith("refuse") ? "!" : "…", until: performance.now() + 1400 };
    vibrate(ok ? [30] : res.outcome.startsWith("refuse") ? [60, 40, 60] : [15]);
  } catch (e) { /* баннер показан */ }
  busy = false;
  if (view) render();
}

for (const btn of document.querySelectorAll("#actions button")) {
  btn.addEventListener("click", () => {
    const a = btn.dataset.act;
    if (a === "feed") openFood();
    else if (a === "say") { show("say-sheet", true); $("#say-input").focus(); }
    else doAct(a);
  });
}

function openFood() {
  const list = $("#food-list");
  list.replaceChildren();
  for (const food of view.foods) {
    const fav = view.form.favorite === food;
    list.append(el("button", {
      class: "secondary food-btn",
      onclick: () => { closeSheets(); doAct("feed", { food }); },
    }, el("span", {}, food), el("span", {}, fav ? "⭐ любимое" : "")));
  }
  show("food-sheet", true);
}

$("#say-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#say-input").value.trim();
  if (!text) return;
  $("#say-input").value = "";
  closeSheets();
  doAct("say", { text });
});

for (const btn of document.querySelectorAll("[data-close]")) btn.addEventListener("click", closeSheets);

$("#meters-toggle").addEventListener("click", () => {
  store.set("nc_meters", metersEnabled() ? "off" : "on");
  if (view) render();
});

// ------------------------------------------------------------------ экраны
function openScreen(title, build) {
  $("#screen-title").textContent = title;
  const body = $("#screen-body");
  body.replaceChildren();
  build(body);
  show("screen", true);
}

function kv(parent, rows) {
  const g = el("div", { class: "kv" });
  for (const [k, v] of rows) g.append(el("span", {}, k), el("span", {}, v));
  parent.append(g);
}

const NEED_NAMES = { hunger: "голод", tiredness: "усталость", dirt: "грязь",
  boredom: "скука", loneliness: "одиночество", walk_need: "тяга к прогулке" };
const REL_NAMES = { trust: "доверие", attachment: "привязанность", grudge: "обида", respect: "уважение" };
const TRAIT_NAMES = { insolence: "наглость", curiosity: "любопытство", vengefulness: "злопамятность",
  greed: "жадность", laziness: "лень", anxiety: "тревожность", clinginess: "привязчивость" };

async function openInspect() {
  const d = await api("/api/inspect");
  openScreen("Осмотр", (b) => {
    b.append(el("p", { class: "note" }, "Зверёк может драматизировать. Здесь настоящие числа."));
    b.append(el("h3", {}, "Потребности (0 спокойно, 100 плохо)"));
    kv(b, Object.entries(d.needs).map(([k, v]) => [NEED_NAMES[k] || k, v]));
    kv(b, [["здоровье", d.health], ["настроение", d.mood]]);
    b.append(el("h3", {}, "Отношения"));
    kv(b, Object.entries(d.relationship).map(([k, v]) => [REL_NAMES[k] || k, v]));
    b.append(el("h3", {}, "Характер"));
    kv(b, Object.entries(d.traits).map(([k, v]) => [TRAIT_NAMES[k] || k, v]));
    b.append(el("p", { class: "note" }, `Очки заботы: ${d.care_points}`));
  });
}

async function openDossier() {
  const d = await api("/api/dossier");
  openScreen("Досье", (b) => {
    b.append(el("h3", {}, "Кто он"));
    b.append(el("p", {}, d.narrative || "Пока он сам не знает."));
    if (d.mutations.length) {
      b.append(el("h3", {}, "Как он изменился"));
      const ul = el("ul", { class: "list" });
      for (const m of d.mutations) ul.append(el("li", {}, `день ${m.day}: ${m.text}`));
      b.append(ul);
    }
    b.append(el("h3", {}, "Что он знает о лаборанте"));
    if (d.facts.length) {
      const ul = el("ul", { class: "list" });
      for (const f of d.facts) ul.append(el("li", {}, f));
      b.append(ul);
    } else b.append(el("p", { class: "note" }, "Пока ничего."));
    b.append(el("h3", {}, "Что запомнилось"));
    if (d.memories.length) {
      const ul = el("ul", { class: "list" });
      for (const m of d.memories) ul.append(el("li", {}, `день ${m.day}: ${m.summary}`));
      b.append(ul);
    } else b.append(el("p", { class: "note" }, "Пока ничего."));
  });
}

async function openDiary() {
  const d = await api("/api/diary");
  openScreen("Дневник и сны", (b) => {
    b.append(el("h3", {}, "Дневник"));
    if (d.diary.length) {
      const ul = el("ul", { class: "list" });
      for (const x of d.diary.slice().reverse()) ul.append(el("li", {}, `день ${x.day}: ${x.text}`));
      b.append(ul);
    } else b.append(el("p", { class: "note" }, "Пока пусто: записи появляются по ночам."));
    b.append(el("h3", {}, "Сны"));
    if (d.dreams.length) {
      const ul = el("ul", { class: "list" });
      for (const x of d.dreams.slice().reverse()) ul.append(el("li", {}, `день ${x.day}: ${x.text}`));
      b.append(ul);
    } else b.append(el("p", { class: "note" }, "Пока пусто."));
  });
}

function openNameScreen() {
  openScreen("Как назовёшь?", (b) => {
    b.append(el("p", {}, "Пока он откликается только на номер. Имя он будет осваивать постепенно."));
    const input = el("input", { maxlength: "24", placeholder: "Имя" });
    b.append(input);
    b.append(el("button", {
      class: "primary",
      onclick: async () => {
        if (!input.value.trim()) return;
        try { await api("/api/name", { method: "POST", body: { name: input.value } }); closeSheets(); await refresh(); }
        catch (e) { /* баннер */ }
      },
    }, "Дать имя"));
  });
}

// ---- уведомления (Web Push)
function b64ToBytes(b64url) {
  const pad = "=".repeat((4 - (b64url.length % 4)) % 4);
  const raw = atob((b64url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (ch) => ch.charCodeAt(0));
}

async function currentSubscription() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return { supported: false };
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = reg ? await reg.pushManager.getSubscription() : null;
  return { supported: true, sub };
}

async function enablePush() {
  const existing = await navigator.serviceWorker.getRegistration();
  if (!existing) throw new Error("Service worker не запущен. Нужен HTTPS и установленное приложение.");
  const reg = await navigator.serviceWorker.ready;
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Разрешение на уведомления не выдано.");
  const { key } = await api("/api/push/key");
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true, applicationServerKey: b64ToBytes(key),
  });
  await api("/api/push/subscribe", { method: "POST", body: sub.toJSON() });
}

async function disablePush() {
  const { sub } = await currentSubscription();
  if (sub) {
    await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } });
    await sub.unsubscribe();
  }
}

function pushSection(b, s) {
  b.append(el("h3", {}, "Уведомления"));
  const status = el("p", { class: "note" }, "Проверяю…");
  const row = el("div", { class: "row" });
  b.append(status, row);
  if (!s.push.configured) { status.textContent = "На сервере уведомления не настроены."; return; }
  const refreshUi = async () => {
    row.replaceChildren();
    const st = await currentSubscription();
    if (!st.supported) { status.textContent = "Этот браузер не поддерживает уведомления. Установи приложение на экран телефона."; return; }
    if (st.sub) {
      status.textContent = "Включены. Зверёк напишет, если проголодается или заболеет, пока вас нет.";
      row.append(
        el("button", { class: "secondary", onclick: async () => {
          try { const r = await api("/api/push/test", { method: "POST" });
                status.textContent = r.sent ? "Проверочное уведомление отправлено." : "Не отправлено: " + r.skipped; }
          catch (e) { /* баннер */ } } }, "Проверить"),
        el("button", { class: "secondary", onclick: async () => {
          try { await disablePush(); await refreshUi(); } catch (e) { /* баннер */ } } }, "Выключить"));
    } else {
      status.textContent = "Выключены. На часы уведомление придёт вместе с телефоном.";
      row.append(el("button", { class: "primary", onclick: async () => {
        try { await enablePush(); await refreshUi(); }
        catch (e) { status.textContent = e.message || "Не получилось включить."; } } }, "Включить"));
    }
  };
  refreshUi().catch(() => { status.textContent = "Не удалось проверить состояние."; });

  b.append(el("label", { for: "pings" }, "Не больше уведомлений в сутки"));
  const pings = el("select", { id: "pings" });
  for (let n = 1; n <= 12; n++) pings.append(el("option", { value: String(n) }, String(n)));
  pings.value = String(s.pings_per_day);
  pings.addEventListener("change", () => {
    api("/api/settings", { method: "POST", body: { pings_per_day: +pings.value } }).catch(() => {});
  });
  b.append(pings);
}

async function openSettings() {
  const s = await api("/api/settings");
  openScreen("Настройки", (b) => {
    b.append(el("h3", {}, "Имя"));
    const name = el("input", { maxlength: "24", placeholder: "Имя", value: (view && view.name) || "" });
    b.append(name);
    b.append(el("button", { class: "secondary", onclick: async () => {
      if (!name.value.trim()) return;
      try { await api("/api/name", { method: "POST", body: { name: name.value } }); await refresh(); closeSheets(); }
      catch (e) { /* баннер */ }
    } }, "Сохранить имя"));

    b.append(el("h3", {}, "Тихие часы"));
    const hours = () => {
      const sel = el("select", {});
      for (let h = 0; h < 24; h++) sel.append(el("option", { value: String(h) }, String(h).padStart(2, "0") + ":00"));
      return sel;
    };
    const from = hours(); from.value = String(s.quiet_start);
    const to = hours(); to.value = String(s.quiet_end);
    b.append(el("div", { class: "row" }, from, to));
    b.append(el("button", { class: "secondary", onclick: async () => {
      try { await api("/api/settings", { method: "POST", body: { quiet_start: +from.value, quiet_end: +to.value } }); closeSheets(); }
      catch (e) { /* баннер */ }
    } }, "Сохранить часы"));
    b.append(el("p", { class: "note" }, "В тихие часы уведомления не приходят."));
    pushSection(b, s);

    b.append(el("h3", {}, "Главный экран"));
    const meters = el("select", {},
      el("option", { value: "on" }, "показывать шкалы потребностей"),
      el("option", { value: "off" }, "скрыть шкалы"));
    meters.value = metersEnabled() ? "on" : "off";
    meters.addEventListener("change", () => { store.set("nc_meters", meters.value); if (view) render(); });
    b.append(meters);

    b.append(el("h3", {}, "Тема"));
    const theme = el("select", {},
      el("option", { value: "auto" }, "как в системе"),
      el("option", { value: "light" }, "светлая"),
      el("option", { value: "dark" }, "тёмная"));
    theme.value = store.get("nc_theme") || "auto";
    theme.addEventListener("change", () => { store.set("nc_theme", theme.value); applyTheme(); });
    b.append(theme);

    b.append(el("h3", {}, "Служебное"));
    kv(b, [["мозг", { openai: "OpenAI", claude: "Claude", stub: "заглушка" }[s.brain] || s.brain],
           ["часовой пояс", `UTC${s.tz_offset_min >= 0 ? "+" : "-"}${Math.abs(s.tz_offset_min / 60)}`]]);
    b.append(el("button", { class: "secondary", onclick: () => {
      store.del("nc_token"); token = ""; closeSheets(); showLogin();
    } }, "Выйти"));
  });
}

for (const btn of document.querySelectorAll("#nav button")) {
  btn.addEventListener("click", () => {
    const which = btn.dataset.screen;
    const fn = { dossier: openDossier, diary: openDiary, inspect: openInspect, settings: openSettings }[which];
    fn().catch(() => { /* баннер уже показан */ });
  });
}

function applyTheme() {
  const t = store.get("nc_theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}

// ------------------------------------------------------------------ рисование
const canvas = $("#stage");
const ctx = canvas.getContext("2d");
ctx.imageSmoothingEnabled = false;

function rect(x, y, w, h, c) {
  ctx.fillStyle = c;
  ctx.fillRect(Math.round(x), Math.round(y), Math.max(0, Math.round(w)), Math.max(0, Math.round(h)));
}
function ellipse(cx, cy, rx, ry, c) {
  for (let y = -ry; y <= ry; y++) {
    const w = Math.round(rx * Math.sqrt(Math.max(0, 1 - (y * y) / (ry * ry))));
    rect(cx - w, cy + y, 2 * w, 1, c);
  }
}
function hashColor(text) {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `hsl(${h % 360} 55% 60%)`;
}

const FOOD_TINT = { "суп": "#e6c86e", "рыба": "#9cc6e6", "сосиски": "#e69a9a", "каша": "#e6d6b0", "печенье": "#c99a6b" };

function palette(hour) {
  const night = hour < 6 || hour >= 22;
  const evening = hour >= 19 && hour < 22;
  return {
    night, evening,
    wall: night ? "#2b2a3f" : evening ? "#d9b9a0" : "#e8dcc8",
    floor: night ? "#3a2f35" : "#b08968",
    sky: night ? "#141a33" : evening ? "#f2a56b" : "#8ec5ff",
  };
}

function drawRoom(p, t) {
  rect(0, 0, 160, 84, p.wall);
  rect(0, 84, 160, 36, p.floor);
  rect(0, 83, 160, 1, "rgba(0,0,0,.25)");
  // окно
  rect(12, 12, 34, 30, "#5c4a3a");
  rect(14, 14, 30, 26, p.sky);
  rect(28, 14, 2, 26, "#5c4a3a");
  rect(14, 26, 30, 2, "#5c4a3a");
  if (p.night) { rect(19, 18, 2, 2, "#fff"); rect(36, 31, 2, 2, "#fff"); rect(22, 33, 1, 1, "#fff");
    ellipse(37, 20, 3, 3, "#f5f0c8"); }
  // дверь
  rect(120, 22, 24, 62, "#6d4c3d");
  rect(122, 24, 20, 58, "#7d5a48");
  rect(137, 54, 3, 3, "#e0c070");
}

function drawProps(p, v) {
  // миска
  ellipse(30, 104, 11, 4, "#4a6fa5");
  ellipse(30, 103, 9, 3, v.state.hunger >= 2 ? "#2f4468" : (FOOD_TINT[v.form.favorite] || "#d9a85b"));
  // подстилка
  ellipse(126, 104, 15, 5, "#8a6bb5");
  ellipse(126, 103, 12, 3, "#a68ad0");
  // мячик и клубок
  ellipse(146, 111, 3, 3, "#d94f4f");
  ellipse(105, 112, 3, 3, "#7b5fd0"); rect(103, 112, 5, 1, "#c8b6ff");
  // коробка
  rect(52, 104, 14, 10, "#b98c5a"); rect(52, 104, 14, 2, "#8c6a3f");
  // найденные вещи
  v.items.forEach((name, i) => rect(70 + i * 7, 114, 4, 3, hashColor(name)));
}

function drawCreature(v, now) {
  const s = v.state;
  const mut = new Set(v.form.mutations);
  const pose = s.pose;
  const hibernate = pose === "hibernate";
  const sleeping = pose === "sleep" || hibernate;
  const turned = pose === "sulk";
  const cx = turned ? 92 : 80;
  let bob = sleeping ? 0 : Math.sin(now / 520) * (pose === "happy" ? 2 : 1);
  const jump = now - jumpAt;
  if (jump < 500) bob -= Math.abs(Math.sin(jump / 500 * Math.PI)) * 9;
  const belly = mut.has("round_belly");
  const rx = belly ? 29 : 26;
  const ry = (belly ? 22 : 19) - (sleeping ? 4 : 0);
  const cy = 80 + bob + (sleeping ? 3 : 0);

  let tint = "#c7b3e6";
  for (const m of mut) if (m.startsWith("color_")) tint = FOOD_TINT[m.slice(6)] || tint;
  if (s.illness) tint = "#a8c98a";
  const dark = "rgba(60,40,90,.35)";

  // тень
  ellipse(cx, 106, rx - 2, 3, "rgba(0,0,0,.25)");

  // лапы: три, с лишней пять
  const legs = mut.has("extra_leg") ? [-20, -10, 0, 10, 20] : [-14, 0, 14];
  for (const dx of legs) { rect(cx + dx - 2, cy + ry - 2, 5, 106 - (cy + ry) + 1, "#9d86c4"); rect(cx + dx - 3, 105, 7, 2, "#7d68a8"); }

  // хвост
  const tx = cx + rx - 2;
  rect(tx, cy + 6, 6, 3, tint); rect(tx + 5, cy + 1, 3, 7, tint); rect(tx + 4, cy - 6, 3, 8, tint);
  if (mut.has("tail_fluffy")) ellipse(tx + 6, cy - 8, 5, 5, "#f3e6ff");
  else if (mut.has("tail_spiky")) { rect(tx + 2, cy - 10, 2, 4, "#c0392b"); rect(tx + 6, cy - 11, 2, 5, "#c0392b"); rect(tx + 9, cy - 9, 2, 3, "#c0392b"); }
  else ellipse(tx + 6, cy - 8, 3, 3, "#8e6fc9");

  // тело
  ellipse(cx, cy, rx, ry, tint);
  ellipse(cx, cy + ry / 2, rx - 6, Math.max(2, ry / 2 - 1), "rgba(255,255,255,.18)");

  // ухо-антенна
  const big = mut.has("big_ear");
  const ex = cx - 14, ey = cy - ry;
  const stalk = big ? 20 : 12;
  const twitch = Math.sin(now / 700) > 0.92 ? 2 : 0;
  rect(ex, ey - stalk, 2, stalk + 2, "#7d68a8");
  ellipse(ex + 1 + twitch, ey - stalk - 3, big ? 11 : 7, big ? 6 : 4, "#e7d9ff");
  ellipse(ex + 1 + twitch, ey - stalk - 3, big ? 7 : 4, big ? 3 : 2, "#9d86c4");
  if (mut.has("find_jewelry")) { rect(ex + 6, ey - stalk - 6, 3, 3, "#53d8d8"); rect(ex + 7, ey - stalk - 8, 1, 1, "#fff"); }

  if (turned) {
    // спиной: видны шов на спине, штамп и хвост
    for (let i = -16; i <= 16; i += 6) rect(cx + i, cy - 2, 2, 5, dark);
  } else {
    drawFace(v, cx, cy, ry, now, sleeping, hibernate);
  }

  // штампы
  rect(cx + 4, cy + 6, 14, 8, "#c0392b"); rect(cx + 5, cy + 7, 12, 6, "#e6665a");
  ctx.fillStyle = "#fff"; ctx.font = "6px monospace"; ctx.fillText("НП", cx + 7, cy + 12.5);
  if (mut.has("stamp_accepted")) { rect(cx - 20, cy + 6, 14, 8, "#2e8b57"); ctx.fillStyle = "#fff"; ctx.fillText("ОК", cx - 17, cy + 12.5); }
  if (mut.has("patch")) { rect(cx - 12, cy + 1, 7, 5, "#f0e0b0"); rect(cx - 9, cy + 1, 1, 5, "#b8a070"); rect(cx - 12, cy + 3, 7, 1, "#b8a070"); }

  // грязь
  for (let i = 0; i < s.dirt * 3; i++) rect(cx - rx + 4 + (i * 17) % (2 * rx - 8), cy - ry + 6 + (i * 11) % (2 * ry - 10), 2, 2, "#6b4a2f");

  // позы и эффекты
  if (sleeping && !hibernate) {
    ctx.fillStyle = "#fff"; ctx.font = "8px monospace";
    for (let i = 0; i < 3; i++) { const k = ((now / 900) + i / 3) % 1; ctx.globalAlpha = 1 - k; ctx.fillText("z", cx + 24 + i * 6, cy - ry - k * 16); }
    ctx.globalAlpha = 1;
  }
  if (s.illness) { rect(cx + 12, cy - ry + 4, 2, 4, "#7fd0ff"); rect(cx - 4, cy + 7, 10, 1, "#e0e0e0"); rect(cx + 6, cy + 6, 2, 3, "#d94f4f"); }
  if (hibernate) {
    ctx.strokeStyle = "rgba(170,220,255,.95)"; ctx.lineWidth = 2;
    ctx.strokeRect(cx - rx - 8, cy - ry - 26, 2 * rx + 16, 2 * ry + 40);
    rect(cx - rx - 8, cy - ry - 26, 2 * rx + 16, 2 * ry + 40, "rgba(150,200,255,.25)");
    ctx.fillStyle = "#fff"; ctx.font = "7px monospace"; ctx.fillText("КАРАНТИН", cx - 18, cy - ry - 30);
  }
  if (fx && now < fx.until) {
    ctx.fillStyle = fx.text === "♥" ? "#ff6f91" : "#fff";
    ctx.font = "14px sans-serif";
    ctx.fillText(fx.text, cx - 4, cy - ry - 24 - ((fx.until - now) < 700 ? 0 : 0) - (1400 - (fx.until - now)) / 80);
  }
}

function drawFace(v, cx, cy, ry, now, sleeping, hibernate) {
  const s = v.state;
  const pose = s.pose;
  const blink = (now % 4200) > 4050;
  const closed = sleeping || blink;
  const mut = new Set(v.form.mutations);
  const look = pose === "hungry" ? [-2, 1] : pose === "bored" ? [0, 2] : [0, 0];
  const eyes = [
    { x: cx - 10, y: cy - 6, rx: 6, ry: 5, c: "#fff" },
    { x: cx + 4, y: cy - 9, rx: 4, ry: 3, c: "#fff" },
    { x: cx + 14, y: cy - 4, rx: 3, ry: 3, c: "#fff4a8" },
  ];
  if (mut.has("anxious_eye")) eyes.push({ x: cx - 1, y: cy - 15, rx: 3, ry: 3, c: "#ffd0d0", door: true });
  for (const e of eyes) {
    if (closed) { rect(e.x - e.rx, e.y, 2 * e.rx, 1, "#2a2438"); continue; }
    ellipse(e.x, e.y, e.rx, e.ry, e.c);
    const px = e.door ? 2 : look[0], py = e.door ? -1 : look[1];
    rect(e.x - 1 + px, e.y - 1 + py, 3, 3, "#2a2438");
    if (pose === "happy") rect(e.x + 1, e.y - 2, 1, 1, "#fff");
    if (pose === "bored" || pose === "ill") rect(e.x - e.rx, e.y - e.ry, 2 * e.rx, Math.ceil(e.ry * 0.8), "rgba(120,90,160,.8)");
  }
  // брови
  if (!closed && (s.mood === "angry" || pose === "sulk")) {
    rect(cx - 15, cy - 13, 9, 2, "#b3261e"); rect(cx + 1, cy - 14, 7, 2, "#b3261e");
  } else if (!closed && s.mood === "sad") {
    rect(cx - 15, cy - 12, 9, 1, "#2a2438"); rect(cx - 16, cy - 11, 2, 1, "#2a2438");
  }
  // рот
  const my = cy + 6;
  if (pose === "happy") { rect(cx - 6, my, 12, 1, "#2a2438"); rect(cx - 7, my - 1, 2, 1, "#2a2438"); rect(cx + 5, my - 1, 2, 1, "#2a2438"); }
  else if (pose === "hungry") { ellipse(cx - 1, my + 1, 4, 3, "#2a2438"); rect(cx - 2, my + 4, 2, 4, "#7fd0ff"); }
  else if (s.mood === "sad" || s.mood === "angry" || pose === "ill") { rect(cx - 5, my, 10, 1, "#2a2438"); rect(cx - 6, my + 1, 2, 1, "#2a2438"); rect(cx + 4, my + 1, 2, 1, "#2a2438"); }
  else rect(cx - 4, my, 8, 1, "#2a2438");
}

let last = 0;
function frame(now) {
  requestAnimationFrame(frame);
  if (now - last < 80) return;       // около 12 кадров в секунду, по-пиксельному и экономно
  last = now;
  if (!view) { rect(0, 0, 160, 120, "#2b2a3f"); return; }
  const p = palette(view.clock.hour);
  drawRoom(p, now);
  drawProps(p, view);
  drawCreature(view, now);
  if (p.night && view.state.pose !== "hibernate") rect(0, 0, 160, 120, "rgba(10,10,40,.28)");
}

// ------------------------------------------------------------------ запуск
applyTheme();
requestAnimationFrame(frame);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

document.addEventListener("visibilitychange", () => { if (!document.hidden && token) refresh().catch(() => {}); });
setInterval(() => { if (!document.hidden && token && !busy) refresh().catch(() => {}); }, 30000);

if (token) refresh().catch(() => {});
else showLogin();
})();
