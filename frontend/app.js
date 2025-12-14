const API_BASE = "http://localhost:8000";

// ─────────────────────────────────────────────────────────────
// Top navigation (Issue / Plans)
// ─────────────────────────────────────────────────────────────
let TOP_PANEL = "issue"; // "issue" | "plans"

function setTopPanel(panel) {
  TOP_PANEL = panel;

  document.querySelectorAll(".top-tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-panel") === panel);
  });

  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  const el = document.getElementById(`panel-${panel}`);
  if (el) el.classList.add("active");

  // When switching to Plans, load list once.
  if (panel === "plans") loadTestPlans();
}

// ─────────────────────────────────────────────────────────────
// Generation (Issue Generator)
// ─────────────────────────────────────────────────────────────
async function generate() {
  const selectEl = document.getElementById("jiraKeySelect");
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const jsonEl = document.getElementById("jsonResult");

  const jiraKey = (selectEl?.value || "").trim();

  resultEl.textContent = "";
  jsonEl.textContent = "";

  if (!jiraKey) {
    statusEl.textContent = "Please select a Jira key.";
    statusEl.style.color = "red";
    return;
  }

  statusEl.textContent = `Generating test plan for ${jiraKey}...`;
  statusEl.style.color = "black";

  try {
    const resp = await fetch(`${API_BASE}/agent/test-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jira_key: jiraKey }),
    });

    if (!resp.ok) {
      const text = await resp.text();
      statusEl.textContent = `Error ${resp.status}: ${text}`;
      statusEl.style.color = "red";
      return;
    }

    const data = await resp.json();
    statusEl.textContent = "Done.";
    statusEl.style.color = "green";

    resultEl.textContent = data.markdown || "";
    jsonEl.textContent = JSON.stringify(data.suggestions || [], null, 2);

    if (isInspectorOpen()) {
      await loadInspector(jiraKey);
    }
  } catch (e) {
    console.error(e);
    statusEl.textContent = "Error calling backend: " + (e?.message || String(e));
    statusEl.style.color = "red";
  }
}

// ─────────────────────────────────────────────────────────────
// Jira Keys (combo)
// ─────────────────────────────────────────────────────────────
async function loadJiraKeysIntoCombo() {
  const sel = document.getElementById("jiraKeySelect");
  if (!sel) return;

  sel.innerHTML = `<option value="">Loading keys...</option>`;

  try {
    const resp = await fetch(`${API_BASE}/api/jira/issue-keys`);
    if (!resp.ok) {
      sel.innerHTML = `<option value="">Error loading keys</option>`;
      return;
    }

    const payload = await resp.json();
    const keys = payload?.data || [];

    sel.innerHTML = `<option value="">-- Select a Jira key --</option>`;

    for (const k of keys) {
      const opt = document.createElement("option");
      opt.value = k;
      opt.textContent = k;
      sel.appendChild(opt);
    }

    if (keys.length === 0) {
      sel.innerHTML = `<option value="">No keys found</option>`;
    }
  } catch (e) {
    console.error(e);
    sel.innerHTML = `<option value="">Error loading keys</option>`;
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
  const overlay = (overlaySel?.value || "").trim(); // "" means none
  const tbody = document.getElementById("plansTbody");
  const statusEl = document.getElementById("plansStatus");

  if (!tbody || !statusEl) return;

  tbody.innerHTML = `<tr><td colspan="6" class="muted">Loading...</td></tr>`;
  statusEl.textContent = overlay ? `Loading plans with overlay=${overlay}...` : "Loading baseline plans...";

  try {
    const url = overlay ? `${API_BASE}/api/test-plans?overlay=${encodeURIComponent(overlay)}` : `${API_BASE}/api/test-plans`;
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

// ─────────────────────────────────────────────────────────────
// Inspector Drawer (extended for Plan tab)
// ─────────────────────────────────────────────────────────────
let INSPECTOR_STATE = {
  jiraKey: null,          // for issue inspector
  planKey: null,          // for plan inspector
  tab: "jira",
  overlay: "",            // "" | "promptA" | "promptB"
  cache: { jira: null, xray: null, bitbucket: null, prompt: null, plan: null },
};

function isInspectorOpen() {
  const drawer = document.getElementById("drawer");
  return drawer?.classList.contains("open");
}

function openInspector() {
  const selectEl = document.getElementById("jiraKeySelect");
  const jiraKey = (selectEl?.value || "").trim();

  if (!jiraKey) {
    const statusEl = document.getElementById("status");
    statusEl.textContent = "Select a Jira key first, then open the inspector.";
    statusEl.style.color = "red";
    return;
  }

  INSPECTOR_STATE.planKey = null; // issue mode
  INSPECTOR_STATE.tab = "jira";
  INSPECTOR_STATE.cache.plan = null;

  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawerBackdrop");
  drawer.classList.add("open");
  backdrop.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");

  // UI: enrich button hidden in issue mode
  const enrichBtn = document.getElementById("enrichBtn");
  if (enrichBtn) enrichBtn.style.display = "none";

  // Sync drawer overlay dropdown with top overlay selection (optional)
  syncDrawerOverlayFromTop();

  loadInspector(jiraKey);
}

function closeInspector() {
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawerBackdrop");
  drawer.classList.remove("open");
  backdrop.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
}

async function refreshInspector() {
  // issue mode
  if (INSPECTOR_STATE.jiraKey && !INSPECTOR_STATE.planKey) {
    INSPECTOR_STATE.cache = { jira: null, xray: null, bitbucket: null, prompt: null, plan: null };
    await loadInspector(INSPECTOR_STATE.jiraKey);
    return;
  }
  // plan mode
  if (INSPECTOR_STATE.planKey) {
    INSPECTOR_STATE.cache.plan = null;
    await loadPlanIntoInspector(INSPECTOR_STATE.planKey);
  }
}

function setInspectorTab(tab) {
  INSPECTOR_STATE.tab = tab;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tab);
  });
  renderInspector();
}

function setInspectorKeyLabel(value) {
  const el = document.getElementById("inspectorKey");
  if (el) el.textContent = value || "—";
}

function prettyJson(obj) {
  return JSON.stringify(obj, null, 2);
}

async function loadInspector(jiraKey) {
  INSPECTOR_STATE.jiraKey = jiraKey;
  setInspectorKeyLabel(jiraKey);

  const contentEl = document.getElementById("inspectorContent");
  contentEl.textContent = "Loading...";

  try {
    if (!INSPECTOR_STATE.cache.jira) {
      const r = await fetch(`${API_BASE}/api/jira/issue/${encodeURIComponent(jiraKey)}`);
      INSPECTOR_STATE.cache.jira = await r.json();
    }
    if (!INSPECTOR_STATE.cache.xray) {
      const r = await fetch(`${API_BASE}/api/xray/tests/${encodeURIComponent(jiraKey)}`);
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
    contentEl.textContent = "Error loading inspector data: " + (e?.message || String(e));
  }
}

// ─────────────────────────────────────────────────────────────
// Plan Viewer in Drawer
// ─────────────────────────────────────────────────────────────
function syncDrawerOverlayFromTop() {
  const drawerSel = document.getElementById("drawerOverlaySelect");
  const topSel = document.getElementById("overlaySelect");

  const topOverlay = (topSel?.value || "").trim();
  INSPECTOR_STATE.overlay = topOverlay;

  if (drawerSel) {
    drawerSel.value = topOverlay || "";
  }
}

function onDrawerOverlayChange() {
  const drawerSel = document.getElementById("drawerOverlaySelect");
  INSPECTOR_STATE.overlay = (drawerSel?.value || "").trim();

  // If plan mode is active, reload plan details with new overlay.
  if (INSPECTOR_STATE.planKey) {
    INSPECTOR_STATE.cache.plan = null;
    loadPlanIntoInspector(INSPECTOR_STATE.planKey);
  }
}

async function openPlan(planKey) {
  if (!planKey) return;

  INSPECTOR_STATE.planKey = planKey;
  INSPECTOR_STATE.jiraKey = null; // plan mode
  INSPECTOR_STATE.tab = "plan";
  INSPECTOR_STATE.cache.plan = null;

  // Open drawer
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawerBackdrop");
  drawer.classList.add("open");
  backdrop.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");

  // Activate tab "plan"
  setInspectorTab("plan");
  setInspectorKeyLabel(planKey);

  // Show enrich button in plan mode
  const enrichBtn = document.getElementById("enrichBtn");
  if (enrichBtn) enrichBtn.style.display = "inline-block";

  // Sync overlay dropdown with top overlay selection
  syncDrawerOverlayFromTop();

  await loadPlanIntoInspector(planKey);
}

async function loadPlanIntoInspector(planKey) {
  const contentEl = document.getElementById("inspectorContent");
  contentEl.textContent = "Loading plan...";

  try {
    const overlay = (INSPECTOR_STATE.overlay || "").trim();
    const url = overlay
      ? `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}?overlay=${encodeURIComponent(overlay)}`
      : `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}`;

    const resp = await fetch(url);
    if (!resp.ok) {
      const text = await resp.text();
      contentEl.textContent = `Error ${resp.status}: ${text}`;
      return;
    }

    INSPECTOR_STATE.cache.plan = await resp.json();
    renderInspector();
  } catch (e) {
    console.error(e);
    contentEl.textContent = "Error loading plan: " + (e?.message || String(e));
  }
}

async function enrichCurrentPlan() {
  const planKey = INSPECTOR_STATE.planKey;
  if (!planKey) return;

  const overlay = (INSPECTOR_STATE.overlay || "").trim() || "promptA";
  const contentEl = document.getElementById("inspectorContent");
  contentEl.textContent = `Enriching ${planKey} with overlay=${overlay}...`;

  try {
    const url = `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}/enrich?overlay=${encodeURIComponent(overlay)}`;
    const resp = await fetch(url, { method: "POST" });
    if (!resp.ok) {
      const text = await resp.text();
      contentEl.textContent = `Error ${resp.status}: ${text}`;
      return;
    }

    // Refresh plan + list
    INSPECTOR_STATE.cache.plan = await resp.json();
    renderInspector();
    await loadTestPlans();
  } catch (e) {
    console.error(e);
    contentEl.textContent = "Error enriching plan: " + (e?.message || String(e));
  }
}

// ─────────────────────────────────────────────────────────────
// Inspector rendering
// ─────────────────────────────────────────────────────────────
function renderInspector() {
  const el = document.getElementById("inspectorContent");
  const tab = INSPECTOR_STATE.tab;

  // Plan tab
  if (tab === "plan") {
    const payload = INSPECTOR_STATE.cache.plan;
    if (!payload) {
      el.textContent = "No plan loaded.";
      return;
    }

    // We render a readable summary + raw JSON below
    const d = payload?.data || {};
    const overlayStatus = d?.overlay_status || "NOT_ANALYZED";
    const ov = d?.overlay || {};

    const lines = [];
    lines.push(`=== PLAN: ${d.key || "?"} ===`);
    lines.push(`Summary: ${d.summary || ""}`);
    lines.push(`Overlay: ${INSPECTOR_STATE.overlay || "none"} (${overlayStatus})`);
    lines.push("");
    lines.push("Jira keys:");
    (d.jira_keys || []).forEach((k) => lines.push(`- ${k}`));
    lines.push("");
    lines.push("Baseline tests:");
    (d.tests || []).forEach((t) => lines.push(`- ${t}`));

    if (ov && typeof ov === "object") {
      lines.push("");
      lines.push("=== OVERLAY ===");

      const exec = Array.isArray(ov.existing_tests_to_execute) ? ov.existing_tests_to_execute : [];
      const skip = Array.isArray(ov.existing_tests_to_skip) ? ov.existing_tests_to_skip : [];
      const create = Array.isArray(ov.new_tests_to_create) ? ov.new_tests_to_create : [];

      lines.push("");
      lines.push(`Existing tests to execute (${exec.length}):`);
      exec.forEach((t) => lines.push(`- ${t}`));

      lines.push("");
      lines.push(`Existing tests to skip (${skip.length}):`);
      skip.forEach((x) => {
        const k = x?.test_key || "?";
        const r = x?.reason || "?";
        lines.push(`- ${k} (${r})`);
      });

      lines.push("");
      lines.push(`New tests to create (${create.length}):`);
      create.forEach((x) => {
        const title = x?.title || "?";
        const prio = x?.priority || "?";
        const tags = Array.isArray(x?.tags) ? x.tags.join(", ") : "";
        lines.push(`- [${prio}] ${title}${tags ? " (tags: " + tags + ")" : ""}`);
      });
    }

    lines.push("");
    lines.push("=== RAW JSON ===");
    lines.push(prettyJson(payload));

    el.textContent = lines.join("\n");
    return;
  }

  // Issue inspector tabs (existing)
  if (!INSPECTOR_STATE.jiraKey) {
    el.textContent = "Open the inspector to load data.";
    return;
  }

  const cache = INSPECTOR_STATE.cache;
  if (tab === "jira") return (el.textContent = prettyJson(cache.jira));
  if (tab === "xray") return (el.textContent = prettyJson(cache.xray));
  if (tab === "bitbucket") return (el.textContent = prettyJson(cache.bitbucket));

  if (tab === "prompt") {
    const d = cache.prompt?.data || {};
    el.textContent = [
      "=== SYSTEM PROMPT ===",
      d.system_prompt || "",
      "",
      "=== USER PROMPT ===",
      d.user_prompt || "",
    ].join("\n");
    return;
  }

  el.textContent = "Unknown tab.";
}

async function copyInspector() {
  const el = document.getElementById("inspectorContent");
  const text = el?.textContent || "";
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const tmp = document.createElement("textarea");
    tmp.value = text;
    document.body.appendChild(tmp);
    tmp.select();
    document.execCommand("copy");
    document.body.removeChild(tmp);
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && isInspectorOpen()) closeInspector();
});

// ─────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadJiraKeysIntoCombo();

  const sel = document.getElementById("jiraKeySelect");
  if (sel) {
    sel.addEventListener("change", async () => {
      const key = (sel.value || "").trim();
      if (isInspectorOpen() && key && !INSPECTOR_STATE.planKey) {
        INSPECTOR_STATE.cache = { jira: null, xray: null, bitbucket: null, prompt: null, plan: null };
        await loadInspector(key);
      }
    });
  }
});
