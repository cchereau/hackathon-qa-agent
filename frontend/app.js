// web/app.js
// -------------------------------------------------------------
// Hackathon QA Test Plan Agent (T0 / T0+)
// -------------------------------------------------------------

let API_BASE = ""; // resolved at runtime

async function resolveApiBase() {
  const origin = window.location.origin;
  const host = window.location.hostname || "127.0.0.1";

  try {
    const r = await fetch(`${origin}/health`, { method: "GET" });
    if (r.ok) {
      API_BASE = ""; // same origin FastAPI
      return;
    }
  } catch (_) {}

  API_BASE = `http://${host}:8000`;
}

// ─────────────────────────────────────────────────────────────
// Globals
// ─────────────────────────────────────────────────────────────
let TOP_PANEL = "issue";
let LAST_GENERATED = null;
let ISSUE_DIRTY = false;

// Inspector role/mode:
// - "issue" => opened from G1/G2; tabs: Jira, Xray, Bitbucket, Prompt. (no Plan)
// - "plan"  => opened from G4; tabs: Plan (HTML). (no Prompt)
let INSPECTOR_MODE = "issue";

// ─────────────────────────────────────────────────────────────
// UI Helpers
// ─────────────────────────────────────────────────────────────
function showPanel(name) {
  TOP_PANEL = name;
  document.getElementById("tab-issue")?.classList.toggle("active", name === "issue");
  document.getElementById("tab-plans")?.classList.toggle("active", name === "plans");
  document.getElementById("panel-issue")?.classList.toggle("active", name === "issue");
  document.getElementById("panel-plans")?.classList.toggle("active", name === "plans");

  if (name === "plans") {
    loadTestPlans();
  }
}

function setExportButtonState() {
  const btn = document.getElementById("exportBtn");
  if (!btn) return;
  btn.disabled = !ISSUE_DIRTY || !LAST_GENERATED?.jira_key;
}

function setInspectorRoleLabel(text) {
  const el = document.getElementById("inspectorRole");
  if (el) el.textContent = text ? `(${text})` : "";
}

function setInspectorKeyLabel(value) {
  const el = document.getElementById("inspectorKey");
  if (el) el.textContent = value || "—";
}

// ─────────────────────────────────────────────────────────────
// Jira keys combo
// ─────────────────────────────────────────────────────────────
async function loadJiraKeysIntoCombo() {
  const sel = document.getElementById("jiraKeySelect");
  if (!sel) return;

  sel.innerHTML = `<option value="">Loading...</option>`;
  try {
    const r = await fetch(`${API_BASE}/api/jira/issue-keys`);
    if (!r.ok) throw new Error(await r.text());
    const payload = await r.json();
    const keys = payload?.data || [];

    sel.innerHTML = "";
    for (const k of keys) {
      const opt = document.createElement("option");
      opt.value = k;
      opt.textContent = k;
      sel.appendChild(opt);
    }
  } catch (e) {
    console.error(e);
    sel.innerHTML = `<option value="">(failed to load US keys)</option>`;
  }
}

function getSelectedUsKey() {
  const selectEl = document.getElementById("jiraKeySelect");
  return (selectEl?.value || "").trim();
}

// ─────────────────────────────────────────────────────────────
// Issue Generator (G1/G2)
// ─────────────────────────────────────────────────────────────
async function generate() {
  const jira_key = getSelectedUsKey();
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const jsonEl = document.getElementById("jsonResult");

  if (!jira_key) {
    statusEl.textContent = "Please select a Jira key (US-xxx).";
    statusEl.style.color = "red";
    return;
  }

  statusEl.textContent = "Generating test plan with LLM...";
  statusEl.style.color = "#333";
  resultEl.textContent = "";
  jsonEl.textContent = "";

  try {
    const resp = await fetch(`${API_BASE}/agent/test-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jira_key }),
    });

    if (!resp.ok) {
      const txt = await resp.text();
      statusEl.textContent = `Error ${resp.status}: ${txt}`;
      statusEl.style.color = "red";
      return;
    }

    const payload = await resp.json();
    LAST_GENERATED = payload;
    ISSUE_DIRTY = true;
    setExportButtonState();

    resultEl.textContent = payload?.markdown || "";
    jsonEl.textContent = JSON.stringify(payload?.suggestions || [], null, 2);

    statusEl.textContent = "Done. Review result, then export the run for G4.";
    statusEl.style.color = "green";
  } catch (e) {
    console.error(e);
    statusEl.textContent = "Failed: " + (e?.message || String(e));
    statusEl.style.color = "red";
  }
}

async function exportRun() {
  const statusEl = document.getElementById("status");
  const jira_key = LAST_GENERATED?.jira_key;

  if (!jira_key) {
    statusEl.textContent = "Nothing to export (generate first).";
    statusEl.style.color = "red";
    return;
  }

  statusEl.textContent = "Exporting run to junction...";
  statusEl.style.color = "#333";

  try {
    const resp = await fetch(`${API_BASE}/api/junction/runs/${encodeURIComponent(jira_key)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(LAST_GENERATED),
    });

    if (!resp.ok) {
      const txt = await resp.text();
      statusEl.textContent = `Export failed ${resp.status}: ${txt}`;
      statusEl.style.color = "red";
      return;
    }

    ISSUE_DIRTY = false;
    setExportButtonState();

    statusEl.textContent = "Run exported. XRAY preview is now able to show AI candidates.";
    statusEl.style.color = "green";

    await loadOverlaysCache();
    if (isInspectorOpen() && INSPECTOR_MODE === "issue") {
      rebuildDrawerOverlaySelectForIssue(getSelectedUsKey());
    }
  } catch (e) {
    console.error(e);
    statusEl.textContent = "Export failed: " + (e?.message || String(e));
    statusEl.style.color = "red";
  }
}

// ─────────────────────────────────────────────────────────────
// Test Plans (G4) - List
// ─────────────────────────────────────────────────────────────
function badgeHtml(status) {
  const s = (status || "NOT_ANALYZED").toUpperCase();
  if (s === "AUTO") return `<span class="badge green">AUTO</span>`;
  if (s === "REVIEW") return `<span class="badge orange">REVIEW</span>`;
  return `<span class="badge gray">NOT_ANALYZED</span>`;
}

async function loadTestPlans() {
  const overlaySel = document.getElementById("overlaySelect");
  const overlay = (overlaySel?.value || "").trim();
  const tbody = document.getElementById("plansTbody");
  const statusEl = document.getElementById("plansStatus");

  if (!tbody || !statusEl) return;

  tbody.innerHTML = `<tr><td colspan="6" class="muted">Loading...</td></tr>`;
  statusEl.textContent = overlay ? `Loading plans with overlay=${overlay}...` : "Loading baseline plans...";

  try {
    const url = overlay
      ? `${API_BASE}/api/test-plans?overlay=${encodeURIComponent(overlay)}`
      : `${API_BASE}/api/test-plans`;
    const resp = await fetch(url);
    if (!resp.ok) {
      const text = await resp.text();
      tbody.innerHTML = `<tr><td colspan="6" class="muted">Error: ${text}</td></tr>`;
      statusEl.textContent = "Failed.";
      return;
    }

    const payload = await resp.json();
    const plans = payload?.data || [];
    statusEl.textContent = `Loaded ${plans.length} plan(s).`;

    if (plans.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted">No plans found.</td></tr>`;
      return;
    }

    tbody.innerHTML = "";
    for (const p of plans) {
      const key = p.key || "";
      const summary = p.summary || "";
      const jiraCount = Array.isArray(p.jira_keys) ? p.jira_keys.length : 0;
      const testCount = Array.isArray(p.tests) ? p.tests.length : 0;
      const overlayStatus = p.overlay_status || "NOT_ANALYZED";

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><code>${escapeHtml(key)}</code></td>
        <td>${escapeHtml(summary)}</td>
        <td>${jiraCount}</td>
        <td>${testCount}</td>
        <td>${badgeHtml(overlayStatus)}</td>
        <td><button onclick="openPlan('${escapeJs(key)}')">View</button></td>
      `;
      tbody.appendChild(tr);
    }
  } catch (e) {
    console.error(e);
    tbody.innerHTML = `<tr><td colspan="6" class="muted">Error loading plans.</td></tr>`;
    statusEl.textContent = "Failed.";
  }
}

// ─────────────────────────────────────────────────────────────
// Common helpers
// ─────────────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeJs(s) {
  return String(s || "").replaceAll("\\", "\\\\").replaceAll("'", "\\'");
}

function prettyJson(obj) {
  return JSON.stringify(obj, null, 2);
}

// ─────────────────────────────────────────────────────────────
// Overlays cache + select builders
// ─────────────────────────────────────────────────────────────
let OVERLAYS_CACHE = {
  loaded: false,
  list: [],    // [{name, kind, label}]
  byName: {},  // name -> overlay
};

async function loadOverlaysCache() {
  try {
    const r = await fetch(`${API_BASE}/api/test-plans/overlays`);
    if (!r.ok) throw new Error(await r.text());
    const payload = await r.json();
    const overlays = Array.isArray(payload?.data) ? payload.data : [];

    OVERLAYS_CACHE.list = overlays;
    OVERLAYS_CACHE.byName = {};
    overlays.forEach((o) => {
      if (o?.name) OVERLAYS_CACHE.byName[o.name] = o;
    });
    OVERLAYS_CACHE.loaded = true;
  } catch (e) {
    console.warn("Failed to load overlays cache:", e);
    OVERLAYS_CACHE.loaded = false;
    OVERLAYS_CACHE.list = [];
    OVERLAYS_CACHE.byName = {};
  }
}

function isRunOverlay(name) {
  const o = OVERLAYS_CACHE.byName[name];
  return (o?.kind || "").toLowerCase() === "run";
}

function isFileOverlay(name) {
  const o = OVERLAYS_CACHE.byName[name];
  return (o?.kind || "").toLowerCase() === "file";
}

function listFileOverlays() {
  return OVERLAYS_CACHE.list.filter((o) => (o?.kind || "").toLowerCase() === "file");
}

async function loadOverlaysIntoMainSelect() {
  const topSel = document.getElementById("overlaySelect");
  if (!topSel) return;

  if (!OVERLAYS_CACHE.loaded) await loadOverlaysCache();

  const current = (topSel.value || "").trim();
  topSel.innerHTML = "";

  const none = document.createElement("option");
  none.value = "";
  none.textContent = "none";
  topSel.appendChild(none);

  for (const o of OVERLAYS_CACHE.list) {
    const opt = document.createElement("option");
    opt.value = o.name;
    opt.textContent = o.label || o.name;
    topSel.appendChild(opt);
  }

  topSel.value = OVERLAYS_CACHE.byName[current] ? current : "";
}

function rebuildDrawerOverlaySelectForIssue(usKey) {
  const drawerSel = document.getElementById("drawerOverlaySelect");
  const wrap = document.getElementById("drawerOverlayWrap");
  if (!drawerSel || !wrap) return;

  const runName = usKey && OVERLAYS_CACHE.byName[usKey] && isRunOverlay(usKey) ? usKey : "";

  if (!runName) {
    wrap.style.display = "none";
    INSPECTOR_STATE.overlay = "";
    return;
  }

  wrap.style.display = "inline-block";
  drawerSel.innerHTML = "";

  const opt = document.createElement("option");
  opt.value = runName;
  opt.textContent = `Run: ${OVERLAYS_CACHE.byName[runName].label || runName}`;
  drawerSel.appendChild(opt);

  drawerSel.value = runName;
  INSPECTOR_STATE.overlay = runName;
}

function rebuildDrawerOverlaySelectForPlan(selectedOverlay) {
  const drawerSel = document.getElementById("drawerOverlaySelect");
  const wrap = document.getElementById("drawerOverlayWrap");
  if (!drawerSel || !wrap) return;

  wrap.style.display = "inline-block";
  drawerSel.innerHTML = "";

  const none = document.createElement("option");
  none.value = "";
  none.textContent = "Overlay: none";
  drawerSel.appendChild(none);

  for (const o of OVERLAYS_CACHE.list) {
    const opt = document.createElement("option");
    opt.value = o.name;
    opt.textContent = `Overlay: ${o.label || o.name}`;
    drawerSel.appendChild(opt);
  }

  drawerSel.value = OVERLAYS_CACHE.byName[selectedOverlay] ? selectedOverlay : "";
  INSPECTOR_STATE.overlay = (drawerSel.value || "").trim();
}

// ─────────────────────────────────────────────────────────────
// Inspector Drawer state
// ─────────────────────────────────────────────────────────────
let INSPECTOR_STATE = {
  jiraKey: null,
  planKey: null,
  tab: "jira",
  overlay: "",
  cache: { jira: null, xray: null, bitbucket: null, prompt: null, plan: null },
};

function isInspectorOpen() {
  const drawer = document.getElementById("drawer");
  return drawer?.classList.contains("open");
}

function openDrawer() {
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawerBackdrop");
  drawer.classList.add("open");
  backdrop.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function closeInspector() {
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawerBackdrop");
  drawer.classList.remove("open");
  backdrop.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
}

function setTabVisibilityForMode(mode) {
  const show = (id, visible) => {
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? "inline-flex" : "none";
  };

  if (mode === "issue") {
    show("tabJiraBtn", true);
    show("tabXrayBtn", true);
    show("tabBitbucketBtn", true);
    show("tabPromptBtn", true);
    show("tabPlanBtn", false);
  } else {
    show("tabJiraBtn", false);
    show("tabXrayBtn", false);
    show("tabBitbucketBtn", false);
    show("tabPromptBtn", false);
    show("tabPlanBtn", true);
  }
}

function setInspectorTab(tab) {
  INSPECTOR_STATE.tab = tab;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    const t = btn.getAttribute("data-tab");
    btn.classList.toggle("active", t === tab);
  });
  updateEnrichButtonVisibility();
  renderInspector();
}

async function refreshInspector() {
  if (INSPECTOR_MODE === "issue" && INSPECTOR_STATE.jiraKey) {
    INSPECTOR_STATE.cache = { jira: null, xray: null, bitbucket: null, prompt: null, plan: null };
    await loadInspectorIssue(INSPECTOR_STATE.jiraKey);
    return;
  }
  if (INSPECTOR_MODE === "plan" && INSPECTOR_STATE.planKey) {
    INSPECTOR_STATE.cache.plan = null;
    await loadPlanIntoInspector(INSPECTOR_STATE.planKey);
  }
}

// ─────────────────────────────────────────────────────────────
// G1/G2 Inspector (Issue mode)
// ─────────────────────────────────────────────────────────────
function openInspector() {
  const jiraKey = getSelectedUsKey();
  if (!jiraKey) {
    const statusEl = document.getElementById("status");
    statusEl.textContent = "Select a Jira key first, then open the inspector.";
    statusEl.style.color = "red";
    return;
  }

  INSPECTOR_MODE = "issue";
  setInspectorRoleLabel("G1/G2 – Issue Generator");

  INSPECTOR_STATE.planKey = null;
  INSPECTOR_STATE.jiraKey = jiraKey;
  INSPECTOR_STATE.tab = "jira";
  INSPECTOR_STATE.cache = { jira: null, xray: null, bitbucket: null, prompt: null, plan: null };

  setTabVisibilityForMode("issue");
  openDrawer();
  setInspectorKeyLabel(jiraKey);

  rebuildDrawerOverlaySelectForIssue(jiraKey);

  loadInspectorIssue(jiraKey);
}

async function loadInspectorIssue(jiraKey) {
  const contentEl = document.getElementById("inspectorContent");
  if (contentEl) {
    contentEl.classList.remove("inspector-html");
    contentEl.textContent = "Loading...";
  }

  try {
    if (!INSPECTOR_STATE.cache.jira) {
      const r = await fetch(`${API_BASE}/api/jira/issue/${encodeURIComponent(jiraKey)}`);
      INSPECTOR_STATE.cache.jira = await r.json();
    }
    if (!INSPECTOR_STATE.cache.xray) {
      const r = await fetch(`${API_BASE}/api/xray/preview/${encodeURIComponent(jiraKey)}`);
      INSPECTOR_STATE.cache.xray = await r.json();
    }
    if (!INSPECTOR_STATE.cache.bitbucket) {
      const r = await fetch(`${API_BASE}/api/bitbucket/changes/${encodeURIComponent(jiraKey)}`);
      INSPECTOR_STATE.cache.bitbucket = await r.json();
    }
    if (!INSPECTOR_STATE.cache.prompt) {
      const r = await fetch(`${API_BASE}/api/llm/prompt/${encodeURIComponent(jiraKey)}`);
      INSPECTOR_STATE.cache.prompt = await r.json();
    }

    renderInspector();
  } catch (e) {
    console.error(e);
    if (contentEl) contentEl.textContent = "Error loading inspector data: " + (e?.message || String(e));
  }
}

// ─────────────────────────────────────────────────────────────
// G4 Inspector (Plan mode)
// ─────────────────────────────────────────────────────────────
async function openPlan(planKey) {
  if (!planKey) return;

  INSPECTOR_MODE = "plan";
  setInspectorRoleLabel("G4 – Test Plans Governance (T0+)");

  INSPECTOR_STATE.planKey = planKey;
  INSPECTOR_STATE.jiraKey = null;
  INSPECTOR_STATE.tab = "plan";
  INSPECTOR_STATE.cache = { jira: null, xray: null, bitbucket: null, prompt: null, plan: null };

  setTabVisibilityForMode("plan");
  openDrawer();
  setInspectorKeyLabel(planKey);

  if (!OVERLAYS_CACHE.loaded) await loadOverlaysCache();

  const mainOverlay = (document.getElementById("overlaySelect")?.value || "").trim();
  rebuildDrawerOverlaySelectForPlan(mainOverlay);

  setInspectorTab("plan");
  await loadPlanIntoInspector(planKey);
}

async function onDrawerOverlayChange() {
  const drawerSel = document.getElementById("drawerOverlaySelect");
  const overlay = (drawerSel?.value || "").trim();
  INSPECTOR_STATE.overlay = overlay;

  const topSel = document.getElementById("overlaySelect");
  if (topSel) topSel.value = overlay;

  updateEnrichButtonVisibility();

  if (INSPECTOR_MODE === "plan" && INSPECTOR_STATE.planKey) {
    INSPECTOR_STATE.cache.plan = null;
    await loadPlanIntoInspector(INSPECTOR_STATE.planKey);
    loadTestPlans();
  }
}

function updateEnrichButtonVisibility() {
  const btn = document.getElementById("enrichBtn");
  if (!btn) return;

  if (!(INSPECTOR_MODE === "plan" && INSPECTOR_STATE.planKey && INSPECTOR_STATE.tab === "plan")) {
    btn.style.display = "none";
    return;
  }

  const overlay = (INSPECTOR_STATE.overlay || "").trim();
  if (!overlay) {
    btn.style.display = "none";
    return;
  }

  if (isRunOverlay(overlay)) {
    btn.style.display = "none";
    return;
  }

  btn.style.display = "inline-block";
}

async function enrichCurrentPlan() {
  const planKey = INSPECTOR_STATE.planKey;
  if (!planKey) return;

  const overlay = (INSPECTOR_STATE.overlay || "").trim();
  if (!overlay) return;

  if (isRunOverlay(overlay)) {
    window.alert(
      "This overlay is computed from a G1/G2 run (Pattern A).\n" +
      "It is read-only. Select a file overlay to persist G4 governance."
    );
    return;
  }

  const contentEl = document.getElementById("inspectorContent");
  if (contentEl) {
    contentEl.classList.add("inspector-html");
    contentEl.innerHTML = "<div class='muted'>Enriching plan...</div>";
  }

  try {
    const url = `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}/enrich?overlay=${encodeURIComponent(overlay)}`;
    const resp = await fetch(url, { method: "POST" });
    if (!resp.ok) {
      const text = await resp.text();
      window.alert(`Enrich failed (${resp.status}): ${text}`);
      return;
    }

    INSPECTOR_STATE.cache.plan = await resp.json();
    renderInspector();
    updateEnrichButtonVisibility();
    loadTestPlans();
  } catch (e) {
    console.error(e);
    window.alert("Enrich failed: " + (e?.message || String(e)));
  }
}

async function loadPlanIntoInspector(planKey) {
  const contentEl = document.getElementById("inspectorContent");
  if (contentEl) {
    contentEl.classList.add("inspector-html");
    contentEl.innerHTML = "<div class='muted'>Loading plan...</div>";
  }

  try {
    const overlay = (INSPECTOR_STATE.overlay || "").trim();
    const url = overlay
      ? `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}?overlay=${encodeURIComponent(overlay)}`
      : `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}`;

    const resp = await fetch(url);
    if (!resp.ok) {
      const text = await resp.text();
      if (contentEl) contentEl.innerHTML = `<div class="muted">Error ${resp.status}: ${escapeHtml(text)}</div>`;
      return;
    }

    INSPECTOR_STATE.cache.plan = await resp.json();
    renderInspector();
    updateEnrichButtonVisibility();
  } catch (e) {
    console.error(e);
    if (contentEl) contentEl.innerHTML = `<div class="muted">Error loading plan: ${escapeHtml(e?.message || String(e))}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────
// T0+ Actions (Apply run → file overlay) + Decisions
// ─────────────────────────────────────────────────────────────
async function applyRunToFileOverlay() {
  const planKey = INSPECTOR_STATE.planKey;
  const currentOverlay = (INSPECTOR_STATE.overlay || "").trim(); // should be run overlay in this action
  const targetSel = document.getElementById("applyTargetOverlay");
  const targetOverlay = (targetSel?.value || "promptA").trim();

  if (!planKey || !currentOverlay || !isRunOverlay(currentOverlay)) {
    window.alert("Apply is only available when a run overlay is selected.");
    return;
  }
  if (!targetOverlay || !isFileOverlay(targetOverlay)) {
    window.alert("Please select a FILE overlay (e.g. promptA) as target.");
    return;
  }

  const contentEl = document.getElementById("inspectorContent");
  if (contentEl) contentEl.innerHTML = "<div class='muted'>Applying run candidates into file overlay...</div>";

  try {
    const url =
      `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}/apply-run` +
      `?run=${encodeURIComponent(currentOverlay)}` +
      `&overlay=${encodeURIComponent(targetOverlay)}`;

    const resp = await fetch(url, { method: "POST" });
    if (!resp.ok) {
      const text = await resp.text();
      window.alert(`Apply failed (${resp.status}): ${text}`);
      return;
    }

    // Switch inspector to the target FILE overlay so G4 can decide
    INSPECTOR_STATE.overlay = targetOverlay;

    // Sync selects
    const topSel = document.getElementById("overlaySelect");
    if (topSel) topSel.value = targetOverlay;

    const drawerSel = document.getElementById("drawerOverlaySelect");
    if (drawerSel) drawerSel.value = targetOverlay;

    INSPECTOR_STATE.cache.plan = await resp.json();
    renderInspector();
    updateEnrichButtonVisibility();
    loadTestPlans();
  } catch (e) {
    console.error(e);
    window.alert("Apply failed: " + (e?.message || String(e)));
  }
}

async function setCandidateDecision(candidateKey, decision) {
  const planKey = INSPECTOR_STATE.planKey;
  const overlay = (INSPECTOR_STATE.overlay || "").trim();

  if (!planKey || !overlay || !isFileOverlay(overlay)) {
    window.alert("Decisions are only available on FILE overlays.");
    return;
  }

  try {
    const url =
      `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}/candidates/decision` +
      `?overlay=${encodeURIComponent(overlay)}`;

    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        candidate_key: candidateKey,
        decision: decision,
        rationale: "",
      }),
    });

    if (!resp.ok) {
      const text = await resp.text();
      window.alert(`Decision failed (${resp.status}): ${text}`);
      return;
    }

    INSPECTOR_STATE.cache.plan = await resp.json();
    renderInspector();
    loadTestPlans();
  } catch (e) {
    console.error(e);
    window.alert("Decision failed: " + (e?.message || String(e)));
  }
}

// ─────────────────────────────────────────────────────────────
// Render Inspector
// ─────────────────────────────────────────────────────────────
function renderInspector() {
  const el = document.getElementById("inspectorContent");
  const tab = INSPECTOR_STATE.tab;
  const cache = INSPECTOR_STATE.cache;

  if (!el) return;

  // Issue mode tabs (text)
  if (INSPECTOR_MODE === "issue") {
    el.classList.remove("inspector-html");

    if (tab === "jira") return (el.textContent = prettyJson(cache.jira));

    if (tab === "xray") {
      const payload = cache.xray || {};
      const d = payload?.data || {};
      const meta = payload?.meta || {};
      const counts = meta?.counts || {};
      const prov = d?.provenance || {};

      const baseline = Array.isArray(d.baseline_tests) ? d.baseline_tests : [];
      const candidates = Array.isArray(d.candidate_tests) ? d.candidate_tests : [];
      const consolidated = Array.isArray(d.consolidated_tests) ? d.consolidated_tests : [];

      const lines = [];
      lines.push(`=== XRAY PREVIEW for ${d.jira_key || INSPECTOR_STATE.jiraKey || ""} ===`);
      lines.push(`Baseline tests: ${counts.baseline ?? baseline.length}`);
      lines.push(`AI candidates (from exported run): ${counts.candidates ?? candidates.length}`);
      lines.push(`Consolidated (preview): ${counts.consolidated ?? consolidated.length}`);
      lines.push("");

      if (!prov?.run_present) {
        lines.push("No exported run found for this issue.");
        lines.push("Export a run from Issue Generator to preview AI candidate tests here.");
        lines.push("");
      } else {
        const ph = prov?.prompt_hash ? String(prov.prompt_hash).slice(0, 18) + "…" : "";
        lines.push(`Run provenance: generated_at=${prov.generated_at || "?"}  prompt_hash=${ph}`);
        lines.push("");
      }

      lines.push("--- BASELINE TESTS ---");
      if (baseline.length === 0) lines.push("(none)");
      else baseline.forEach((t) => lines.push(`- ${t?.key || "?"}: ${t?.summary || ""}`));

      lines.push("");
      lines.push("--- AI CANDIDATE TESTS (PREVIEW) ---");
      if (candidates.length === 0) lines.push("(none)");
      else {
        candidates.forEach((t) => {
          const tags = Array.isArray(t?.tags) ? ` [${t.tags.join(", ")}]` : "";
          lines.push(`- ${t?.key || "?"}: ${t?.summary || ""}${tags}`);
        });
      }

      lines.push("");
      lines.push("--- CONSOLIDATED (PREVIEW) ---");
      if (consolidated.length === 0) lines.push("(none)");
      else consolidated.forEach((t) => lines.push(`- ${t?.key || "?"}: ${t?.summary || ""}`));

      el.textContent = lines.join("\n");
      return;
    }

    if (tab === "bitbucket") return (el.textContent = prettyJson(cache.bitbucket));

    if (tab === "prompt") {
      const payload = cache.prompt || {};
      const d = payload?.data || {};
      el.textContent = [
        "=== PROMPT TRACEABILITY (run-linked) ===",
        "",
        `prompt_hash: ${d.prompt_hash || ""}`,
        `schema_id:   ${d.schema_id || ""}`,
        "",
        "=== SYSTEM PROMPT ===",
        d.system_prompt || "",
        "",
        "=== USER PROMPT ===",
        d.user_prompt || "",
      ].join("\n");
      return;
    }

    el.textContent = "Select a tab.";
    return;
  }

  // Plan mode (HTML)
  if (INSPECTOR_MODE === "plan") {
    el.classList.add("inspector-html");
    if (tab !== "plan") {
      el.innerHTML = "<div class='muted'>Select Plan tab.</div>";
      return;
    }
    el.innerHTML = renderPlanHtml(cache.plan);
    return;
  }

  el.textContent = "Unknown mode.";
}

function pillForDecision(dec) {
  const d = String(dec || "PENDING").toUpperCase();
  if (d === "ACCEPTED") return `<span class="pill pill-accept">ACCEPTED</span>`;
  if (d === "REJECTED") return `<span class="pill pill-reject">REJECTED</span>`;
  return `<span class="pill pill-pending">PENDING</span>`;
}

function renderPlanHtml(planPayload) {
  if (!planPayload) return `<div class="muted">No plan loaded.</div>`;

  const data = planPayload?.data || {};
  const meta = planPayload?.meta || {};

  const overlayName = meta?.overlay || "";
  const overlayKind = meta?.overlay_kind || "";
  const gov = data?.governance || {};
  const ov = data?.overlay || {};

  const baselineTests = Array.isArray(data?.tests) ? data.tests : [];
  const jiraKeys = Array.isArray(data?.jira_keys) ? data.jira_keys : [];

  const header = `
    <div class="inspector-block">
      <div class="inspector-title">TEST PLAN: ${escapeHtml(data?.key || "")}</div>
      <div class="inspector-subtitle">${escapeHtml(data?.summary || "")}</div>
      <div style="margin-top:0.6rem;">
        <span class="pill pill-meta">Overlay: ${escapeHtml(overlayName || "(none)")}</span>
        <span class="pill pill-meta">kind=${escapeHtml(overlayKind || "(none)")}</span>
        <span class="pill pill-meta">Governance: ${escapeHtml(gov?.status || "NOT_ANALYZED")}</span>
        <span class="pill pill-meta">source=${escapeHtml(gov?.source || "baseline")}</span>
      </div>
      ${(Array.isArray(gov?.signals) && gov.signals.length)
        ? `<div class="muted" style="margin-top:0.5rem;">Signals: ${escapeHtml(gov.signals.join(" | "))}</div>`
        : `<div class="muted" style="margin-top:0.5rem;">Signals: (none)</div>`
      }
      <div style="margin-top:0.75rem;" class="muted">
        Jira keys (${jiraKeys.length}): ${escapeHtml(jiraKeys.join(", ") || "(none)")}
      </div>
    </div>
  `;

  const baseline = `
    <div class="inspector-block">
      <div class="inspector-section">BASELINE (read-only)</div>
      <div class="muted">Baseline tests in plan (${baselineTests.length}):</div>
      <ul class="inspector-list">
        ${baselineTests.length ? baselineTests.map((t) => `<li><code>${escapeHtml(t)}</code></li>`).join("") : `<li class="muted">(none)</li>`}
      </ul>
    </div>
  `;

  // Run overlay (read-only) + Apply button (T0+)
  if (overlayKind === "run") {
    const candidates = Array.isArray(ov?.candidate_tests) ? ov.candidate_tests : [];

    const fileOverlays = listFileOverlays();
    const targetOptions =
      fileOverlays.length
        ? fileOverlays.map((o) => `<option value="${escapeHtml(o.name)}">${escapeHtml(o.label || o.name)}</option>`).join("")
        : `<option value="promptA">promptA (file)</option>`;

    const applyBlock = `
      <div class="inspector-block">
        <div class="inspector-section">AI CANDIDATE TESTS (from run, read-only)</div>
        <div class="muted">These are preview candidates computed from the exported run. No decisions are persisted here.</div>

        <div style="margin-top:0.75rem; display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center;">
          <span class="pill pill-meta">T0+ Action</span>
          <label for="applyTargetOverlay" class="muted" style="font-weight:600;">Apply into file overlay:</label>
          <select id="applyTargetOverlay" style="min-width:220px;">${targetOptions}</select>
          <button onclick="applyRunToFileOverlay()">Apply run → file overlay</button>
        </div>
      </div>
    `;

    const list = `
      <div class="inspector-block">
        <div class="candidate-grid">
          ${candidates.length ? candidates.map((c) => {
            return `
              <div class="candidate-card">
                <div class="candidate-head">
                  <div><code>${escapeHtml(c.candidate_key || "")}</code></div>
                  <div class="candidate-meta">
                    <span class="pill pill-meta">${escapeHtml(c.priority || "MEDIUM")}</span>
                    <span class="pill pill-meta">${escapeHtml(c.type || "functional")}</span>
                  </div>
                </div>
                <div class="candidate-title">${escapeHtml(c.title || "")}</div>
                ${c.mapped_existing_test_key ? `<div class="muted">mapped_to: <code>${escapeHtml(c.mapped_existing_test_key)}</code></div>` : `<div class="muted">mapped_to: (none)</div>`}
              </div>
            `;
          }).join("") : `<div class="muted">(none)</div>`}
        </div>
      </div>
    `;

    return header + baseline + applyBlock + list;
  }

  // File overlay (persisted) with decisions
  if (overlayKind === "file") {
    const aiCandidates = Array.isArray(ov?.ai_candidates) ? ov.ai_candidates : [];

    const execTests = Array.isArray(ov?.existing_tests_to_execute) ? ov.existing_tests_to_execute : [];
    const skipTests = Array.isArray(ov?.existing_tests_to_skip) ? ov.existing_tests_to_skip : [];
    const newTests = Array.isArray(ov?.new_tests_to_create) ? ov.new_tests_to_create : [];

    const g4EnrichHint = `
      <div class="inspector-block">
        <div class="inspector-section">G4 overlay (persisted)</div>
        <div class="muted">
          Use <b>Enrich</b> to generate baseline governance in this file overlay.
          Use <b>Accept/Reject</b> on AI candidates (applied via T0+) to make decisions visible.
        </div>
      </div>
    `;

    const aiBlock = `
      <div class="inspector-block">
        <div class="inspector-section">AI CANDIDATE TESTS (persisted in file overlay)</div>
        <div class="muted">Decisions are persisted here (T0+). Overlay file is the source of truth for G4 governance.</div>

        <div class="candidate-grid">
          ${aiCandidates.length ? aiCandidates.map((c) => {
            const dec = String(c.decision || "PENDING").toUpperCase();
            const cardClass = dec === "ACCEPTED" ? "candidate-accept" : dec === "REJECTED" ? "candidate-reject" : "";
            return `
              <div class="candidate-card ${cardClass}">
                <div class="candidate-head">
                  <div><code>${escapeHtml(c.candidate_key || "")}</code></div>
                  <div class="candidate-meta">
                    ${pillForDecision(c.decision)}
                    <span class="pill pill-meta">${escapeHtml(c.priority || "MEDIUM")}</span>
                    <span class="pill pill-meta">${escapeHtml(c.type || "functional")}</span>
                  </div>
                </div>

                <div class="candidate-title">${escapeHtml(c.title || "")}</div>

                ${c.mapped_existing_test_key ? `<div class="muted">mapped_to: <code>${escapeHtml(c.mapped_existing_test_key)}</code></div>` : `<div class="muted">mapped_to: (none)</div>`}

                <div class="candidate-actions" style="margin-top:0.75rem;">
                  <button onclick="setCandidateDecision('${escapeJs(c.candidate_key || "")}', 'ACCEPTED')">Accept</button>
                  <button onclick="setCandidateDecision('${escapeJs(c.candidate_key || "")}', 'REJECTED')">Reject</button>
                  <button onclick="setCandidateDecision('${escapeJs(c.candidate_key || "")}', 'PENDING')">Reset</button>
                </div>
              </div>
            `;
          }).join("") : `<div class="muted">(No AI candidates in this overlay yet. Use run overlay + Apply to import.)</div>`}
        </div>
      </div>
    `;

    const g4Lists = `
      <div class="inspector-block">
        <div class="inspector-section">EXISTING TESTS: TO EXECUTE</div>
        ${execTests.length ? `<ul class="inspector-list">${execTests.map((t) => `<li><code>${escapeHtml(t)}</code></li>`).join("")}</ul>` : `<div class="muted">(none)</div>`}
      </div>

      <div class="inspector-block">
        <div class="inspector-section">EXISTING TESTS: TO SKIP</div>
        ${skipTests.length ? `<ul class="inspector-list">${skipTests.map((t) => `<li>${escapeHtml(t?.test_key || t?.key || JSON.stringify(t))}</li>`).join("")}</ul>` : `<div class="muted">(none)</div>`}
      </div>

      <div class="inspector-block">
        <div class="inspector-section">NEW TESTS: TO CREATE (governance target)</div>
        ${newTests.length ? `
          <div class="candidate-grid">
            ${newTests.map((t, i) => `
              <div class="candidate-card">
                <div class="candidate-head">
                  <div><b>${i + 1}.</b> ${escapeHtml(t?.jira_key || "")}</div>
                  <div class="candidate-meta">
                    <span class="pill pill-meta">${escapeHtml(t?.priority || "MEDIUM")}</span>
                  </div>
                </div>
                <div class="candidate-title">${escapeHtml(t?.title || "")}</div>
                ${Array.isArray(t?.tags) && t.tags.length ? `<div class="muted">tags: ${escapeHtml(t.tags.join(", "))}</div>` : ""}
                ${t?.given ? `<div class="candidate-steps"><b>GIVEN</b>: ${escapeHtml(t.given)}</div>` : ""}
                ${t?.when ? `<div class="candidate-steps"><b>WHEN</b>: ${escapeHtml(t.when)}</div>` : ""}
                ${t?.then ? `<div class="candidate-steps"><b>THEN</b>: ${escapeHtml(t.then)}</div>` : ""}
              </div>
            `).join("")}
          </div>
        ` : `<div class="muted">(none)</div>`}
      </div>
    `;

    return header + baseline + g4EnrichHint + aiBlock + g4Lists;
  }

  return header + baseline + `<div class="inspector-block"><div class="muted">(No overlay selected.)</div></div>`;
}

// ─────────────────────────────────────────────────────────────
// Copy
// ─────────────────────────────────────────────────────────────
async function copyInspector() {
  const el = document.getElementById("inspectorContent");
  const text = el?.innerText || el?.textContent || "";
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const tmp = document.createElement("textarea");
    tmp.value = text;
    document.body.appendChild(tmp);
    tmp.select();
    document.execCommand("copy");
    document.body.removeChild(tmp);
  }
}

// ─────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await resolveApiBase();

  await loadJiraKeysIntoCombo();
  await loadOverlaysCache();
  await loadOverlaysIntoMainSelect();

  const sel = document.getElementById("jiraKeySelect");
  if (sel) {
    sel.addEventListener("change", async () => {
      const key = getSelectedUsKey();

      if (ISSUE_DIRTY && LAST_GENERATED?.jira_key && key && key !== LAST_GENERATED.jira_key) {
        const doExport = window.confirm("Your AI-assisted Test Plan has not been exported. Do you want to export it now?");
        if (doExport) await exportRun();
        ISSUE_DIRTY = false;
        LAST_GENERATED = null;
        setExportButtonState();
      }

      if (isInspectorOpen() && INSPECTOR_MODE === "issue" && key) {
        INSPECTOR_STATE.jiraKey = key;
        INSPECTOR_STATE.cache = { jira: null, xray: null, bitbucket: null, prompt: null, plan: null };
        setInspectorKeyLabel(key);
        rebuildDrawerOverlaySelectForIssue(key);
        await loadInspectorIssue(key);
      }
    });
  }
});
