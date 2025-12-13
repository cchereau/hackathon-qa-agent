const API_BASE = "http://localhost:8000";

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
  } catch (e) {
    console.error(e);
    statusEl.textContent = "Error calling backend: " + (e?.message || String(e));
    statusEl.style.color = "red";
  }
}

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

document.addEventListener("DOMContentLoaded", () => {
  loadJiraKeysIntoCombo();
});
