// web/app.js
// -------------------------------------------------------------
// Hackathon QA Test Plan Agent (T0 / T0+)
// - G1/G2: Issue → Generate → Export run (junction)
// - G4   : Test Plans → Overlays (run/file) → Apply → Decide → Effective
// -------------------------------------------------------------

/* eslint-disable no-alert */

(() => {
  "use strict";

  // ---------------------------------------------------------------------------
  // API base resolution
  // ---------------------------------------------------------------------------
  let API_BASE = ""; // resolved at runtime

  async function resolveApiBase() {
  const origin = window.location.origin;
  const host = window.location.hostname || "127.0.0.1";

  // Probe the API endpoint, not /health
  try {
    const r = await fetch(`${origin}/api/test-plans/overlays`, { method: "GET" });
    if (r.ok) {
      API_BASE = ""; // same-origin works (proxy present)
      return;
    }
  } catch (_) {}

  // Fallback to backend port
  API_BASE = `http://${host}:8000`;
}

  // Debug helper (always current value)
  function getApiBase() {
    return API_BASE;
  }

  // ---------------------------------------------------------------------------
  // Normalizers
  // ---------------------------------------------------------------------------
  function normOverlay(v) {
    const s = String(v ?? "").trim();
    return s ? s : "";
  }

  // ---------------------------------------------------------------------------
  // DOM helpers
  // ---------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);

  function setText(id, text) {
    const el = $(id);
    if (el) el.textContent = text ?? "";
  }

  function setHtml(id, html) {
    const el = $(id);
    if (el) el.innerHTML = html ?? "";
  }

  function toggleClass(id, cls, on) {
    const el = $(id);
    if (el) el.classList.toggle(cls, !!on);
  }

  // Escapers: HTML + JS string literal
  function esc(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function escJs(s) {
    return String(s ?? "").replaceAll("\\", "\\\\").replaceAll("'", "\\'");
  }

  function prettyJson(obj) {
    return JSON.stringify(obj, null, 2);
  }

  function notifyInline(message, kind = "info") {
    const el = $("plansStatus") || $("status");
    if (!el) {
      window.alert(message);
      return;
    }
    el.textContent = message;

    // Keep legacy behavior for #status
    if (el === $("status")) {
      if (kind === "error") el.style.color = "red";
      else if (kind === "success") el.style.color = "green";
      else el.style.color = "#333";
    }
  }

  // ---------------------------------------------------------------------------
  // Global UI State
  // ---------------------------------------------------------------------------
  let TOP_PANEL = "issue";
  let LAST_GENERATED = null;
  let ISSUE_DIRTY = false;

  // Inspector role/mode:
  // - "issue" => opened from G1/G2; tabs: Jira, Xray, Bitbucket, Prompt. (no Plan)
  // - "plan"  => opened from G4; tabs: Plan (HTML) + Effective. (no Prompt)
  let INSPECTOR_MODE = "issue";

  const INSPECTOR_STATE = {
    jiraKey: null,
    planKey: null,
    tab: "jira", // jira | xray | bitbucket | prompt | plan | effective
    overlay: "",
    cache: {
      jira: null,
      xray: null,
      bitbucket: null,
      prompt: null,
      plan: null,
      effective: null,
    },
  };

  // ---------------------------------------------------------------------------
  // Overlays cache
  // ---------------------------------------------------------------------------
  const OVERLAYS_CACHE = {
    loaded: false,
    list: [], // [{name, kind, label}]
    byName: {}, // name -> overlay
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

  // ---------------------------------------------------------------------------
  // Top navigation
  // ---------------------------------------------------------------------------
  function showPanel(name) {
    TOP_PANEL = name;

    toggleClass("tab-issue", "active", name === "issue");
    toggleClass("tab-plans", "active", name === "plans");
    toggleClass("panel-issue", "active", name === "issue");
    toggleClass("panel-plans", "active", name === "plans");

    if (name === "plans") loadTestPlans();
  }

  function setExportButtonState() {
    const btn = $("exportBtn");
    if (!btn) return;
    btn.disabled = !ISSUE_DIRTY || !LAST_GENERATED?.jira_key;
  }

  function setInspectorRoleLabel(text) {
    setText("inspectorRole", text ? `(${text})` : "");
  }

  function setInspectorKeyLabel(value) {
    setText("inspectorKey", value || "—");
  }

  // ---------------------------------------------------------------------------
  // Jira keys combo
  // ---------------------------------------------------------------------------
  async function loadJiraKeysIntoCombo() {
    const sel = $("jiraKeySelect");
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
    return normOverlay($("jiraKeySelect")?.value);
  }

  // ---------------------------------------------------------------------------
  // G1/G2 — Issue Generator
  // ---------------------------------------------------------------------------
  async function generate() {
    const jira_key = getSelectedUsKey();
    const statusEl = $("status");
    const resultEl = $("result");
    const jsonEl = $("jsonResult");

    if (!jira_key) {
      if (statusEl) {
        statusEl.textContent = "Please select a Jira key (US-xxx).";
        statusEl.style.color = "red";
      }
      return;
    }

    if (statusEl) {
      statusEl.textContent = "Generating test plan with LLM...";
      statusEl.style.color = "#333";
    }
    if (resultEl) resultEl.textContent = "";
    if (jsonEl) jsonEl.textContent = "";

    try {
      const resp = await fetch(`${API_BASE}/agent/test-plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jira_key }),
      });

      if (!resp.ok) {
        const txt = await resp.text();
        if (statusEl) {
          statusEl.textContent = `Error ${resp.status}: ${txt}`;
          statusEl.style.color = "red";
        }
        return;
      }

      const payload = await resp.json();
      LAST_GENERATED = payload;
      ISSUE_DIRTY = true;
      setExportButtonState();

      if (resultEl) resultEl.textContent = payload?.markdown || "";
      if (jsonEl) jsonEl.textContent = JSON.stringify(payload?.suggestions || [], null, 2);

      if (statusEl) {
        statusEl.textContent = "Done. Review result, then export the run for G4.";
        statusEl.style.color = "green";
      }
    } catch (e) {
      console.error(e);
      if (statusEl) {
        statusEl.textContent = "Failed: " + (e?.message || String(e));
        statusEl.style.color = "red";
      }
    }
  }

  async function exportRun() {
    const statusEl = $("status");
    const jira_key = LAST_GENERATED?.jira_key;

    if (!jira_key) {
      if (statusEl) {
        statusEl.textContent = "Nothing to export (generate first).";
        statusEl.style.color = "red";
      }
      return;
    }

    if (statusEl) {
      statusEl.textContent = "Exporting run to junction...";
      statusEl.style.color = "#333";
    }

    try {
      const resp = await fetch(`${API_BASE}/api/junction/runs/${encodeURIComponent(jira_key)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(LAST_GENERATED),
      });

      if (!resp.ok) {
        const txt = await resp.text();
        if (statusEl) {
          statusEl.textContent = `Export failed ${resp.status}: ${txt}`;
          statusEl.style.color = "red";
        }
        return;
      }

      ISSUE_DIRTY = false;
      setExportButtonState();

      if (statusEl) {
        statusEl.textContent = "Run exported. XRAY preview is now able to show AI candidates.";
        statusEl.style.color = "green";
      }

      await loadOverlaysCache();
      await loadOverlaysIntoMainSelect();

      if (isInspectorOpen() && INSPECTOR_MODE === "issue") {
        rebuildDrawerOverlaySelectForIssue(getSelectedUsKey());
      }
    } catch (e) {
      console.error(e);
      if (statusEl) {
        statusEl.textContent = "Export failed: " + (e?.message || String(e));
        statusEl.style.color = "red";
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Overlays selects
  // ---------------------------------------------------------------------------
  async function loadOverlaysIntoMainSelect() {
    const topSel = $("overlaySelect");
    if (!topSel) return;

    if (!OVERLAYS_CACHE.loaded) await loadOverlaysCache();

    const current = normOverlay(topSel.value);
    topSel.innerHTML = "";

    const none = document.createElement("option");
    none.value = "";
    none.textContent = "none";
    topSel.appendChild(none);

    for (const o of OVERLAYS_CACHE.list) {
      if (!o?.name) continue;
      const opt = document.createElement("option");
      opt.value = o.name;
      opt.textContent = o.label || o.name;
      topSel.appendChild(opt);
    }

    topSel.value = OVERLAYS_CACHE.byName[current] ? current : "";
  }

  function rebuildDrawerOverlaySelectForIssue(usKey) {
    const drawerSel = $("drawerOverlaySelect");
    const wrap = $("drawerOverlayWrap");
    if (!drawerSel || !wrap) return;

    const key = normOverlay(usKey);
    const runName = key && OVERLAYS_CACHE.byName[key] && isRunOverlay(key) ? key : "";

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
    const drawerSel = $("drawerOverlaySelect");
    const wrap = $("drawerOverlayWrap");
    if (!drawerSel || !wrap) return;

    wrap.style.display = "inline-block";
    drawerSel.innerHTML = "";

    const none = document.createElement("option");
    none.value = "";
    none.textContent = "Overlay: none";
    drawerSel.appendChild(none);

    for (const o of OVERLAYS_CACHE.list) {
      if (!o?.name) continue;
      const opt = document.createElement("option");
      opt.value = o.name;
      opt.textContent = `Overlay: ${o.label || o.name}`;
      drawerSel.appendChild(opt);
    }

    const sel = normOverlay(selectedOverlay);
    drawerSel.value = OVERLAYS_CACHE.byName[sel] ? sel : "";
    INSPECTOR_STATE.overlay = normOverlay(drawerSel.value);
  }

  // ---------------------------------------------------------------------------
  // Test Plans list (G4)
  // ---------------------------------------------------------------------------
  function badgeHtml(status) {
    const s = (status || "NOT_ANALYZED").toUpperCase();
    if (s === "AUTO") return `<span class="badge green">AUTO</span>`;
    if (s === "REVIEW") return `<span class="badge orange">REVIEW</span>`;
    return `<span class="badge gray">NOT_ANALYZED</span>`;
  }

  async function loadTestPlans() {
    const overlaySel = $("overlaySelect");
    const overlay = normOverlay(overlaySel?.value);
    const tbody = $("plansTbody");
    const statusEl = $("plansStatus");

    if (!tbody || !statusEl) return;

    tbody.innerHTML = `<tr><td colspan="6" class="muted">Loading...</td></tr>`;
    statusEl.textContent = overlay ? `Loading plans with overlay=${overlay}...` : "Loading baseline plans...";

    try {
      // IMPORTANT: don't send ?overlay= (empty)
      const url = overlay
        ? `${API_BASE}/api/test-plans?overlay=${encodeURIComponent(overlay)}`
        : `${API_BASE}/api/test-plans`;

      const resp = await fetch(url);
      if (!resp.ok) {
        const text = await resp.text();
        tbody.innerHTML = `<tr><td colspan="6" class="muted">Error: ${esc(text)}</td></tr>`;
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
          <td><code>${esc(key)}</code></td>
          <td>${esc(summary)}</td>
          <td>${jiraCount}</td>
          <td>${testCount}</td>
          <td>${badgeHtml(overlayStatus)}</td>
          <td><button onclick="openPlan('${escJs(key)}')">View</button></td>
        `;
        tbody.appendChild(tr);
      }
    } catch (e) {
      console.error(e);
      tbody.innerHTML = `<tr><td colspan="6" class="muted">Error loading plans.</td></tr>`;
      statusEl.textContent = "Failed.";
    }
  }

  // ---------------------------------------------------------------------------
  // Inspector drawer helpers
  // ---------------------------------------------------------------------------
  function isInspectorOpen() {
    return $("drawer")?.classList.contains("open");
  }

  function openDrawer() {
    $("drawer")?.classList.add("open");
    $("drawerBackdrop")?.classList.add("open");
  }

  function closeInspector() {
    $("drawer")?.classList.remove("open");
    $("drawerBackdrop")?.classList.remove("open");
  }

  function setTabVisibilityForMode(mode) {
    const show = (id, visible) => {
      const el = $(id);
      if (el) el.style.display = visible ? "inline-flex" : "none";
    };

    if (mode === "issue") {
      show("tabJiraBtn", true);
      show("tabXrayBtn", true);
      show("tabBitbucketBtn", true);
      show("tabPromptBtn", true);
      show("tabPlanBtn", false);
      show("tabEffectiveBtn", false);
    } else {
      show("tabJiraBtn", false);
      show("tabXrayBtn", false);
      show("tabBitbucketBtn", false);
      show("tabPromptBtn", false);
      show("tabPlanBtn", true);
      show("tabEffectiveBtn", true);
    }
  }

  function resetInspectorCache() {
    INSPECTOR_STATE.cache = {
      jira: null,
      xray: null,
      bitbucket: null,
      prompt: null,
      plan: null,
      effective: null,
    };
  }

  function updateEnrichButtonVisibility() {
    const btn = $("enrichBtn");
    if (!btn) return;

    // Enrich only on: Plan mode + tab=plan + file overlay selected
    if (!(INSPECTOR_MODE === "plan" && INSPECTOR_STATE.planKey && INSPECTOR_STATE.tab === "plan")) {
      btn.style.display = "none";
      return;
    }

    const overlay = normOverlay(INSPECTOR_STATE.overlay);
    if (!overlay || isRunOverlay(overlay)) {
      btn.style.display = "none";
      return;
    }

    btn.style.display = "inline-block";
  }

  function setInspectorTab(tab) {
    INSPECTOR_STATE.tab = tab;

    document.querySelectorAll(".tab-btn").forEach((btn) => {
      const t = btn.getAttribute("data-tab");
      btn.classList.toggle("active", t === tab);
    });

    updateEnrichButtonVisibility();

    // Lazy-load effective view
    if (INSPECTOR_MODE === "plan" && tab === "effective" && INSPECTOR_STATE.planKey) {
      if (!INSPECTOR_STATE.cache.effective) {
        loadEffectivePlanIntoInspector(INSPECTOR_STATE.planKey);
        return;
      }
    }

    renderInspector();
  }

  async function refreshInspector() {
    if (!INSPECTOR_STATE.planKey && !INSPECTOR_STATE.jiraKey) return;

    // Effective first
    if (INSPECTOR_MODE === "plan" && INSPECTOR_STATE.tab === "effective" && INSPECTOR_STATE.planKey) {
      INSPECTOR_STATE.cache.effective = null;
      await loadEffectivePlanIntoInspector(INSPECTOR_STATE.planKey);
      return;
    }

    // Plan
    if (INSPECTOR_MODE === "plan" && INSPECTOR_STATE.planKey) {
      INSPECTOR_STATE.cache.plan = null;
      await loadPlanIntoInspector(INSPECTOR_STATE.planKey);
      return;
    }

    // Issue
    if (INSPECTOR_MODE === "issue" && INSPECTOR_STATE.jiraKey) {
      resetInspectorCache();
      await loadInspectorIssue(INSPECTOR_STATE.jiraKey);
    }
  }

  // ---------------------------------------------------------------------------
  // G1/G2 Inspector (Issue mode)
  // ---------------------------------------------------------------------------
  function openInspector() {
    const jiraKey = getSelectedUsKey();
    if (!jiraKey) {
      notifyInline("Select a Jira key first, then open the inspector.", "error");
      return;
    }

    INSPECTOR_MODE = "issue";
    setInspectorRoleLabel("G1/G2 – Issue Generator");

    INSPECTOR_STATE.planKey = null;
    INSPECTOR_STATE.jiraKey = jiraKey;
    INSPECTOR_STATE.tab = "jira";
    resetInspectorCache();

    setTabVisibilityForMode("issue");
    openDrawer();
    setInspectorKeyLabel(jiraKey);

    rebuildDrawerOverlaySelectForIssue(jiraKey);
    loadInspectorIssue(jiraKey);
  }

  async function fetchJsonStrict(url) {
    const r = await fetch(url);
    const txt = await r.text();
    if (!r.ok) {
      // Preserve server message (often JSON), but don't crash UI
      return { errors: [{ message: `HTTP ${r.status}`, detail: txt }], meta: { url, status: r.status }, data: null };
    }
    try {
      return JSON.parse(txt);
    } catch (_) {
      return { data: txt, meta: { url }, errors: [] };
    }
  }

  async function loadInspectorIssue(jiraKey) {
    const contentEl = $("inspectorContent");
    if (contentEl) {
      contentEl.classList.remove("inspector-html");
      contentEl.textContent = "Loading...";
    }

    try {
      const key = encodeURIComponent(jiraKey);

      if (!INSPECTOR_STATE.cache.jira) {
        INSPECTOR_STATE.cache.jira = await fetchJsonStrict(`${API_BASE}/api/jira/issue/${key}`);
      }
      if (!INSPECTOR_STATE.cache.xray) {
        INSPECTOR_STATE.cache.xray = await fetchJsonStrict(`${API_BASE}/api/xray/preview/${key}`);
      }
      if (!INSPECTOR_STATE.cache.bitbucket) {
        INSPECTOR_STATE.cache.bitbucket = await fetchJsonStrict(`${API_BASE}/api/bitbucket/changes/${key}`);
      }
      if (!INSPECTOR_STATE.cache.prompt) {
        INSPECTOR_STATE.cache.prompt = await fetchJsonStrict(`${API_BASE}/api/llm/prompt/${key}`);
      }

      renderInspector();
    } catch (e) {
      console.error(e);
      if (contentEl) contentEl.textContent = "Error loading inspector data: " + (e?.message || String(e));
    }
  }

  // ---------------------------------------------------------------------------
  // G4 Inspector (Plan mode)
  // ---------------------------------------------------------------------------
  async function openPlan(planKey) {
    if (!planKey) return;

    INSPECTOR_MODE = "plan";
    setInspectorRoleLabel("G4 – Test Plans Governance (T0+)");

    INSPECTOR_STATE.planKey = planKey;
    INSPECTOR_STATE.jiraKey = null;
    INSPECTOR_STATE.tab = INSPECTOR_STATE.tab === "effective" ? "effective" : "plan";
    resetInspectorCache();

    setTabVisibilityForMode("plan");
    openDrawer();
    setInspectorKeyLabel(planKey);

    if (!OVERLAYS_CACHE.loaded) await loadOverlaysCache();

    const mainOverlay = normOverlay($("overlaySelect")?.value);
    rebuildDrawerOverlaySelectForPlan(mainOverlay);

    setInspectorTab(INSPECTOR_STATE.tab);
    await loadPlanIntoInspector(planKey);

    if (INSPECTOR_STATE.tab === "effective") {
      await loadEffectivePlanIntoInspector(planKey);
    }
  }

  async function onDrawerOverlayChange() {
    const overlay = normOverlay($("drawerOverlaySelect")?.value);
    INSPECTOR_STATE.overlay = overlay;

    const topSel = $("overlaySelect");
    if (topSel) topSel.value = overlay;

    updateEnrichButtonVisibility();

    if (INSPECTOR_MODE === "plan" && INSPECTOR_STATE.planKey) {
      INSPECTOR_STATE.cache.plan = null;
      await loadPlanIntoInspector(INSPECTOR_STATE.planKey);
      loadTestPlans();
    }

    if (INSPECTOR_MODE === "plan" && INSPECTOR_STATE.planKey && INSPECTOR_STATE.tab === "effective") {
      INSPECTOR_STATE.cache.effective = null;
      await loadEffectivePlanIntoInspector(INSPECTOR_STATE.planKey);
    }
  }

  async function enrichCurrentPlan() {
    const planKey = INSPECTOR_STATE.planKey;
    if (!planKey) return;

    const overlay = normOverlay(INSPECTOR_STATE.overlay);
    if (!overlay) return;

    if (isRunOverlay(overlay)) {
      window.alert(
        "This overlay is computed from a G1/G2 run (Pattern A).\n" +
          "It is read-only. Select a file overlay to persist G4 governance."
      );
      return;
    }

    const contentEl = $("inspectorContent");
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

      if (INSPECTOR_STATE.tab === "effective") {
        INSPECTOR_STATE.cache.effective = null;
        await loadEffectivePlanIntoInspector(planKey);
      }
    } catch (e) {
      console.error(e);
      window.alert("Enrich failed: " + (e?.message || String(e)));
    }
  }

  async function loadPlanIntoInspector(planKey) {
    const contentEl = $("inspectorContent");
    if (contentEl) {
      contentEl.classList.add("inspector-html");
      contentEl.innerHTML = "<div class='muted'>Loading plan...</div>";
    }

    try {
      const overlay = normOverlay(INSPECTOR_STATE.overlay);

      // IMPORTANT: don't send ?overlay= (empty)
      const url = overlay
        ? `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}?overlay=${encodeURIComponent(overlay)}`
        : `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}`;

      const resp = await fetch(url);
      if (!resp.ok) {
        const text = await resp.text();
        if (contentEl) contentEl.innerHTML = `<div class="muted">Error ${resp.status}: ${esc(text)}</div>`;
        return;
      }

      INSPECTOR_STATE.cache.plan = await resp.json();
      renderInspector();
      updateEnrichButtonVisibility();
    } catch (e) {
      console.error(e);
      if (contentEl) contentEl.innerHTML = `<div class="muted">Error loading plan: ${esc(e?.message || String(e))}</div>`;
    }
  }

  async function loadEffectivePlanIntoInspector(planKey) {
    const contentEl = $("inspectorContent");
    if (contentEl) {
      contentEl.classList.remove("inspector-html");
      contentEl.textContent = "Loading effective plan...";
    }

    const overlay = normOverlay($("drawerOverlaySelect")?.value);

    // IMPORTANT: don't send ?overlay= (empty)
    const url = overlay
      ? `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}/effective?overlay=${encodeURIComponent(overlay)}`
      : `${API_BASE}/api/test-plans/${encodeURIComponent(planKey)}/effective`;

    try {
      const res = await fetch(url);
      if (!res.ok) {
        const txt = await res.text();
        if (contentEl) contentEl.textContent = `Error ${res.status}: ${txt}`;
        return;
      }
      const json = await res.json();
      INSPECTOR_STATE.cache.effective = json;
      renderInspector();
    } catch (e) {
      console.error(e);
      if (contentEl) contentEl.textContent = "Failed to load effective plan: " + (e?.message || String(e));
    }
  }

  // ---------------------------------------------------------------------------
  // T0+ — Apply run → file overlay + candidate decisions
  // ---------------------------------------------------------------------------
  function pickDefaultFileOverlayName() {
    const mainOverlay = normOverlay($("overlaySelect")?.value);
    if (mainOverlay && isFileOverlay(mainOverlay)) return mainOverlay;

    const cur = normOverlay(INSPECTOR_STATE.overlay);
    if (cur && isFileOverlay(cur)) return cur;

    if (isFileOverlay("promptA")) return "promptA";

    const fo = listFileOverlays();
    return fo.length ? fo[0].name : "promptA";
  }

  async function applyRunToFileOverlay() {
    const planKey = INSPECTOR_STATE.planKey;
    const currentOverlay = normOverlay(INSPECTOR_STATE.overlay); // should be run overlay in this action
    const targetSel = $("applyTargetOverlay");
    const targetOverlay = normOverlay(targetSel?.value || pickDefaultFileOverlayName());

    if (!planKey) {
      window.alert("No plan selected.");
      return;
    }
    if (!currentOverlay || !isRunOverlay(currentOverlay)) {
      window.alert("Apply is only available when a RUN overlay is selected (computed from exported run).");
      return;
    }
    if (!targetOverlay || !isFileOverlay(targetOverlay)) {
      window.alert("Please select a FILE overlay (e.g. promptA/promptB) as target.");
      return;
    }

    const contentEl = $("inspectorContent");
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

      await loadOverlaysCache();
      await loadOverlaysIntoMainSelect();

      INSPECTOR_STATE.overlay = targetOverlay;

      const topSel = $("overlaySelect");
      if (topSel) topSel.value = targetOverlay;

      const drawerSel = $("drawerOverlaySelect");
      if (drawerSel) drawerSel.value = targetOverlay;

      INSPECTOR_STATE.cache.plan = await resp.json();
      renderInspector();
      updateEnrichButtonVisibility();
      loadTestPlans();

      if (INSPECTOR_STATE.tab === "effective") {
        INSPECTOR_STATE.cache.effective = null;
        await loadEffectivePlanIntoInspector(planKey);
      }
    } catch (e) {
      console.error(e);
      window.alert("Apply failed: " + (e?.message || String(e)));
    }
  }

  async function setCandidateDecision(candidateKey, decision) {
    const planKey = INSPECTOR_STATE.planKey;
    const overlay = normOverlay(INSPECTOR_STATE.overlay);

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

      if (INSPECTOR_STATE.tab === "effective" && INSPECTOR_STATE.planKey) {
        INSPECTOR_STATE.cache.effective = null;
        await loadEffectivePlanIntoInspector(INSPECTOR_STATE.planKey);
      }
    } catch (e) {
      console.error(e);
      window.alert("Decision failed: " + (e?.message || String(e)));
    }
  }

  // ---------------------------------------------------------------------------
  // Inspector rendering
  // ---------------------------------------------------------------------------
  function pillForDecision(dec) {
    const d = String(dec || "PENDING").toUpperCase();
    if (d === "ACCEPTED") return `<span class="pill pill-accept">ACCEPTED</span>`;
    if (d === "REJECTED") return `<span class="pill pill-reject">REJECTED</span>`;
    return `<span class="pill pill-pending">PENDING</span>`;
  }

  function renderInspector() {
    const el = $("inspectorContent");
    const tab = INSPECTOR_STATE.tab;
    const cache = INSPECTOR_STATE.cache;
    if (!el) return;

    // ---------------------------
    // Issue mode (text)
    // ---------------------------
    if (INSPECTOR_MODE === "issue") {
      el.classList.remove("inspector-html");

      if (tab === "jira") {
        el.textContent = prettyJson(cache.jira);
        return;
      }

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

      if (tab === "bitbucket") {
        el.textContent = prettyJson(cache.bitbucket);
        return;
      }

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

    // ---------------------------
    // Plan mode
    // ---------------------------
    if (INSPECTOR_MODE === "plan") {
      // Effective (text)
      if (tab === "effective") {
        el.classList.remove("inspector-html");

        const payload = cache.effective || {};
        const d = payload?.data || null;
        if (!d) {
          el.textContent = "No effective plan loaded.";
          return;
        }

        const lines = [];
        lines.push(`=== EFFECTIVE TEST PLAN for ${d.plan_key || INSPECTOR_STATE.planKey || ""} ===`);
        lines.push(`overlay: ${d.overlay || "(none)"}`);
        lines.push(`status:  ${d.status || ""}`);
        lines.push("");

        const s = d.summary || {};
        lines.push("=== SUMMARY ===");
        lines.push(`baseline_tests:  ${s.baseline_tests ?? "?"}`);
        lines.push(`accepted_ai:     ${s.accepted_ai ?? "?"}`);
        lines.push(`rejected_ai:     ${s.rejected_ai ?? "?"}`);
        lines.push(`pending_ai:      ${s.pending_ai ?? "?"}`);
        lines.push(`missing_tests:   ${s.missing_tests ?? "?"}`);
        lines.push(`effective_total: ${s.effective_total ?? "?"}`);
        lines.push("");

        lines.push("NOTE:");
        lines.push("- Keys like 'CAND-...' represent AI candidates (tests to create), not existing XRAY test keys.");
        lines.push("- If baseline-skip governance exists, ensure your backend effective computation applies it.");
        lines.push("");

        lines.push("=== TESTS TO EXECUTE (effective) ===");
        (Array.isArray(d.tests_to_execute) && d.tests_to_execute.length)
          ? d.tests_to_execute.forEach((t) => lines.push(`- ${t}`))
          : lines.push("(none)");
        lines.push("");

        lines.push("=== TESTS PENDING (AI) ===");
        (Array.isArray(d.tests_pending) && d.tests_pending.length)
          ? d.tests_pending.forEach((t) => lines.push(`- ${t}`))
          : lines.push("(none)");
        lines.push("");

        lines.push("=== TESTS EXCLUDED (rejected AI) ===");
        (Array.isArray(d.tests_excluded) && d.tests_excluded.length)
          ? d.tests_excluded.forEach((t) => lines.push(`- ${t}`))
          : lines.push("(none)");
        lines.push("");

        lines.push("=== MISSING TESTS (to create) ===");
        const missing = Array.isArray(d.tests_missing) ? d.tests_missing : [];
        if (!missing.length) lines.push("(none)");
        else missing.forEach((x, i) => lines.push(`- ${i + 1}. ${x?.jira_key || "?"} — ${x?.title || ""}`));

        el.textContent = lines.join("\n");
        return;
      }

      // Plan HTML
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

  // ---------------------------------------------------------------------------
  // Plan HTML renderer (unchanged except minor normalizations)
  // ---------------------------------------------------------------------------
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
        <div class="inspector-title">TEST PLAN: ${esc(data?.key || "")}</div>
        <div class="inspector-subtitle">${esc(data?.summary || "")}</div>
        <div style="margin-top:0.6rem;">
          <span class="pill pill-meta">Overlay: ${esc(overlayName || "(none)")}</span>
          <span class="pill pill-meta">kind=${esc(overlayKind || "(none)")}</span>
          <span class="pill pill-meta">Governance: ${esc(gov?.status || "NOT_ANALYZED")}</span>
          <span class="pill pill-meta">source=${esc(gov?.source || "baseline")}</span>
        </div>
        ${(Array.isArray(gov?.signals) && gov.signals.length)
          ? `<div class="muted" style="margin-top:0.5rem;">Signals: ${esc(gov.signals.join(" | "))}</div>`
          : `<div class="muted" style="margin-top:0.5rem;">Signals: (none)</div>`
        }
        <div style="margin-top:0.75rem;" class="muted">
          Jira keys (${jiraKeys.length}): ${esc(jiraKeys.join(", ") || "(none)")}
        </div>
      </div>
    `;

    const baseline = `
      <div class="inspector-block">
        <div class="inspector-section">BASELINE (read-only)</div>
        <div class="muted">Baseline tests in plan (${baselineTests.length}):</div>
        <ul class="inspector-list">
          ${baselineTests.length ? baselineTests.map((t) => `<li><code>${esc(t)}</code></li>`).join("") : `<li class="muted">(none)</li>`}
        </ul>
      </div>
    `;

    // Run overlay (read-only) + Apply (T0+)
    if (overlayKind === "run") {
      const candidates = Array.isArray(ov?.candidate_tests) ? ov.candidate_tests : [];

      const fileOverlays = listFileOverlays();
      const defaultTarget = pickDefaultFileOverlayName();
      const targetOptions =
        fileOverlays.length
          ? fileOverlays
              .map((o) => {
                const selected = o.name === defaultTarget ? " selected" : "";
                return `<option value="${esc(o.name)}"${selected}>${esc(o.label || o.name)}</option>`;
              })
              .join("")
          : `<option value="promptA" selected>promptA (file)</option>`;

      const applyBlock = `
        <div class="inspector-block">
          <div class="inspector-section">AI CANDIDATE TESTS (from run, read-only)</div>
          <div class="muted">These candidates come from the exported run. Apply them into a file overlay to enable G4 decisions.</div>

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
            ${candidates.length
              ? candidates
                  .map((c) => {
                    return `
                      <div class="candidate-card">
                        <div class="candidate-head">
                          <div><code>${esc(c.candidate_key || "")}</code></div>
                          <div class="candidate-meta">
                            <span class="pill pill-meta">${esc(c.priority || "MEDIUM")}</span>
                            <span class="pill pill-meta">${esc(c.type || "functional")}</span>
                          </div>
                        </div>
                        <div class="candidate-title">${esc(c.title || "")}</div>
                        ${c.mapped_existing_test_key
                          ? `<div class="muted">mapped_to: <code>${esc(c.mapped_existing_test_key)}</code></div>`
                          : `<div class="muted">mapped_to: (none)</div>`}
                      </div>
                    `;
                  })
                  .join("")
              : `<div class="muted">(none)</div>`}
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
            Use <b>Enrich</b> to compute baseline governance inside this file overlay.
            Use <b>Accept/Reject</b> on AI candidates to persist decisions.
          </div>
        </div>
      `;

      const aiBlock = `
        <div class="inspector-block">
          <div class="inspector-section">AI CANDIDATE TESTS (persisted in file overlay)</div>
          <div class="muted">Decisions are persisted here. This file overlay is the source of truth for G4 governance.</div>

          <div class="candidate-grid">
            ${aiCandidates.length
              ? aiCandidates
                  .map((c) => {
                    const dec = String(c.decision || "PENDING").toUpperCase();
                    const cardClass =
                      dec === "ACCEPTED" ? "candidate-accept" : dec === "REJECTED" ? "candidate-reject" : "";
                    return `
                      <div class="candidate-card ${cardClass}">
                        <div class="candidate-head">
                          <div><code>${esc(c.candidate_key || "")}</code></div>
                          <div class="candidate-meta">
                            ${pillForDecision(c.decision)}
                            <span class="pill pill-meta">${esc(c.priority || "MEDIUM")}</span>
                            <span class="pill pill-meta">${esc(c.type || "functional")}</span>
                          </div>
                        </div>

                        <div class="candidate-title">${esc(c.title || "")}</div>

                        ${c.mapped_existing_test_key
                          ? `<div class="muted">mapped_to: <code>${esc(c.mapped_existing_test_key)}</code></div>`
                          : `<div class="muted">mapped_to: (none)</div>`}

                        <div class="candidate-actions" style="margin-top:0.75rem;">
                          <button onclick="setCandidateDecision('${escJs(c.candidate_key || "")}', 'ACCEPTED')">Accept</button>
                          <button onclick="setCandidateDecision('${escJs(c.candidate_key || "")}', 'REJECTED')">Reject</button>
                          <button onclick="setCandidateDecision('${escJs(c.candidate_key || "")}', 'PENDING')">Reset</button>
                        </div>
                      </div>
                    `;
                  })
                  .join("")
              : `<div class="muted">(No AI candidates in this overlay yet. Use a run overlay + Apply to import.)</div>`}
          </div>
        </div>
      `;

      const g4Lists = `
        <div class="inspector-block">
          <div class="inspector-section">EXISTING TESTS: TO EXECUTE</div>
          ${execTests.length
            ? `<ul class="inspector-list">${execTests.map((t) => `<li><code>${esc(t)}</code></li>`).join("")}</ul>`
            : `<div class="muted">(none)</div>`}
        </div>

        <div class="inspector-block">
          <div class="inspector-section">EXISTING TESTS: TO SKIP</div>
          ${skipTests.length
            ? `<ul class="inspector-list">${skipTests
                .map((t) => `<li>${esc(t?.test_key || t?.key || JSON.stringify(t))}</li>`)
                .join("")}</ul>`
            : `<div class="muted">(none)</div>`}
        </div>

        <div class="inspector-block">
          <div class="inspector-section">NEW TESTS: TO CREATE (governance target)</div>
          ${newTests.length
            ? `
              <div class="candidate-grid">
                ${newTests
                  .map(
                    (t, i) => `
                      <div class="candidate-card">
                        <div class="candidate-head">
                          <div><b>${i + 1}.</b> ${esc(t?.jira_key || "")}</div>
                          <div class="candidate-meta">
                            <span class="pill pill-meta">${esc(t?.priority || "MEDIUM")}</span>
                          </div>
                        </div>
                        <div class="candidate-title">${esc(t?.title || "")}</div>
                        ${Array.isArray(t?.tags) && t.tags.length ? `<div class="muted">tags: ${esc(t.tags.join(", "))}</div>` : ""}
                        ${t?.given ? `<div class="candidate-steps"><b>GIVEN</b>: ${esc(t.given)}</div>` : ""}
                        ${t?.when ? `<div class="candidate-steps"><b>WHEN</b>: ${esc(t.when)}</div>` : ""}
                        ${t?.then ? `<div class="candidate-steps"><b>THEN</b>: ${esc(t.then)}</div>` : ""}
                      </div>
                    `
                  )
                  .join("")}
              </div>
            `
            : `<div class="muted">(none)</div>`}
        </div>
      `;

      return header + baseline + g4EnrichHint + aiBlock + g4Lists;
    }

    return header + baseline + `<div class="inspector-block"><div class="muted">(No overlay selected.)</div></div>`;
  }

  // ---------------------------------------------------------------------------
  // Copy inspector
  // ---------------------------------------------------------------------------
  async function copyInspector() {
    const el = $("inspectorContent");
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

  // ---------------------------------------------------------------------------
  // Main overlay select change (G4)
  // ---------------------------------------------------------------------------
  async function onMainOverlayChange() {
    await loadTestPlans();

    if (isInspectorOpen() && INSPECTOR_MODE === "plan" && INSPECTOR_STATE.planKey) {
      const overlay = normOverlay($("overlaySelect")?.value);
      if (!OVERLAYS_CACHE.loaded) await loadOverlaysCache();
      rebuildDrawerOverlaySelectForPlan(overlay);

      INSPECTOR_STATE.cache.plan = null;
      await loadPlanIntoInspector(INSPECTOR_STATE.planKey);

      if (INSPECTOR_STATE.tab === "effective") {
        INSPECTOR_STATE.cache.effective = null;
        await loadEffectivePlanIntoInspector(INSPECTOR_STATE.planKey);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", async () => {
    await resolveApiBase();

    // publish the real resolved values for debugging
    window.API_BASE = API_BASE;
    window.getApiBase = getApiBase;

    await loadJiraKeysIntoCombo();
    await loadOverlaysCache();
    await loadOverlaysIntoMainSelect();

    const overlaySel = $("overlaySelect");
    if (overlaySel) overlaySel.addEventListener("change", onMainOverlayChange);

    const drawerSel = $("drawerOverlaySelect");
    if (drawerSel) drawerSel.addEventListener("change", onDrawerOverlayChange);

    const sel = $("jiraKeySelect");
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
          resetInspectorCache();
          setInspectorKeyLabel(key);
          rebuildDrawerOverlaySelectForIssue(key);
          await loadInspectorIssue(key);
        }
      });
    }

    setExportButtonState();
  });

  // ---------------------------------------------------------------------------
  // Public API for inline onclick + external bindings
  // ---------------------------------------------------------------------------
  window.showPanel = showPanel;

  // G1/G2
  window.generate = generate;
  window.exportRun = exportRun;
  window.openInspector = openInspector;

  // G4
  window.openPlan = openPlan;
  window.enrichCurrentPlan = enrichCurrentPlan;
  window.applyRunToFileOverlay = applyRunToFileOverlay;
  window.setCandidateDecision = setCandidateDecision;

  // Common
  window.copyInspector = copyInspector;
  window.closeInspector = closeInspector;
  window.setInspectorTab = setInspectorTab;
  window.refreshInspector = refreshInspector;
})();
