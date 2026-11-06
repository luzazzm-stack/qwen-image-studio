// Qwen Image Studio front end (same look and patterns as Wan Studio).
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SIZES = { "1:1": [1024, 1024], "3:4": [896, 1184], "4:3": [1184, 896], "9:16": [768, 1344], "16:9": [1344, 768] };
const RUN = ["uploading", "submitting", "running", "downloading"];
const ACTIVE = ["queued", ...RUN];

let S = null;                 // last /api/state
let library = [];
let seenDone = null;          // job ids already known to be finished
const form = { aspect: "1:1", quality: "turbo", count: 1 };
const edit = { image: null, count: 1 };

// ------------------------------------------------------------------ helpers
async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.headers = { "Content-Type": "application/json" }; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch(path, o);
  if (r.status === 401) { location.href = "/login"; throw new Error("login"); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.error || r.statusText); e.blocked = !!d.blocked; throw e; }
  return d;
}
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.innerHTML = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 8000 : 5200);
}
function confirmBox(title, text, okLabel = "OK") {
  return new Promise(res => {
    $("#cTitle").textContent = title; $("#cText").innerHTML = text; $("#cOk").textContent = okLabel;
    const m = $("#confirm"); m.classList.remove("hidden");
    const done = v => { m.classList.add("hidden"); $("#cOk").onclick = null; m.onclick = null; res(v); };
    $("#cOk").onclick = () => done(true);
    m.onclick = e => { if (e.target === m || e.target.closest("[data-close]")) done(false); };
  });
}
const fmtMin = s => s < 90 ? `${Math.max(1, Math.round(s))} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
const DTF = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const ago = t => { const s = Date.now() / 1000 - t; return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : DTF.format(new Date(t * 1000)); };
const words = t => (t.trim() ? t.trim().split(/\s+/).length : 0);
function estimateOne(kind, p = {}) {
  if (kind === "create") { const [w, h] = SIZES[p.aspect] || SIZES["1:1"]; return (p.quality === "quality" ? 175 : 26) * (0.35 + 0.65 * w * h / 1048576); }
  return { edit: 75, rembg: 300, improve: 390 }[kind] || 60;
}
const ratio = x => (x.w && x.h) ? `${x.w}/${x.h}` : (x.aspect || "1:1").replace(":", "/");

// ------------------------------------------------------------------ navigation
function go(page) {
  if (!$("#page-" + page)) page = "create";
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("active", p.id === "page-" + page));
  if (location.hash !== "#" + page) history.replaceState(null, "", "#" + page);
  if (page === "gallery" || page === "queue") loadLibrary();
  if (page === "settings") renderSettings();
  window.scrollTo({ top: 0 });
}
$$("#nav a, a.logo").forEach(a => a.onclick = e => { e.preventDefault(); go(a.dataset.page); });
window.addEventListener("hashchange", () => { const p = location.hash.slice(1); if ($("#page-" + p) && !$("#page-" + p).classList.contains("active")) go(p); });
document.addEventListener("click", e => { const g = e.target.closest("[data-goto]"); if (g) go(g.dataset.goto); });

// ------------------------------------------------------------------ state polling + server card
async function poll() {
  try {
    S = await api("/api/state");
    renderTop(); renderJobs(); checkFinished(); renderEstimate(); renderEditState(); renderNsfw();
  } catch (e) { /* offline: keep last state */ }
  setTimeout(poll, document.hidden ? 8000 : 2000);
}

const STAGE_TEXT = {
  queued: "Asking Kaggle for 2 GPUs", booting: "Starting up", installing: "Installing software", downloading: "Downloading Qwen Image",
  starting: "Starting both GPUs", warming: "Loading the model into both GPUs", ready: "Ready", stopping: "Turning off",
};
const busyJobs = () => S.jobs.filter(j => RUN.includes(j.status));
function renderTop() {
  const p = S.power, L = S.live;
  $("#power").className = "server glass " + p;
  const dp = $("#dockPower"); dp.className = "dock-power " + p; dp.setAttribute("aria-checked", p === "ready" || p === "starting" ? "true" : "false");
  document.body.dataset.power = p;
  $("#activity").className = "glass panel activity " + p;
  $("#powerSwitch").setAttribute("aria-checked", p === "ready" || p === "starting" ? "true" : "false");
  $("#powerBar").classList.toggle("hidden", p !== "starting");
  if (p === "off") {
    $("#powerTitle").textContent = "Server off";
    $("#powerSub").textContent = S.last_stop_reason ? S.last_stop_reason : "Turn on to make images (takes ~6 min)";
  } else if (p === "starting") {
    $("#powerTitle").textContent = "Starting… " + (L.pct || 0) + "%";
    $("#powerSub").textContent = (STAGE_TEXT[L.stage] || L.stage || "Starting") + (L.detail && L.stage === "downloading" ? " · " + L.detail : "");
    $("#powerBarFill").style.width = (L.pct || 2) + "%";
  } else if (p === "ready") {
    const busy = S.jobs.some(j => ACTIVE.includes(j.status) && !j.outbox);
    $("#powerTitle").textContent = busy ? "Working" : "Ready";
    $("#powerSub").textContent = busy ? "Making images…" : (S.idle_left != null ? `Turns off in ${fmtMin(S.idle_left)} if unused` : "Ready");
  } else if (p === "stopping") {
    $("#powerTitle").textContent = "Turning off…"; $("#powerSub").textContent = "Stopping the Kaggle GPUs";
  } else if (p === "error") {
    $("#powerTitle").textContent = "Problem"; $("#powerSub").textContent = (S.error || "Something went wrong").slice(0, 140);
    $("#powerSub").title = S.error || "";
  }
  $("#homeStatus").textContent = $("#powerTitle").textContent;
  $("#homeSub").textContent = $("#powerSub").textContent;
  $("#homePower").textContent = p === "ready" || p === "starting" ? "Turn off" : p === "stopping" ? "Stopping…" : "Turn on";
  $("#homePower").disabled = p === "stopping";
  $$(".gpu[data-g]").forEach(el => {
    const g = +el.dataset.g;
    const job = S.jobs.find(j => j.gpu === g && ["submitting", "running"].includes(j.status));
    el.classList.toggle("idle", p === "ready" && !job);
    el.classList.toggle("busy", !!job);
    $(".gpu-fill", el).style.width = job ? Math.max(4, Math.round((job.progress || 0) * 100)) + "%" : "0%";
    $(".gpu-val", el).textContent = job ? (job.phase === "finishing" ? "saving" : job.progress ? Math.round(job.progress * 100) + "%" : "starting") : p === "ready" ? "idle" : p === "starting" ? "…" : "off";
  });
  const q = S.quota;
  if (q && q.total_h) {
    const left = Math.max(0, q.total_h - q.used_h - (q.reserved_h || 0));
    $("#quotaVal").textContent = left.toFixed(1) + " h";
    $("#quotaRing").style.width = (100 * left / q.total_h).toFixed(1) + "%";
    $("#quota").title = `Used ${q.used_h.toFixed(1)} of ${q.total_h} free GPU hours this week` + (q.refresh ? `. Resets ${DTF.format(new Date(q.refresh + "Z"))}` : "");
  }
  document.documentElement.dataset.theme = S.settings.theme || "dark";
  $$(".local-only").forEach(el => el.classList.toggle("hidden", !S.local));
}

$("#powerSwitch").onclick = async () => {
  const p = S?.power;
  if (p === "ready" || p === "starting") {
    const active = S.jobs.filter(j => ["submitting", "running"].includes(j.status)).length;
    const ok = await confirmBox("Turn off the server?", active
      ? `<b>${active} image(s) are being made right now.</b> They will be stopped and made again next time you turn the server on.`
      : "This stops the Kaggle GPUs so they don't use your free hours.", "Turn off");
    if (!ok) return;
    await api("/api/power", { json: { on: false } });
    toast("Turning off…");
  } else {
    await api("/api/power", { json: { on: true } });
    toast("Starting the Kaggle server. It takes about 6 minutes. You can queue images now.");
  }
  setTimeout(poll, 400);
};
$("#dockPower").onclick = () => $("#powerSwitch").click();
$("#homePower").onclick = () => $("#powerSwitch").click();

// ------------------------------------------------------------------ finished-job notices
const WHAT = { create: "Your image", edit: "Your edited photo", rembg: "Background removal", improve: "Prompt helper" };
function checkFinished() {
  const done = S.jobs.filter(j => ["done", "failed"].includes(j.status) && j.kind !== "improve");
  if (seenDone === null) { seenDone = new Set(done.map(j => j.id)); return; }
  let fresh = false;
  for (const j of done) {
    if (seenDone.has(j.id)) continue;
    seenDone.add(j.id);
    const what = WHAT[j.kind] || "Your image";
    if (j.status === "done") {
      fresh = true;
      toast(`${what} is ready${j.took ? " (" + fmtMin(j.took) + ")" : ""}`, "good");
      if ($("#notifyDone").checked && "Notification" in window && Notification.permission === "granted" && document.hidden)
        new Notification("Qwen Image Studio", { body: `${what} is ready` });
    } else toast(`${what} failed: ${esc((j.error || "").slice(0, 160))}`, "bad");
  }
  if (fresh) loadLibrary();
}

// ------------------------------------------------------------------ jobs (queue page + mini queue)
const KIND = { create: "Create", edit: "Edit", rembg: "No bg", improve: "Prompt" };
function jobLine(j) {
  const p = j.params || {};
  if (j.kind === "rembg") return "Remove background";
  if (j.kind === "improve") return "Improve: " + (p.prompt || "");
  if (j.kind === "edit") return "Edit: " + (p.prompt || "");
  return p.prompt || "Image";
}
function jobStatusText(j) {
  if (j.status === "queued") {
    if (j.outbox || S.power !== "ready") return "Waiting for the server to be on";
    const ahead = S.jobs.filter(x => x.status === "queued" && !x.outbox && x.created < j.created).length;
    return ahead ? `${ahead} ahead` : "Starts next";
  }
  if (j.status === "running") {
    if (j.phase === "finishing") return `GPU ${j.gpu + 1} · saving…`;
    const pct = Math.round((j.progress || 0) * 100);
    return `GPU ${j.gpu + 1} · ${pct ? pct + "%" : "preparing"}${j.eta ? " · about " + fmtMin(j.eta) + " left" : ""}`;
  }
  if (j.status === "downloading") return "Saving to your laptop…";
  if (j.status === "done") return `Done ${ago(j.finished)}${j.took ? " · took " + fmtMin(j.took) : ""}`;
  if (j.status === "failed") return "Failed " + ago(j.finished || j.created);
  if (j.status === "cancelled") return "Cancelled";
  return j.status;
}
function jobMeta(j) {
  const p = j.params || {};
  const bits = [];
  if (j.kind === "create") bits.push(p.aspect, p.quality === "quality" ? "Quality" : "Turbo");
  if (p.seed != null && p.seed >= 0) bits.push("seed " + p.seed);
  if (p.nsfw && j.kind === "create") bits.push("NSFW");
  if (j.source && j.source !== "laptop") bits.push("from " + j.source);
  return bits.filter(Boolean).join(" · ");
}
function renderJobs() {
  const jobs = [...S.jobs].reverse();
  const active = S.jobs.filter(j => ACTIVE.includes(j.status));
  const groups = [["Making now", S.jobs.filter(j => RUN.includes(j.status))],
                  ["Waiting", S.jobs.filter(j => j.status === "queued")],
                  ["Finished", jobs.filter(j => !ACTIVE.includes(j.status))]];
  const badge = $("#queueBadge");
  badge.textContent = active.length; badge.classList.toggle("hidden", !active.length);
  const jobHtml = j => `
    <div class="job">
      <div class="job-kind">${KIND[j.kind] || j.kind}</div>
      <div class="job-main">
        <div class="job-title">${esc(jobLine(j))}</div>
        <div class="job-sub"><span class="st ${j.status}">${j.status}</span> ${esc(jobStatusText(j))} ${jobMeta(j) ? "· " + esc(jobMeta(j)) : ""}</div>
        ${j.status === "running" ? `<div class="bar ${j.progress ? "" : "indet"}"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div>` : ""}
        ${j.status === "downloading" ? `<div class="bar indet"><i></i></div>` : ""}
        ${j.error ? `<div class="job-err">${esc(j.error)}</div>` : ""}
        ${j.note && ACTIVE.includes(j.status) ? `<div class="job-note">${esc(j.note)}</div>` : ""}
      </div>
      <div class="row">
        ${["queued", "running"].includes(j.status) && j.kind !== "improve" ? `<button class="btn small danger" data-cancel="${j.id}">Cancel</button>` : ""}
        ${["failed", "cancelled"].includes(j.status) && j.kind !== "improve" && !j.blocked ? `<button class="btn small" data-retry="${j.id}">Try again</button>` : ""}
        ${j.status === "done" && j.result ? `<button class="btn small" data-open="${j.result}">View</button>` : ""}
      </div>
    </div>`;
  $("#jobList").innerHTML = jobs.length
    ? groups.filter(([, list]) => list.length).map(([name, list]) => `<div class="job-group">${name} (${list.length})</div>` + list.map(jobHtml).join("")).join("")
    : `<div class="empty">Nothing in the queue. Images you make show up here.</div>`;
  const shown = [...active].sort((a, b) => (RUN.includes(b.status) - RUN.includes(a.status)) || a.created - b.created);
  $("#miniQueue").innerHTML = shown.slice(0, 4).map(j => `
    <div class="mq"><div class="mq-top"><span>${esc(jobLine(j))}</span><span class="hint">${esc(jobStatusText(j))}</span></div>
    ${j.status === "running" ? `<div class="bar ${j.progress ? "" : "indet"}"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div>` : ""}</div>`).join("")
    + (!active.length ? `<div class="hint">Nothing is being made right now. Press Generate to start.</div>` : "")
    + (active.length > 4 ? `<a class="link" data-goto="queue">${active.length - 4} more in the queue</a>` : "");
}
document.addEventListener("click", async e => {
  const c = e.target.closest("[data-cancel]"), r = e.target.closest("[data-retry]"), o = e.target.closest("[data-open]");
  try {
    if (c) { await api(`/api/jobs/${c.dataset.cancel}/cancel`, { method: "POST" }); toast("Cancelling…"); poll(); }
    if (r) { await api(`/api/jobs/${r.dataset.retry}/retry`, { method: "POST" }); toast("Added to the queue again"); }
    if (o) { if (!library.length) await loadLibrary(); openViewer(o.dataset.open); }
  } catch (err) { toast(esc(err.message), "bad"); }
});
$("#clearJobsBtn").onclick = async () => { await api("/api/jobs/clear", { method: "POST" }); poll(); };

// ------------------------------------------------------------------ create form
const promptEl = $("#prompt");
function setAspect(a) {
  form.aspect = SIZES[a] ? a : "1:1";
  $$("#aspects button").forEach(x => x.classList.toggle("on", x.dataset.a === form.aspect));
  $("#sizeHint").textContent = SIZES[form.aspect].join(" × ") + " pixels";
  renderEstimate();
}
function setQuality(q) { form.quality = q === "quality" ? "quality" : "turbo"; $$("#qualitySeg button").forEach(x => x.classList.toggle("on", x.dataset.q === form.quality)); renderEstimate(); }
function setCount(n) { form.count = +n; $$("#countSeg button").forEach(x => x.classList.toggle("on", +x.dataset.n === form.count)); renderEstimate(); }
$$("#aspects button").forEach(b => b.onclick = () => setAspect(b.dataset.a));
$$("#qualitySeg button").forEach(b => b.onclick = () => setQuality(b.dataset.q));
$$("#countSeg button").forEach(b => b.onclick = () => setCount(b.dataset.n));
$("#diceBtn").onclick = () => $("#seed").value = Math.floor(Math.random() * 2 ** 31);
$("#seedClear").onclick = () => $("#seed").value = "";
$("#clearBtn").onclick = () => { promptEl.value = ""; $("#improveOpts").classList.add("hidden"); countWords(); };
$("#notifyDone").onchange = () => { if ($("#notifyDone").checked && "Notification" in window) Notification.requestPermission(); };
function countWords() { $("#wordCount").textContent = words(promptEl.value) + " words"; }
promptEl.oninput = countWords;

function renderEstimate() {
  if (!S) return;
  const one = estimateOne("create", form);
  const free = 2 - busyJobs().length;
  const ahead = S.jobs.filter(j => ACTIVE.includes(j.status)).length;
  const rounds = Math.ceil(form.count / 2);
  let txt = `About <b>${fmtMin(one)}</b> per image` + (form.count > 1 ? `, ${form.count} images on both GPUs: about <b>${fmtMin(one * rounds)}</b>` : "");
  if (ahead) txt += `. ${ahead} ${ahead === 1 ? "job" : "jobs"} ahead`;
  if (S.power === "off" || S.power === "error") txt += "<br>Server is off: the image starts once it's on.";
  if (S.power === "starting") txt += "<br>Server is starting: your image begins when it's ready.";
  $("#estimate").innerHTML = txt;
  void free;
}

async function offerPowerOn(what) {
  if (S && (S.power === "off" || S.power === "error")) {
    if (await confirmBox("Turn on the server?", `The Kaggle GPU server is off. Turn it on now? It takes about 6 minutes, then ${what} starts automatically.`, "Turn on"))
      await api("/api/power", { json: { on: true } });
  }
}
$("#generateBtn").onclick = async () => {
  const prompt = promptEl.value.trim();
  if (!prompt) { toast("Describe your image first"); promptEl.focus(); return; }
  const seed = $("#seed").value.trim();
  try {
    const r = await api("/api/submit", { json: { kind: "create", prompt, negative: $("#negative").value.trim(), aspect: form.aspect,
      quality: form.quality, count: form.count, seed: seed === "" ? -1 : +seed } });
    toast(`${form.count > 1 ? form.count + " images" : "Image"} added. <a class="link" data-goto="queue">See progress</a>`, "good");
    if ($("#notifyDone").checked && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
    if (!r.sent) await offerPowerOn("your image");
    poll();
  } catch (e) { toast((e.blocked ? "" : "Could not add: ") + esc(e.message), "bad"); }
};

// ------------------------------------------------------------------ prompt helper (official Qwen prompt enhancer on the GPU)
function showOptions(box, opts, note, onPick) {
  box.innerHTML = `<div class="opts-head"><b>Improved version</b><span class="hint">${esc(note || "")}</span><button class="btn small ghost" data-optclose>Keep mine</button></div>`
    + opts.map((o, i) => `<button class="opt" data-opt="${i}"><span class="opt-n">${i + 1}</span><span>${esc(o)}</span></button>`).join("");
  box.classList.remove("hidden");
  box.onclick = e => {
    if (e.target.closest("[data-optclose]")) { box.classList.add("hidden"); return; }
    const b = e.target.closest("[data-opt]"); if (!b) return;
    onPick(opts[+b.dataset.opt]); box.classList.add("hidden"); toast("Prompt updated", "good");
  };
}
async function improve({ btn, label, input, box, mode, image }) {
  const idea = input.value.trim();
  if (!idea) return toast("Write a short idea first");
  const span = $("span", btn);
  btn.disabled = true; span.textContent = "Writing…";
  const t0 = Date.now();
  try {
    const r = await api("/api/improve", { json: { prompt: idea, mode, aspect: form.aspect, image } });
    if (r.text) { showOptions(box, [r.text], `in ${r.took} s`, t => { input.value = t; input.dispatchEvent(new Event("input")); }); btn.disabled = false; span.textContent = label; return; }
    if (!r.pe_ready) toast("The prompt helper model is still downloading on the server. Your request waits for it.");
    for (;;) {
      await new Promise(res => setTimeout(res, 2500));
      const j = await api("/api/improve/" + r.id);
      const secs = Math.round((Date.now() - t0) / 1000);
      span.textContent = j.status === "queued" ? `Waiting for a free GPU… ${secs} s` : `Writing… ${secs} s`;
      if (j.status === "done") { showOptions(box, [j.text], `by the Qwen prompt enhancer in ${fmtMin(j.took || secs)}`, t => { input.value = t; input.dispatchEvent(new Event("input")); }); break; }
      if (["failed", "cancelled"].includes(j.status)) { toast(esc(j.error || "The prompt helper failed"), "bad"); break; }
      if (secs > 900) { toast("The prompt helper is taking too long. Try again later.", "bad"); break; }
    }
  } catch (e) { toast(esc(e.message), "bad"); }
  btn.disabled = false; span.textContent = label;
}
$("#improveBtn").onclick = () => improve({ btn: $("#improveBtn"), label: "Improve my prompt", input: promptEl, box: $("#improveOpts"), mode: "t2i" });

// ------------------------------------------------------------------ edit page
const eDrop = $("#eDrop");
eDrop.onclick = e => { if (!e.target.closest("#eChange")) $("#eFile").click(); };
$("#eChange").onclick = e => { e.stopPropagation(); $("#eFile").click(); };
eDrop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#eFile").click(); } };
eDrop.ondragover = e => { e.preventDefault(); eDrop.classList.add("over"); };
eDrop.ondragleave = () => eDrop.classList.remove("over");
eDrop.ondrop = e => { e.preventDefault(); eDrop.classList.remove("over"); if (e.dataTransfer.files[0]) uploadEditPhoto(e.dataTransfer.files[0]); };
$("#eFile").onchange = () => { if ($("#eFile").files[0]) uploadEditPhoto($("#eFile").files[0]); $("#eFile").value = ""; };
async function uploadEditPhoto(file) {
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd }).then(r => r.json());
    if (r.error) throw new Error(r.error);
    setEditImage(r.name);
  } catch (e) { toast("Upload failed: " + esc(e.message), "bad"); }
}
function setEditImage(name) {
  edit.image = name;
  $("#eImg").src = "/uploads/" + name;
  $("#eEmpty").classList.add("hidden"); $("#eFull").classList.remove("hidden");
  renderEditState();
}
$$("#eCountSeg button").forEach(b => b.onclick = () => { edit.count = +b.dataset.n; $$("#eCountSeg button").forEach(x => x.classList.toggle("on", x === b)); renderEditState(); });
$("#ePrompt").oninput = () => { $("#eWords").textContent = words($("#ePrompt").value) + " words"; renderEditState(); };
$("#eClear").onclick = () => { $("#ePrompt").value = ""; $("#ePrompt").oninput(); $("#eImproveOpts").classList.add("hidden"); };
function renderEditState() {
  const has = !!edit.image, txt = $("#ePrompt").value.trim();
  $("#eBtn").disabled = !has || !txt; $("#rembgBtn").disabled = !has;
  if (!has) { $("#eEstimate").textContent = "Choose a photo first."; return; }
  if (!txt) { $("#eEstimate").textContent = "Write what should change, or press Remove background."; return; }
  let t = `About <b>${fmtMin(estimateOne("edit"))}</b>` + (edit.count > 1 ? " each, both GPUs at once" : "");
  if (S && S.power !== "ready") t += "<br>The server is off: it starts once it's on.";
  $("#eEstimate").innerHTML = t;
}
$("#eBtn").onclick = async () => {
  try {
    const r = await api("/api/submit", { json: { kind: "edit", image: edit.image, prompt: $("#ePrompt").value.trim(), count: edit.count, seed: -1 } });
    toast(`Edit added. <a class="link" data-goto="queue">See progress</a>`, "good");
    if (!r.sent) await offerPowerOn("the edit");
    poll();
  } catch (e) { toast(esc(e.message), "bad"); }
};
$("#rembgBtn").onclick = () => removeBackground(edit.image);
async function removeBackground(image) {
  try {
    const r = await api("/api/submit", { json: { kind: "rembg", image } });
    toast(`Background removal added (about 5 min). <a class="link" data-goto="queue">See progress</a>`, "good");
    if (!r.sent) await offerPowerOn("it");
    poll();
  } catch (e) { toast(esc(e.message), "bad"); }
}
$("#eImproveBtn").onclick = () => {
  if (!edit.image) return toast("Choose a photo first");
  improve({ btn: $("#eImproveBtn"), label: "Improve my instruction", input: $("#ePrompt"), box: $("#eImproveOpts"), mode: "i2i", image: edit.image });
};

// ------------------------------------------------------------------ gallery + viewer
let galFilter = "all";
async function loadLibrary() {
  try { library = await api("/api/library"); } catch (e) { return; }
  renderGallery(); renderPreview();
}
function renderGallery() {
  const q = $("#galSearch").value.toLowerCase(), f = galFilter;
  const list = library.filter(x => (f === "all" || (f === "fav" && x.favorite) || x.kind === f || (f === "phone" && x.from && x.from !== "laptop"))
    && (!q || (x.prompt || "").toLowerCase().includes(q)));
  $("#galCount").textContent = `${library.length} image${library.length === 1 ? "" : "s"} · saved in your Pictures folder`;
  $("#galGrid").innerHTML = list.map(x => `
    <div class="vcard icard">
      <button class="thumb ${x.kind === "rembg" ? "checker" : ""}" data-view="${x.id}" aria-label="Open image" style="aspect-ratio:${ratio(x)}">
        <img src="/thumbs/${x.id}.jpg" alt="" loading="lazy" width="${x.w || 1024}" height="${x.h || 1024}">
        <span class="tag">${x.kind === "edit" ? "Edit" : x.kind === "rembg" ? "No bg" : x.quality === "quality" ? "Quality" : "Turbo"}</span>${x.favorite ? `<span class="star" aria-label="Favourite">★</span>` : ""}
      </button>
      <div class="tbody"><p>${esc(x.prompt)}</p><span class="hint num">${ago(x.created)}${x.w ? `, ${x.w}×${x.h}` : ""}${x.seed != null ? `, seed ${x.seed}` : ""}</span></div>
    </div>`).join("") || `<div class="empty">No images yet. Make one on the Create page.</div>`;
}
$("#galSearch").oninput = renderGallery;
$$("#galFilters button").forEach(b => b.onclick = () => { galFilter = b.dataset.f; $$("#galFilters button").forEach(x => x.classList.toggle("on", x === b)); renderGallery(); });
$("#galGrid").onclick = e => { const v = e.target.closest("[data-view]"); if (v) openViewer(v.dataset.view); };
function renderRecent() {
  $("#homeRecent").innerHTML = library.slice(0, 6).map(x => `<button class="rthumb" data-open="${x.id}" aria-label="Open image">
    <img src="/thumbs/${x.id}.jpg" alt="" loading="lazy"><span class="tag">${x.kind === "edit" ? "Edit" : x.kind === "rembg" ? "No bg" : x.aspect || ""}</span></button>`).join("")
    || `<div class="hint">Your finished images will show up here.</div>`;
}
function renderPreview() {
  renderRecent();
  const x = library[0];
  if (!x || $("#previewBody").dataset.id === x.id) return;
  $("#previewBody").dataset.id = x.id;
  $("#previewBody").innerHTML = `<button class="pv-img ${x.kind === "rembg" ? "checker" : ""}" data-open="${x.id}" aria-label="Open image"><img src="/media/${encodeURIComponent(x.file)}" alt=""></button>
    <p class="hint">${esc((x.prompt || "").length > 160 ? x.prompt.slice(0, 157) + "…" : x.prompt || "")}</p><div class="row"><button class="btn small" data-open="${x.id}">Open</button><a class="btn small" href="/media/${encodeURIComponent(x.file)}?dl=1" download>Download</a></div>`;
}

let cur = null;
function openViewer(id) {
  cur = library.find(x => x.id === id); if (!cur) return;
  const src = "/media/" + encodeURIComponent(cur.file);
  $("#vImg").src = src;
  $("#vImgWrap").classList.toggle("checker", cur.kind === "rembg");
  $("#vTitle").textContent = cur.kind === "edit" ? "Edited photo" : cur.kind === "rembg" ? "Background removed" : "Made from words";
  $("#vTags").innerHTML = [cur.w ? `${cur.w}×${cur.h}` : null, cur.aspect, cur.kind === "create" ? (cur.quality === "quality" ? "Quality" : "Turbo") : null,
    cur.nsfw ? "NSFW" : null, cur.from && cur.from !== "laptop" ? "from " + cur.from : null].filter(Boolean).map(t => `<span>${esc(t)}</span>`).join("");
  $("#vPrompt").textContent = cur.prompt || "";
  $("#vMeta").innerHTML = [["Made", DTF.format(new Date(cur.created * 1000))], ["Took", cur.took ? fmtMin(cur.took) : "–"], ["Seed", cur.seed ?? "–"],
    ["Size", cur.size ? (cur.size / 1048576).toFixed(1) + " MB" : "–"], ["File", cur.file]].map(([k, v]) => `<span>${k}</span><span>${esc(v)}</span>`).join("");
  $("#vDownload").href = src + "?dl=1";
  $("#vFav").textContent = cur.favorite ? "Remove from favourites" : "Add to favourites";
  $("#vReuse").disabled = $("#vVariations").disabled = $("#vCopy").disabled = cur.kind !== "create";
  $("#viewer").classList.remove("hidden");
}
$$(".modal").forEach(m => m.addEventListener("click", e => { if (m.id !== "confirm" && (e.target === m || e.target.closest("[data-close]"))) m.classList.add("hidden"); }));
document.addEventListener("keydown", e => { if (e.key === "Escape") $$(".modal:not(.hidden)").forEach(m => { if (m.id !== "confirm") m.classList.add("hidden"); }); });
$("#vFav").onclick = async () => { cur.favorite = !cur.favorite; await api("/api/library/" + cur.id, { json: { favorite: cur.favorite } }); $("#vFav").textContent = cur.favorite ? "Remove from favourites" : "Add to favourites"; renderGallery(); };
$("#vReuse").onclick = () => {
  promptEl.value = cur.prompt || ""; setAspect(cur.aspect || "1:1"); setQuality(cur.quality); $("#seed").value = cur.seed ?? "";
  $("#negative").value = cur.negative || "";
  $("#viewer").classList.add("hidden"); countWords(); go("create");
  toast("Prompt and settings loaded, including the seed. Change one thing and generate.");
};
$("#vVariations").onclick = async () => {
  try {
    const r = await api("/api/submit", { json: { kind: "create", prompt: cur.prompt, negative: cur.negative, aspect: cur.aspect || "1:1", quality: cur.quality, count: 2, seed: -1 } });
    toast("2 more images added to the queue", "good"); if (!r.sent) await offerPowerOn("they");
  } catch (e) { toast(esc(e.message), "bad"); }
};
async function useInEdit(then) {
  const r = await api(`/api/library/${cur.id}/use`, { method: "POST" });
  $("#viewer").classList.add("hidden");
  return then(r.name);
}
$("#vEdit").onclick = () => useInEdit(name => { setEditImage(name); go("edit"); $("#ePrompt").focus(); });
$("#vRembg").onclick = () => useInEdit(name => removeBackground(name));
$("#vCopy").onclick = async () => { try { await navigator.clipboard.writeText(cur.prompt || ""); toast("Prompt copied"); } catch (e) { toast("Could not copy"); } };
$("#vDelete").onclick = async () => {
  if (!(await confirmBox("Delete this image?", "The file will be deleted from your Pictures folder, and from the Kaggle server if it is on.", "Delete"))) return;
  await api("/api/library/" + cur.id, { method: "DELETE" });
  $("#viewer").classList.add("hidden"); $("#previewBody").dataset.id = ""; loadLibrary();
};
const openFolder = () => api("/api/open-folder", { method: "POST" });
$("#openFolderBtn").onclick = openFolder; $("#openFolderBtn2").onclick = openFolder;

// ------------------------------------------------------------------ settings
function nsfwStatusText() {
  const st = S.settings, L = S.live;
  if (!st.nsfw) return "Off. Images are made without the NSFW LoRA.";
  if (S.power !== "ready") return "On. The LoRA is loaded and checked when the server starts.";
  if (L.nsfw === "ok") return "On and working on this server.";
  if (L.nsfw === "checking") return "On. Checking the LoRA on the server…";
  return "NSFW detail unavailable on this server" + (L.nsfw_detail ? ` (${L.nsfw_detail})` : "") + ". Images are made without it.";
}
function renderNsfw() {
  if (!S) return;
  $("#nsfwBadge").classList.toggle("hidden", !S.settings.nsfw);
  $("#nsfwBadge").classList.toggle("warn", !!S.settings.nsfw && S.power === "ready" && S.live.nsfw !== "ok" && S.live.nsfw !== "checking");
  $("#nsfwBadge").textContent = S.settings.nsfw && S.power === "ready" && S.live.nsfw !== "ok" && S.live.nsfw !== "checking" ? "NSFW unavailable" : "NSFW on";
  if ($("#page-settings").classList.contains("active")) $("#nsfwStatus").textContent = nsfwStatusText();
}
function renderSettings() {
  if (!S) return;
  const st = S.settings;
  $("#setIdle").value = st.idle_min; $("#idleLabel").textContent = st.idle_min + " min";
  $("#setMax").value = st.max_hours; $("#maxLabel").textContent = st.max_hours + " h";
  $("#setAspect").value = st.aspect; $("#setQuality").value = st.quality;
  $$("#themeSeg button").forEach(b => b.classList.toggle("on", b.dataset.t === (st.theme || "dark")));
  $("#setNsfw").checked = !!st.nsfw;
  $("#setNsfwStrength").value = st.nsfw_strength; $("#nsfwStrengthLabel").textContent = (+st.nsfw_strength).toFixed(2);
  if (document.activeElement !== $("#setLoraRepo")) $("#setLoraRepo").value = st.nsfw_lora_repo || "";
  if (document.activeElement !== $("#setLoraFile")) $("#setLoraFile").value = st.nsfw_lora_file || "";
  $("#nsfwStatus").textContent = nsfwStatusText();
  $("#outDir").textContent = st.output_dir;
  $("#pinShow").textContent = st.pin || "••••";
  $("#lanUrl").textContent = S.lan_url || "Not connected to Wi-Fi";
  if (S.lan_url && window.QRCode && $("#qr").dataset.u !== S.lan_url) {
    $("#qr").innerHTML = ""; new QRCode($("#qr"), { text: S.lan_url, width: 128, height: 128 }); $("#qr").dataset.u = S.lan_url;
  }
  const t = S.live.timings || {}, pe = S.live.pe || {};
  const peText = S.power !== "ready" ? "off" : pe.t2i === true && pe.i2i === true ? "ready" : pe.t2i === "failed" ? "failed to download" : "downloading…";
  $("#serverInfo").innerHTML = [["Status", S.power], ["Kaggle", S.kaggle ? S.kaggle.kernel : "–"], ["Up for", S.live.uptime ? fmtMin(S.live.uptime) : "–"],
    ["Start-up time", t.ready_s ? fmtMin(t.ready_s) : "–"], ["Prompt helper", peText],
    ["GPU hours", S.quota ? `${S.quota.used_h.toFixed(1)} used of ${S.quota.total_h}` : "–"]].map(([k, v]) => `<span>${k}</span><span>${esc(v)}</span>`).join("");
}
const saveSetting = async d => { await api("/api/settings", { json: d }); S = await api("/api/state"); renderSettings(); renderTop(); renderNsfw(); };
$("#setIdle").oninput = () => $("#idleLabel").textContent = $("#setIdle").value + " min";
$("#setIdle").onchange = () => saveSetting({ idle_min: +$("#setIdle").value });
$("#setMax").oninput = () => $("#maxLabel").textContent = $("#setMax").value + " h";
$("#setMax").onchange = () => saveSetting({ max_hours: +$("#setMax").value });
$("#setAspect").onchange = () => { saveSetting({ aspect: $("#setAspect").value }); setAspect($("#setAspect").value); };
$("#setQuality").onchange = () => { saveSetting({ quality: $("#setQuality").value }); setQuality($("#setQuality").value); };
$$("#themeSeg button").forEach(b => b.onclick = () => saveSetting({ theme: b.dataset.t }));
$("#setNsfw").onchange = () => saveSetting({ nsfw: $("#setNsfw").checked });
$("#setNsfwStrength").oninput = () => $("#nsfwStrengthLabel").textContent = (+$("#setNsfwStrength").value).toFixed(2);
$("#setNsfwStrength").onchange = () => saveSetting({ nsfw_strength: +$("#setNsfwStrength").value });
$("#saveLora").onclick = async () => { await saveSetting({ nsfw_lora_repo: $("#setLoraRepo").value.trim(), nsfw_lora_file: $("#setLoraFile").value.trim() }); toast("Saved. The server uses it from its next start.", "good"); };
$("#savePin").onclick = async () => {
  const v = $("#setPin").value.trim();
  if (!/^\d{4,8}$/.test(v)) return toast("PIN must be 4 to 8 digits");
  await saveSetting({ pin: v }); $("#setPin").value = ""; toast("PIN changed. Phones will need the new PIN.", "good");
};

// ------------------------------------------------------------------ boot
(async function boot() {
  try { S = await api("/api/state"); } catch (e) { S = null; }
  if (S) {
    setAspect(S.settings.aspect || "1:1"); setQuality(S.settings.quality); setCount(S.settings.count || 1);
    document.documentElement.dataset.theme = S.settings.theme || "dark";
    renderNsfw();
  } else setAspect("1:1");
  countWords();
  await loadLibrary();
  go((location.hash || "#create").slice(1) || "create");
  poll();
})();
