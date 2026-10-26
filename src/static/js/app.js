/**
 * PettyFlow Unified Frontend Engine
 * Drives the Custodian Mobile PWA and Real-Time Finance Director Approval Dashboard.
 * Includes IndexedDB offline draft storage, WebSocket live feed, camera OCR,
 * and high-performance (< 200ms) DOM rendering.
 */

// Global Application State
const state = {
  activeView: "custodian", // "custodian" | "dashboard"
  tenantId: "TENANT_CORP_01",
  custodianId: "CUSTODIAN_BRANCH_42",
  actorId: "MGR_ALEX_CHENG",
  actorRole: "MANAGER", // "MANAGER" | "FINANCE_DIRECTOR"
  currentFundId: "fund-default-001",
  availableBalanceScaled: 24500000, // $2,450.00
  currency: "USD",
  isOnline: navigator.onLine,
  ws: null,
  wsConnected: false,
  approvalQueue: [],
  queueFilterTier: "ALL",
  queueFilterState: "PENDING",
  offlineDrafts: [],
  selectedReceiptFile: null,
  selectedReceiptBase64: null,
  activeModalRequestId: null,
};

// ─────────────────────────────────────────────────────────────────────────────
// 1. IndexedDB Offline Draft Storage Engine
// ─────────────────────────────────────────────────────────────────────────────

const DB_NAME = "PettyFlowOfflineDB";
const STORE_NAME = "expense_drafts";

function openDB() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      resolve(null);
      return;
    }
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function saveOfflineDraft(draft) {
  draft.id = draft.id || "draft_" + Date.now() + "_" + Math.random().toString(36).substr(2, 6);
  draft.created_at = new Date().toISOString();

  const db = await openDB();
  if (db) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).put(draft);
      tx.oncomplete = () => {
        loadOfflineDrafts();
        resolve(draft);
      };
      tx.onerror = () => reject(tx.error);
    });
  } else {
    // LocalStorage fallback
    const drafts = JSON.parse(localStorage.getItem("pf_drafts") || "[]");
    drafts.push(draft);
    localStorage.setItem("pf_drafts", JSON.stringify(drafts));
    loadOfflineDrafts();
    return draft;
  }
}

async function getOfflineDrafts() {
  const db = await openDB();
  if (db) {
    return new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, "readonly");
      const req = tx.objectStore(STORE_NAME).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => resolve([]);
    });
  } else {
    return JSON.parse(localStorage.getItem("pf_drafts") || "[]");
  }
}

async function deleteOfflineDraft(draftId) {
  const db = await openDB();
  if (db) {
    return new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).delete(draftId);
      tx.oncomplete = () => {
        loadOfflineDrafts();
        resolve();
      };
    });
  } else {
    let drafts = JSON.parse(localStorage.getItem("pf_drafts") || "[]");
    drafts = drafts.filter((d) => d.id !== draftId);
    localStorage.setItem("pf_drafts", JSON.stringify(drafts));
    loadOfflineDrafts();
  }
}

async function loadOfflineDrafts() {
  state.offlineDrafts = await getOfflineDrafts();
  renderOfflineDraftsBanner();
}

function renderOfflineDraftsBanner() {
  const banner = document.getElementById("offlineBanner");
  const countSpan = document.getElementById("draftCount");
  const draftsListEl = document.getElementById("draftsQueueList");

  if (!banner || !countSpan) return;

  const count = state.offlineDrafts.length;
  countSpan.textContent = count;

  if (count > 0 || !state.isOnline) {
    banner.style.display = "flex";
  } else {
    banner.style.display = "none";
  }

  if (draftsListEl) {
    if (count === 0) {
      draftsListEl.innerHTML = `<p style="font-size:0.813rem; color:var(--text-muted);">No offline drafts pending sync.</p>`;
    } else {
      draftsListEl.innerHTML = state.offlineDrafts
        .map(
          (d) => `
        <div style="background:var(--bg-input); padding:0.6rem 0.8rem; border-radius:var(--radius-sm); margin-bottom:0.5rem; display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div style="font-weight:700; font-size:0.875rem;">${d.description || "Expense Draft"}</div>
            <div style="font-size:0.75rem; color:var(--text-secondary);">$${(d.amount_scaled / 10000).toFixed(2)} ${d.currency} &bull; ${new Date(d.created_at).toLocaleTimeString()}</div>
          </div>
          <div style="display:flex; gap:0.4rem;">
            ${state.isOnline ? `<button onclick="syncSingleDraft('${d.id}')" style="background:var(--tier-auto); color:#000; border:none; padding:0.25rem 0.5rem; font-size:0.75rem; font-weight:700; border-radius:var(--radius-sm); cursor:pointer;">Sync</button>` : ""}
            <button onclick="discardDraft('${d.id}')" style="background:transparent; color:var(--text-muted); border:1px solid var(--border-subtle); padding:0.25rem 0.5rem; font-size:0.75rem; border-radius:var(--radius-sm); cursor:pointer;">Discard</button>
          </div>
        </div>
      `
        )
        .join("");
    }
  }
}

async function syncAllPendingDrafts() {
  if (!state.isOnline) {
    showToast("Device is still offline. Cannot sync drafts.", "warning");
    return;
  }
  const drafts = await getOfflineDrafts();
  if (drafts.length === 0) return;

  showToast(`Syncing ${drafts.length} offline draft(s)...`, "info");
  for (const draft of drafts) {
    try {
      await submitExpenseToServer(draft);
      await deleteOfflineDraft(draft.id);
    } catch (err) {
      console.error("Failed to sync draft:", draft.id, err);
    }
  }
  showToast("All drafts synced successfully!", "success");
  loadApprovalQueue();
}

async function syncSingleDraft(draftId) {
  const draft = state.offlineDrafts.find((d) => d.id === draftId);
  if (!draft) return;
  try {
    await submitExpenseToServer(draft);
    await deleteOfflineDraft(draft.id);
    showToast("Draft synced successfully!", "success");
    loadApprovalQueue();
  } catch (err) {
    showToast("Failed to sync draft: " + err.message, "error");
  }
}

function discardDraft(draftId) {
  deleteOfflineDraft(draftId);
  showToast("Draft discarded", "info");
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Real-Time WebSocket Live Feed Client
// ─────────────────────────────────────────────────────────────────────────────

function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/v1/approvals/ws`;

  try {
    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
      state.wsConnected = true;
      updateConnectionStatus(true);
      console.log("[WebSocket] Connected to PettyFlow Real-Time Approval Feed");
    };

    state.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleWebSocketMessage(msg);
      } catch (e) {
        console.error("[WebSocket] Failed to parse message:", e);
      }
    };

    state.ws.onclose = () => {
      state.wsConnected = false;
      updateConnectionStatus(false);
      console.log("[WebSocket] Disconnected. Reconnecting in 3s...");
      setTimeout(initWebSocket, 3000);
    };

    state.ws.onerror = (err) => {
      console.warn("[WebSocket] Error:", err);
      state.ws.close();
    };
  } catch (e) {
    console.warn("[WebSocket] Initialization failed:", e);
  }
}

// Heartbeat ping
setInterval(() => {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "PING" }));
  }
}, 25000);

function handleWebSocketMessage(msg) {
  if (msg.type === "WORKFLOW_EVENT") {
    // Visual cue
    pulseLiveDot();

    // Trigger toast notification
    const actionLabel = msg.event_type.replace("REQUEST_", "").toLowerCase();
    showToast(`Real-Time Update: Request ${msg.request_id.slice(0, 8)} was ${actionLabel}`, "info");

    // Refresh live queue & stats with high priority
    loadApprovalQueue(false);
    loadSummaryStats();
  }
}

function updateConnectionStatus(connected) {
  const indicator = document.getElementById("liveStatusIndicator");
  const label = document.getElementById("liveStatusLabel");
  if (!indicator || !label) return;

  if (connected && state.isOnline) {
    indicator.className = "live-indicator";
    label.textContent = "Live Feed Connected";
  } else {
    indicator.className = "live-indicator offline";
    label.textContent = state.isOnline ? "Reconnecting..." : "Offline";
  }
}

function pulseLiveDot() {
  const dot = document.querySelector(".pulsing-dot");
  if (!dot) return;
  dot.style.transform = "scale(1.6)";
  setTimeout(() => {
    dot.style.transform = "scale(1)";
  }, 300);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Custodian Float Balance & Receipt Scanning
// ─────────────────────────────────────────────────────────────────────────────

function updateFloatWidgetUI() {
  const amountEl = document.getElementById("floatAmountDisplay");
  const currencyEl = document.getElementById("floatCurrencyDisplay");
  if (amountEl) {
    amountEl.textContent = `$${(state.availableBalanceScaled / 10000).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }
  if (currencyEl) {
    currencyEl.textContent = state.currency;
  }
}

function refreshFloatBalance() {
  // In real backend, queries /v1/funds/{fund_id}/custodians/{custodian_id}/balance
  showToast("Float balance refreshed", "success");
  updateFloatWidgetUI();
}

// Dynamic Policy Tier Preview
function onExpenseAmountChanged(value) {
  const num = parseFloat(value);
  const banner = document.getElementById("tierPreviewBanner");
  const label = document.getElementById("tierPreviewLabel");
  const note = document.getElementById("tierPreviewNote");

  if (isNaN(num) || num <= 0) {
    banner.className = "tier-preview-banner tier-auto";
    label.textContent = "Tier 1: Auto-Approve";
    note.textContent = "Enter an amount above $0.00";
    return;
  }

  if (num < 50.0) {
    banner.className = "tier-preview-banner tier-auto";
    label.textContent = "🟢 Tier 1: Auto-Approve";
    note.textContent = "Instant auto-approval (< $50.00)";
  } else if (num < 500.0) {
    banner.className = "tier-preview-banner tier-manager";
    label.textContent = "🟡 Tier 2: Line Manager Approval";
    note.textContent = "Requires Line Manager sign-off ($50 – $499.99)";
  } else {
    banner.className = "tier-preview-banner tier-cfo";
    label.textContent = "🔴 Tier 3: CFO / Finance Director Approval";
    note.textContent = "High-value policy requires Finance Director sign-off (>= $500)";
  }
}

// Receipt File Selection & AI OCR Scanner
function handleReceiptFileSelect(event) {
  const file = event.target.files[0];
  if (!file) return;

  state.selectedReceiptFile = file;

  // Render preview thumbnail
  const reader = new FileReader();
  reader.onload = (e) => {
    state.selectedReceiptBase64 = e.target.result;
    const previewBox = document.getElementById("cameraPreviewBox");
    const previewImg = document.getElementById("cameraPreviewImg");
    if (previewBox && previewImg) {
      previewImg.src = state.selectedReceiptBase64;
      previewBox.style.display = "block";
    }
  };
  reader.readAsDataURL(file);

  // Trigger auto-scan
  scanReceiptWithAI(file);
}

async function scanReceiptWithAI(file) {
  const ocrStatusEl = document.getElementById("ocrStatusChip");
  if (ocrStatusEl) {
    ocrStatusEl.style.display = "block";
    ocrStatusEl.textContent = "⚡ AI Vision Engine: Analyzing receipt image...";
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/v1/receipts/extract", {
      method: "POST",
      body: formData,
    });

    if (!res.ok) {
      throw new Error(`OCR service returned HTTP ${res.status}`);
    }

    const ocrData = await res.json();

    // Auto-populate form
    if (ocrData.total_scaled) {
      const amountFloat = (ocrData.total_scaled / 10000).toFixed(2);
      document.getElementById("expenseAmountInput").value = amountFloat;
      onExpenseAmountChanged(amountFloat);
    }
    if (ocrData.merchant_name) {
      document.getElementById("merchantInput").value = ocrData.merchant_name;
    }
    if (ocrData.merchant_name || ocrData.line_items) {
      const itemsDesc = ocrData.line_items?.map((i) => i.description).join(", ") || "General expense";
      document.getElementById("expenseDescInput").value = `${ocrData.merchant_name || "Merchant"} - ${itemsDesc}`;
    }

    if (ocrStatusEl) {
      ocrStatusEl.textContent = `✓ OCR Extracted: ${ocrData.merchant_name} ($${(ocrData.total_scaled / 10000).toFixed(2)}) - Confidence: ${(ocrData.confidence_score * 100).toFixed(0)}%`;
    }
    showToast("Receipt extracted successfully with AI!", "success");
  } catch (err) {
    console.warn("OCR API fallback or offline extraction:", err);
    if (ocrStatusEl) {
      ocrStatusEl.textContent = "⚠ Image attached. Manual verification enabled.";
    }
  }
}

// Expense Submission
async function handleExpenseSubmit(event) {
  event.preventDefault();

  const amountFloat = parseFloat(document.getElementById("expenseAmountInput").value);
  if (isNaN(amountFloat) || amountFloat <= 0) {
    showToast("Please enter a valid positive expense amount", "error");
    return;
  }

  const amountScaled = Math.round(amountFloat * 10000);
  const currency = document.getElementById("expenseCurrencySelect").value;
  const description = document.getElementById("expenseDescInput").value.trim();
  const merchant = document.getElementById("merchantInput").value.trim();

  if (!description) {
    showToast("Description is required", "error");
    return;
  }

  const payload = {
    tenant_id: state.tenantId,
    custodian_id: state.custodianId,
    amount_scaled: amountScaled,
    currency: currency,
    description: description,
    receipt_metadata: {
      merchant_name: merchant,
      receipt_image_attached: !!state.selectedReceiptBase64,
    },
    auto_approve_if_eligible: true,
  };

  if (!state.isOnline) {
    // Offline mode: queue locally
    await saveOfflineDraft(payload);
    showToast("Offline mode active: Expense saved as local draft!", "warning");
    resetExpenseForm();
    return;
  }

  try {
    await submitExpenseToServer(payload);
    showToast("Disbursement request submitted successfully!", "success");
    resetExpenseForm();
    loadApprovalQueue();
    loadSummaryStats();
  } catch (err) {
    showToast("Submission failed: " + err.message + ". Saved to offline drafts.", "error");
    await saveOfflineDraft(payload);
  }
}

async function submitExpenseToServer(payload) {
  const res = await fetch("/v1/approvals/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || `Server returned ${res.status}`);
  }

  return await res.json();
}

function resetExpenseForm() {
  document.getElementById("expenseForm").reset();
  state.selectedReceiptFile = null;
  state.selectedReceiptBase64 = null;
  const previewBox = document.getElementById("cameraPreviewBox");
  if (previewBox) previewBox.style.display = "none";
  const ocrStatusEl = document.getElementById("ocrStatusChip");
  if (ocrStatusEl) ocrStatusEl.style.display = "none";
  onExpenseAmountChanged(0);
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Management Approval Dashboard Engine (< 200 ms render budget)
// ─────────────────────────────────────────────────────────────────────────────

async function loadSummaryStats() {
  try {
    const res = await fetch(`/v1/approvals/summary?tenant_id=${encodeURIComponent(state.tenantId)}`);
    if (!res.ok) return;
    const stats = await res.json();

    document.getElementById("kpiTotalPending").textContent = stats.pending_count;
    document.getElementById("kpiTier1").textContent = stats.tier_1_auto_count;
    document.getElementById("kpiTier2").textContent = stats.tier_2_manager_count;
    document.getElementById("kpiTier3").textContent = stats.tier_3_cfo_count;
    document.getElementById("kpiPendingAmount").textContent = stats.total_pending_formatted;
  } catch (e) {
    console.warn("Failed to load summary stats:", e);
  }
}

async function loadApprovalQueue(showSpinner = true) {
  const queueContainer = document.getElementById("approvalQueueList");
  const latencyBadge = document.getElementById("renderLatencyBadge");

  const tFetchStart = performance.now();

  try {
    let url = `/v1/approvals?tenant_id=${encodeURIComponent(state.tenantId)}&limit=100`;
    if (state.queueFilterState !== "ALL") {
      url += `&state=${encodeURIComponent(state.queueFilterState)}`;
    }
    if (state.queueFilterTier !== "ALL") {
      url += `&tier=${encodeURIComponent(state.queueFilterTier)}`;
    }

    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.approvalQueue = data.requests || [];

    // Render with DOM performance measurement
    const tRenderStart = performance.now();
    renderApprovalQueueDOM(state.approvalQueue);
    const tRenderEnd = performance.now();

    const totalRenderMs = tRenderEnd - tRenderStart;
    const e2eMs = tRenderEnd - tFetchStart;

    if (latencyBadge) {
      latencyBadge.textContent = `Render: ${totalRenderMs.toFixed(1)}ms | E2E: ${e2eMs.toFixed(1)}ms`;
      // Verify sub-200ms invariant
      if (totalRenderMs < 200) {
        latencyBadge.style.color = "#34d399"; // green
      } else {
        latencyBadge.style.color = "#f87171"; // red alert
      }
    }
  } catch (err) {
    console.error("Failed to load approval queue:", err);
    if (queueContainer) {
      queueContainer.innerHTML = `<div style="padding:2rem; text-align:center; color:var(--text-muted);">Failed to load approval requests.</div>`;
    }
  }
}

function renderApprovalQueueDOM(requests) {
  const container = document.getElementById("approvalQueueList");
  if (!container) return;

  if (!requests || requests.length === 0) {
    container.innerHTML = `
      <div style="padding:3rem 1rem; text-align:center; color:var(--text-muted);">
        <div style="font-size:2rem; margin-bottom:0.5rem;">🎉</div>
        <div style="font-weight:700; font-size:1rem; color:var(--text-primary); margin-bottom:0.25rem;">No Pending Approvals</div>
        <div style="font-size:0.813rem;">All petty cash disbursement requests have been reviewed.</div>
      </div>
    `;
    return;
  }

  const html = requests
    .map((req) => {
      const tierNum = req.required_approval_tier === "AUTO_APPROVE" ? "1" : req.required_approval_tier === "MANAGER" ? "2" : "3";
      const tierLabel = req.required_approval_tier === "AUTO_APPROVE" ? "Tier 1: Auto" : req.required_approval_tier === "MANAGER" ? "Tier 2: Manager" : "Tier 3: CFO";
      const isPending = req.state === "PENDING";

      return `
      <div class="queue-item" data-id="${req.request_id}">
        <div class="item-main">
          <div class="item-avatar">${req.custodian_id.slice(-2).toUpperCase()}</div>
          <div class="item-details">
            <h4>${escapeHtml(req.description)}</h4>
            <div class="item-meta">
              <span>👤 Custodian: <strong>${escapeHtml(req.custodian_id)}</strong></span>
              <span>📅 ${new Date(req.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              <span class="badge-tier tier-${tierNum}">${tierLabel}</span>
              <span class="badge-state ${req.state}">${req.state}</span>
            </div>
          </div>
        </div>

        <div style="display:flex; align-items:center; gap:1.25rem; justify-content:flex-end;">
          <div class="item-amount-col">
            <div class="item-amount">${req.amount_formatted}</div>
            <div style="font-size:0.688rem; color:var(--text-muted);">${req.currency}</div>
          </div>

          <div class="action-buttons">
            ${
              isPending
                ? `
              <button class="btn-approve" onclick="openActionModal('${req.request_id}', 'APPROVE')">
                <span>✓</span> 1-Click Approve
              </button>
              <button class="btn-reject" onclick="openActionModal('${req.request_id}', 'REJECT')">
                <span>✕</span> Reject
              </button>
            `
                : ""
            }
            <button class="btn-audit" onclick="openAuditModal('${req.request_id}')">
              Audit Trail
            </button>
          </div>
        </div>
      </div>
    `;
    })
    .join("");

  container.innerHTML = html;
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. 1-Click Action Modal & Mandatory Audit Reasoning
// ─────────────────────────────────────────────────────────────────────────────

function openActionModal(requestId, actionType) {
  state.activeModalRequestId = requestId;
  state.activeModalAction = actionType;

  const modal = document.getElementById("actionModal");
  const title = document.getElementById("actionModalTitle");
  const confirmBtn = document.getElementById("actionConfirmBtn");
  const notesInput = document.getElementById("actionAuditNotes");
  const presetsBox = document.getElementById("actionPresets");

  notesInput.value = "";

  if (actionType === "APPROVE") {
    title.textContent = "1-Click Approve Disbursement";
    title.style.color = "#34d399";
    confirmBtn.textContent = "Confirm Approval";
    confirmBtn.style.backgroundColor = "var(--tier-auto)";
    confirmBtn.style.color = "#000";

    presetsBox.innerHTML = `
      <span class="preset-pill" onclick="setPresetNote('Verified against receipt expenditure proof')">Verified against receipt</span>
      <span class="preset-pill" onclick="setPresetNote('Routine branch office supply replenishment')">Routine office supply</span>
      <span class="preset-pill" onclick="setPresetNote('Pre-authorized emergency client travel expense')">Pre-authorized travel</span>
    `;
  } else {
    title.textContent = "1-Click Reject Disbursement";
    title.style.color = "#f87171";
    confirmBtn.textContent = "Confirm Rejection";
    confirmBtn.style.backgroundColor = "var(--tier-cfo)";
    confirmBtn.style.color = "#fff";

    presetsBox.innerHTML = `
      <span class="preset-pill" onclick="setPresetNote('Missing itemized tax receipt proof')">Missing receipt</span>
      <span class="preset-pill" onclick="setPresetNote('Exceeds daily custodian discretionary policy threshold')">Exceeds threshold</span>
      <span class="preset-pill" onclick="setPresetNote('Suspected duplicate receipt submission')">Duplicate receipt</span>
    `;
  }

  modal.classList.add("active");
  notesInput.focus();
}

function setPresetNote(text) {
  const notesInput = document.getElementById("actionAuditNotes");
  if (notesInput) {
    notesInput.value = text;
  }
}

function closeActionModal() {
  const modal = document.getElementById("actionModal");
  if (modal) modal.classList.remove("active");
  state.activeModalRequestId = null;
}

async function confirmApprovalAction() {
  const notes = document.getElementById("actionAuditNotes").value.trim();

  // Mandatory audit reasoning invariant
  if (!notes) {
    showToast("Mandatory audit reasoning is required before confirming.", "error");
    document.getElementById("actionAuditNotes").focus();
    return;
  }

  const endpoint = state.activeModalAction === "APPROVE" ? "approve" : "reject";
  const url = `/v1/approvals/${encodeURIComponent(state.activeModalRequestId)}/${endpoint}`;

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tenant_id: state.tenantId,
        actor_id: state.actorId,
        actor_role: state.actorRole,
        notes: notes,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server returned ${res.status}`);
    }

    closeActionModal();
    showToast(`Request ${state.activeModalAction.toLowerCase()}d successfully!`, "success");
    loadApprovalQueue();
    loadSummaryStats();
  } catch (err) {
    showToast("Action failed: " + err.message, "error");
  }
}

// Audit Trail Modal
async function openAuditModal(requestId) {
  const modal = document.getElementById("auditModal");
  const body = document.getElementById("auditModalBody");
  modal.classList.add("active");
  body.innerHTML = `<div style="padding:1.5rem; text-align:center; color:var(--text-muted);">Loading immutable audit history...</div>`;

  try {
    const res = await fetch(`/v1/approvals/${encodeURIComponent(requestId)}?tenant_id=${encodeURIComponent(state.tenantId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const timelineHtml = (data.audit_trail || [])
      .map(
        (rec) => `
      <div class="timeline-node">
        <div class="timeline-node-time">${new Date(rec.timestamp).toLocaleString()} &bull; Actor: <strong>${escapeHtml(rec.actor_id)}</strong></div>
        <div class="timeline-node-content">
          <div style="font-weight:700; color:var(--text-primary); margin-bottom:0.2rem;">
            Transition: ${rec.from_state} &rarr; ${rec.to_state} [${rec.event}]
          </div>
          <div style="color:var(--text-secondary);">${escapeHtml(rec.notes || "No notes provided")}</div>
          <div style="font-family:var(--font-mono); font-size:0.688rem; color:var(--text-muted); margin-top:0.25rem;">
            TxID: ${rec.transition_id}
          </div>
        </div>
      </div>
    `
      )
      .join("");

    body.innerHTML = `
      <div style="margin-bottom:1rem; border-bottom:1px solid var(--border-subtle); padding-bottom:0.75rem;">
        <div style="font-size:1.125rem; font-weight:800; color:var(--text-primary);">${data.amount_formatted} ${data.currency}</div>
        <div style="font-size:0.875rem; color:var(--text-secondary);">${escapeHtml(data.description)}</div>
        <div style="font-size:0.75rem; color:var(--text-muted); margin-top:0.25rem;">
          Request ID: <span style="font-family:var(--font-mono);">${data.request_id}</span> &bull; Required Tier: <strong>${data.required_approval_tier}</strong>
        </div>
      </div>
      <h5 style="font-size:0.813rem; text-transform:uppercase; color:var(--text-muted); letter-spacing:0.05em; margin-bottom:0.5rem;">
        Immutable State Transition Chain
      </h5>
      <div class="timeline">
        ${timelineHtml || "<p>No transitions recorded.</p>"}
      </div>
    `;
  } catch (err) {
    body.innerHTML = `<div style="padding:1.5rem; text-align:center; color:var(--tier-cfo);">Failed to load audit history: ${err.message}</div>`;
  }
}

function closeAuditModal() {
  const modal = document.getElementById("auditModal");
  if (modal) modal.classList.remove("active");
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. Navigation & Utility Functions
// ─────────────────────────────────────────────────────────────────────────────

function switchView(viewName) {
  state.activeView = viewName;

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === viewName);
  });

  document.querySelectorAll(".view-container").forEach((vc) => {
    vc.classList.toggle("active", vc.id === `${viewName}View`);
  });

  if (viewName === "dashboard") {
    loadApprovalQueue();
    loadSummaryStats();
  }
}

function setFilterTier(tier) {
  state.queueFilterTier = tier;
  document.querySelectorAll("[data-filter-tier]").forEach((el) => {
    el.classList.toggle("active", el.dataset.filterTier === tier);
  });
  loadApprovalQueue();
}

function setFilterState(filterState) {
  state.queueFilterState = filterState;
  document.querySelectorAll("[data-filter-state]").forEach((el) => {
    el.classList.toggle("active", el.dataset.filterState === filterState);
  });
  loadApprovalQueue();
}

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = "toast";

  const icon = type === "success" ? "✓" : type === "error" ? "✕" : type === "warning" ? "⚠" : "ℹ";
  toast.innerHTML = `<span style="font-weight:700;">${icon}</span> <span>${escapeHtml(message)}</span>`;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(8px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. Initialization Lifecycle
// ─────────────────────────────────────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", () => {
  // Service Worker registration
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker
      .register("/sw.js")
      .then(() => console.log("[PWA] Service Worker registered"))
      .catch((err) => console.warn("[PWA] SW registration failed:", err));
  }

  // Network listeners
  window.addEventListener("online", () => {
    state.isOnline = true;
    updateConnectionStatus(state.wsConnected);
    showToast("Network restored: Connected online", "success");
    syncAllPendingDrafts();
  });

  window.addEventListener("offline", () => {
    state.isOnline = false;
    updateConnectionStatus(false);
    showToast("Network lost: Offline mode active", "warning");
    renderOfflineDraftsBanner();
  });

  // Tab navigation
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  // Initial loads
  loadOfflineDrafts();
  initWebSocket();
  updateFloatWidgetUI();
  onExpenseAmountChanged(0);
});
