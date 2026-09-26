"use strict";

/* 展柜报警链路冗余裁决 —— 前端逻辑。
 * 所有裁决均调用真实业务 API（/api/draft、/api/adjudicate、/api/maintenance/plan），
 * 前端不做任何本地裁决。
 * 修改任一草稿字段（含计划停用光纤）后，本地立即作废旧裁决与旧检修预案，
 * 并将新草稿同步到服务端使服务端结果一并失效。
 */

const MIN_NODES = 5, MAX_NODES = 9, MIN_FIBERS = 7, MAX_FIBERS = 15;

const SAMPLE = {
  nodes: ["J1", "J2", "J3", "J4", "J5", "J6"],
  fibers: [
    { a: "J1", b: "J2", length: 4, attenuation: 1 },
    { a: "J2", b: "J4", length: 5, attenuation: 1 },
    { a: "J4", b: "J6", length: 4, attenuation: 2 },
    { a: "J1", b: "J3", length: 3, attenuation: 2 },
    { a: "J3", b: "J5", length: 6, attenuation: 1 },
    { a: "J5", b: "J6", length: 3, attenuation: 1 },
    { a: "J2", b: "J3", length: 2, attenuation: 1 },
    { a: "J4", b: "J5", length: 3, attenuation: 1 },
  ],
  source: "J1",
  target: "J6",
  attLimit: 10,
  maintenanceFiber: null,
};

/* 三走廊拓扑：光纤 #1 计划停用时存在无中断检修预案。 */
const MAINTENANCE_SAMPLE = {
  nodes: ["J1", "J2", "J3", "J4", "J5", "J6", "J7", "J8"],
  fibers: [
    { a: "J1", b: "J2", length: 3, attenuation: 1 },
    { a: "J2", b: "J3", length: 3, attenuation: 1 },
    { a: "J3", b: "J8", length: 3, attenuation: 1 },
    { a: "J1", b: "J4", length: 4, attenuation: 1 },
    { a: "J4", b: "J5", length: 4, attenuation: 1 },
    { a: "J5", b: "J8", length: 4, attenuation: 1 },
    { a: "J1", b: "J6", length: 4, attenuation: 1 },
    { a: "J6", b: "J7", length: 4, attenuation: 1 },
    { a: "J7", b: "J8", length: 4, attenuation: 1 },
  ],
  source: "J1",
  target: "J8",
  attLimit: 10,
  maintenanceFiber: 1,
};

const state = {
  nodes: [],
  fibers: [],
  source: "",
  target: "",
  attLimit: 0,
  maintenanceFiber: null, // 计划检修停用的光纤录入序号（1 基），null 表示未选择
  result: null, // 当前展示的裁决（来自服务端）
  maintenanceResult: null, // 当前展示的检修预案（来自服务端）
  maintStage: "before", // 拓扑图展示的预案阶段：before=检修前 / during=停用期间
};

const $ = (id) => document.getElementById(id);

/* ---------------- 表单渲染 ---------------- */

function defaultNodeName(i) {
  let name = "J" + (i + 1);
  let k = 1;
  while (state.nodes.includes(name)) name = "J" + (i + 1) + "_" + (++k);
  return name;
}

function renderNodeNames() {
  const wrap = $("node-names");
  wrap.innerHTML = "";
  state.nodes.forEach((name, i) => {
    const label = document.createElement("label");
    label.textContent = "接续点 " + (i + 1);
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 16;
    input.value = name;
    input.addEventListener("input", () => {
      state.nodes[i] = input.value.trim();
      refreshAllSelects();
      onDraftChanged();
    });
    label.appendChild(input);
    wrap.appendChild(label);
  });
}

function fillSelect(sel, current) {
  sel.innerHTML = "";
  state.nodes.forEach((n) => {
    const opt = document.createElement("option");
    opt.value = n;
    opt.textContent = n;
    sel.appendChild(opt);
  });
  if (state.nodes.includes(current)) sel.value = current;
}

function refreshAllSelects() {
  fillSelect($("source-select"), state.source);
  fillSelect($("target-select"), state.target);
  state.source = $("source-select").value || state.nodes[0] || "";
  state.target = $("target-select").value || state.nodes[1] || "";
  // 光纤端点引用了被删除/改名的接续点时，回退到前两个接续点
  state.fibers.forEach((f) => {
    if (!state.nodes.includes(f.a)) f.a = state.nodes[0] || "";
    if (!state.nodes.includes(f.b)) f.b = state.nodes[1] || state.nodes[0] || "";
  });
  renderFiberRows();
}

function renderFiberRows() {
  const tbody = $("fiber-table").querySelector("tbody");
  tbody.innerHTML = "";
  state.fibers.forEach((f, i) => {
    const tr = document.createElement("tr");

    const tdNo = document.createElement("td");
    tdNo.textContent = i + 1;
    tr.appendChild(tdNo);

    ["a", "b"].forEach((key) => {
      const td = document.createElement("td");
      const sel = document.createElement("select");
      state.nodes.forEach((n) => {
        const opt = document.createElement("option");
        opt.value = n;
        opt.textContent = n;
        sel.appendChild(opt);
      });
      sel.value = state.nodes.includes(f[key]) ? f[key] : state.nodes[0];
      sel.addEventListener("change", () => {
        f[key] = sel.value;
        onDraftChanged();
      });
      td.appendChild(sel);
      tr.appendChild(td);
    });

    ["length", "attenuation"].forEach((key) => {
      const td = document.createElement("td");
      const input = document.createElement("input");
      input.type = "number";
      input.step = "1";
      input.min = key === "length" ? "1" : "0";
      input.value = f[key];
      input.addEventListener("input", () => {
        f[key] = input.value === "" ? "" : Number(input.value);
        onDraftChanged();
      });
      td.appendChild(input);
      tr.appendChild(td);
    });

    const tdDel = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link";
    btn.textContent = "删除";
    btn.disabled = state.fibers.length <= MIN_FIBERS;
    btn.addEventListener("click", () => {
      if (state.fibers.length <= MIN_FIBERS) return;
      state.fibers.splice(i, 1);
      renderFiberRows();
      onDraftChanged();
    });
    tdDel.appendChild(btn);
    tr.appendChild(tdDel);

    tbody.appendChild(tr);
  });
  $("fiber-count-label").textContent = state.fibers.length;
  $("add-fiber").disabled = state.fibers.length >= MAX_FIBERS;
  renderMaintenanceSelect();
}

/* 计划停用光纤下拉：跟随光纤表重建，保留仍有效的选择。 */
function renderMaintenanceSelect() {
  const sel = $("maintenance-select");
  sel.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "不选择";
  sel.appendChild(none);
  state.fibers.forEach((f, i) => {
    const opt = document.createElement("option");
    opt.value = String(i + 1);
    opt.textContent = `#${i + 1} ${f.a}⇄${f.b}（L${f.length} A${f.attenuation}）`;
    sel.appendChild(opt);
  });
  if (state.maintenanceFiber === null || state.maintenanceFiber > state.fibers.length) {
    state.maintenanceFiber = null;
  }
  sel.value = state.maintenanceFiber === null ? "" : String(state.maintenanceFiber);
  $("maintenance-submit").disabled = state.maintenanceFiber === null;
}

function renderAll() {
  $("node-count").value = state.nodes.length;
  $("att-limit").value = state.attLimit;
  renderNodeNames();
  refreshAllSelects();
  $("source-select").value = state.source;
  $("target-select").value = state.target;
}

/* ---------------- 草稿校验与采集 ---------------- */

function collectDraft() {
  const errors = [];
  const n = state.nodes.length;
  if (n < MIN_NODES || n > MAX_NODES) errors.push("接续点数量须在 5–9 之间");
  if (state.nodes.some((x) => !x || !x.trim())) errors.push("接续点名称不能为空");
  if (new Set(state.nodes).size !== n) errors.push("接续点名称必须唯一");
  if (state.source === state.target) errors.push("主控室与展柜不能相同");
  if (!state.nodes.includes(state.source) || !state.nodes.includes(state.target))
    errors.push("主控室与展柜必须是已录入的接续点");
  if (!Number.isInteger(state.attLimit) || state.attLimit < 0)
    errors.push("每路衰减上限须为非负整数");
  if (state.fibers.length < MIN_FIBERS || state.fibers.length > MAX_FIBERS)
    errors.push("光纤段数须在 7–15 之间");
  state.fibers.forEach((f, i) => {
    const no = i + 1;
    if (!f.a || !f.b) errors.push(`光纤 #${no}：端点不能为空`);
    else if (f.a === f.b) errors.push(`光纤 #${no}：两端不能是同一接续点`);
    if (!Number.isInteger(f.length) || f.length < 1)
      errors.push(`光纤 #${no}：长度须为正整数`);
    if (!Number.isInteger(f.attenuation) || f.attenuation < 0)
      errors.push(`光纤 #${no}：衰减须为非负整数`);
  });
  if (errors.length) return { errors };
  return {
    draft: {
      nodes: state.nodes.slice(),
      fibers: state.fibers.map((f) => ({
        a: f.a, b: f.b, length: f.length, attenuation: f.attenuation,
      })),
      source: state.source,
      target: state.target,
      attenuation_limit: state.attLimit,
      maintenance_fiber: state.maintenanceFiber,
    },
  };
}

/* ---------------- 草稿失效 ---------------- */

let saveTimer = null;

function onDraftChanged() {
  // 本地立即使旧裁决、旧检修预案失效
  if (state.result) {
    state.result = null;
    showStale();
  }
  if (state.maintenanceResult) {
    state.maintenanceResult = null;
    showMaintenanceStale();
  }
  renderMaintenanceSelect(); // 光纤端点/参数可能已改，刷新停用光纤下拉标签
  drawTopology();
  // 防抖同步草稿到服务端，使服务端旧结果一并失效（草稿合法时才保存）
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const { draft } = collectDraft();
    if (draft) api("PUT", "/api/draft", draft).catch(() => {});
  }, 500);
}

function showStale() {
  const panel = $("result-panel");
  panel.hidden = false;
  $("result-banner").innerHTML =
    '<div class="banner banner-stale">草稿已修改，旧裁决已失效，请重新提交裁决。</div>';
  $("details").innerHTML = "";
}

function showMaintenanceStale() {
  const panel = $("maintenance-panel");
  panel.hidden = false;
  $("maintenance-banner").innerHTML =
    '<div class="banner banner-stale">草稿或目标停用光纤已修改，旧检修预案已失效，请重新生成。</div>';
  $("maintenance-details").innerHTML = "";
  $("stage-toggle").hidden = true;
}

/* ---------------- API 调用 ---------------- */

async function api(method, url, body) {
  const resp = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload = null;
  try { payload = await resp.json(); } catch (e) { /* 非 JSON 响应 */ }
  if (!resp.ok) {
    const detail = payload && payload.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg).join("；")
      : (detail || "请求失败（HTTP " + resp.status + "）");
    const err = new Error(msg);
    err.status = resp.status;
    throw err;
  }
  return payload;
}

async function submitAdjudication() {
  const errBox = $("form-error");
  errBox.hidden = true;
  const { draft, errors } = collectDraft();
  if (errors) {
    errBox.textContent = "草稿校验未通过：" + errors.join("；");
    errBox.hidden = false;
    return;
  }
  const btn = $("submit");
  btn.disabled = true;
  try {
    await api("PUT", "/api/draft", draft);
    state.result = await api("POST", "/api/adjudicate");
    renderResult();
  } catch (e) {
    errBox.textContent = "裁决请求失败：" + e.message;
    errBox.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

async function submitMaintenance() {
  const errBox = $("form-error");
  errBox.hidden = true;
  const { draft, errors } = collectDraft();
  if (errors) {
    errBox.textContent = "草稿校验未通过：" + errors.join("；");
    errBox.hidden = false;
    return;
  }
  if (state.maintenanceFiber === null) {
    errBox.textContent = "请先在草稿中选择计划检修停用的光纤。";
    errBox.hidden = false;
    return;
  }
  const btn = $("maintenance-submit");
  btn.disabled = true;
  try {
    await api("PUT", "/api/draft", draft);
    state.maintenanceResult = await api("POST", "/api/maintenance/plan");
    state.maintStage = "before";
    renderMaintenanceResult();
  } catch (e) {
    errBox.textContent = "检修预案请求失败：" + e.message;
    errBox.hidden = false;
  } finally {
    btn.disabled = state.maintenanceFiber === null;
  }
}

/* ---------------- 结果渲染 ---------------- */

function pathCard(title, cls, path) {
  return (
    `<div class="path-card ${cls}"><h3>${title}</h3><dl>` +
    `<dt>经过接续点</dt><dd>${path.nodes.map(escapeHtml).join(" → ")}</dd>` +
    `<dt>光纤录入序号</dt><dd>${path.fibers.map((f) => "#" + f).join("、")}</dd>` +
    `<dt>总长度</dt><dd>${path.length}</dd>` +
    `<dt>总衰减</dt><dd>${path.attenuation}</dd>` +
    `</dl></div>`
  );
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderResult() {
  const r = state.result;
  const panel = $("result-panel");
  panel.hidden = false;
  const banner = $("result-banner");
  const details = $("details");
  if (r.status === "ok") {
    banner.innerHTML =
      '<div class="banner banner-ok">裁决成功：已形成主备冗余链路。</div>';
    details.innerHTML =
      pathCard("主路", "primary", r.primary) +
      pathCard("备路", "backup", r.backup) +
      `<div class="summary">裁决要点：较长一路长度 ${Math.max(r.primary.length, r.backup.length)}，` +
      `两路衰减和 ${r.primary.attenuation + r.backup.attenuation}，` +
      `各自衰减均未超过上限（草稿版本 v${r.draft_version}）。</div>`;
  } else {
    banner.innerHTML =
      '<div class="banner banner-infeasible">无法形成冗余链路' +
      `<p>${escapeHtml(r.message || "不存在满足接续点独立、光纤独立且衰减不超限的主备双路。")}</p></div>`;
    details.innerHTML =
      '<div class="summary">请调整光纤连接、衰减上限或接续点规模后重新提交裁决。' +
      "系统不会把共享接续点或复用光纤的路线冒充为备路。</div>";
  }
  drawTopology();
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------------- 检修切换预案渲染 ---------------- */

function renderMaintenanceResult() {
  const r = state.maintenanceResult;
  const panel = $("maintenance-panel");
  panel.hidden = false;
  const banner = $("maintenance-banner");
  const details = $("maintenance-details");
  const toggle = $("stage-toggle");
  if (r.status === "ok") {
    const target = r.maintenance_fiber;
    banner.innerHTML =
      `<div class="banner banner-ok">已生成无中断检修预案：仅切换受影响路，` +
      `驻留路在检修前与停用期间保持完整接续点与光纤序列不变（计划停用光纤 #${target}）。</div>`;
    const maxLen = Math.max(r.affected.length, r.resident.length, r.replacement.length);
    const totalAtt = r.affected.attenuation + r.resident.attenuation + r.replacement.attenuation;
    const delta = r.replacement.length - r.affected.length;
    details.innerHTML =
      `<h3 class="stage-title">阶段一 · 检修前（光纤 #${target} 在用）</h3>` +
      `<div class="details">` +
      pathCard("受影响路 · 切换线路（停用开始即切出）", "affected", r.affected) +
      pathCard("驻留路 · 两阶段不变", "resident", r.resident) +
      `</div>` +
      `<h3 class="stage-title">阶段二 · 停用期间（光纤 #${target} 停用）</h3>` +
      `<div class="details">` +
      pathCard("替代路 · 切换线路（恢复后切回）", "replacement", r.replacement) +
      pathCard("驻留路 · 与阶段一完全一致", "resident", r.resident) +
      `</div>` +
      `<div class="summary">裁决要点：三路最长长度 ${maxLen}，三路总衰减 ${totalAtt}，` +
      `替代路相对原路长度增量 ${delta}；两阶段各自衰减均未超过上限（草稿版本 v${r.draft_version}）。` +
      `可在拓扑图上方切换阶段查看两组线路。</div>`;
    toggle.hidden = false;
    updateStageButtons();
  } else {
    banner.innerHTML =
      '<div class="banner banner-infeasible">无法形成无中断检修预案' +
      `<p>${escapeHtml(r.message || "在只切换一路的条件下，没有可行的检修前/停用期间双路组合。")}</p></div>`;
    details.innerHTML =
      '<div class="summary">系统不会把共享接续点、复用光纤或衰减超限的路线冒充为切换预案。' +
      "请调整光纤连接、衰减上限，或改选其他计划停用光纤后重新生成。</div>";
    toggle.hidden = true;
  }
  drawTopology();
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function updateStageButtons() {
  $("stage-before").classList.toggle("active", state.maintStage === "before");
  $("stage-during").classList.toggle("active", state.maintStage === "during");
}

/* ---------------- 拓扑图 ---------------- */

function drawTopology() {
  const svg = $("topology");
  const NS = "http://www.w3.org/2000/svg";
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const nodes = state.nodes.filter((x) => x);
  if (!nodes.length) return;

  const W = 560, H = 460, cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 72;
  const pos = {};
  nodes.forEach((n, i) => {
    const ang = -Math.PI / 2 + (i * 2 * Math.PI) / nodes.length;
    pos[n] = { x: cx + R * Math.cos(ang), y: cy + R * Math.sin(ang) };
  });

  const result = state.result && state.result.status === "ok" ? state.result : null;
  const maint = state.maintenanceResult && state.maintenanceResult.status === "ok"
    ? state.maintenanceResult
    : null;
  const fiberRole = {};
  const nodeRole = {};
  if (maint) {
    // 检修预案分阶段着色：驻留路两阶段不变，切换线路随阶段变化
    const active = state.maintStage === "before" ? maint.affected : maint.replacement;
    const activeRole = state.maintStage === "before" ? "affected" : "replacement";
    active.fibers.forEach((f) => (fiberRole[f] = activeRole));
    maint.resident.fibers.forEach((f) => (fiberRole[f] = "resident"));
    active.nodes.forEach((n) => (nodeRole[n] = activeRole));
    maint.resident.nodes.forEach((n) => (nodeRole[n] = "resident"));
    if (state.maintStage === "during") fiberRole[maint.maintenance_fiber] = "outage";
  } else if (result) {
    result.primary.fibers.forEach((f) => (fiberRole[f] = "primary"));
    result.backup.fibers.forEach((f) => (fiberRole[f] = "backup"));
    result.primary.nodes.forEach((n) => (nodeRole[n] = "primary"));
    result.backup.nodes.forEach((n) => (nodeRole[n] = "backup"));
  }
  // 未生成预案时，已选择的计划停用光纤以虚线标出
  if (!maint && state.maintenanceFiber !== null && fiberRole[state.maintenanceFiber] === undefined) {
    fiberRole[state.maintenanceFiber] = "target";
  }
  nodeRole[state.source] = "source";
  nodeRole[state.target] = "target";

  // 同一对接续点间的并行光纤，沿法向错开绘制
  const groups = {};
  state.fibers.forEach((f, i) => {
    const k = [f.a, f.b].sort().join("|");
    (groups[k] = groups[k] || []).push(i);
  });

  state.fibers.forEach((f, i) => {
    const p1 = pos[f.a], p2 = pos[f.b];
    if (!p1 || !p2) return;
    const g = groups[[f.a, f.b].sort().join("|")];
    const off = (g.indexOf(i) - (g.length - 1) / 2) * 22;
    const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2;
    const dx = p2.x - p1.x, dy = p2.y - p1.y;
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len;
    const cpx = mx + nx * off, cpy = my + ny * off;

    const role = fiberRole[i + 1];
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", `M ${p1.x} ${p1.y} Q ${cpx} ${cpy} ${p2.x} ${p2.y}`);
    path.setAttribute("class", "edge" + (role ? " edge-" + role : ""));
    if (role === "backup") path.setAttribute("stroke-dasharray", "8 5");
    svg.appendChild(path);

    const qx = 0.25 * p1.x + 0.5 * cpx + 0.25 * p2.x;
    const qy = 0.25 * p1.y + 0.5 * cpy + 0.25 * p2.y;
    const label = document.createElementNS(NS, "text");
    label.setAttribute("x", qx + nx * 12);
    label.setAttribute("y", qy + ny * 12);
    label.setAttribute("class", "edge-label" + (role ? " edge-label-" + role : ""));
    let labelText = `#${i + 1} L${f.length} A${f.attenuation}`;
    if (state.maintenanceFiber === i + 1) {
      if (maint && state.maintStage === "during") labelText = `#${i + 1} ✕停用`;
      else if (maint) labelText += " ⚠将停用";
      else labelText += " ⚠停用目标";
    }
    label.textContent = labelText;
    svg.appendChild(label);
  });

  nodes.forEach((n) => {
    const p = pos[n];
    const role = nodeRole[n];
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "node" + (role ? " node-" + role : ""));
    const circle = document.createElementNS(NS, "circle");
    circle.setAttribute("cx", p.x);
    circle.setAttribute("cy", p.y);
    circle.setAttribute("r", 17);
    g.appendChild(circle);
    const text = document.createElementNS(NS, "text");
    text.setAttribute("x", p.x);
    text.setAttribute("y", p.y + 3.5);
    text.textContent = n;
    g.appendChild(text);
    if (role === "source" || role === "target") {
      const badge = document.createElementNS(NS, "text");
      badge.setAttribute("x", p.x);
      badge.setAttribute("y", p.y + 32);
      badge.setAttribute("class", "badge");
      badge.textContent = role === "source" ? "主控室" : "展柜";
      g.appendChild(badge);
    }
    svg.appendChild(g);
  });
}

/* ---------------- 事件绑定与初始化 ---------------- */

function bindEvents() {
  $("node-count").addEventListener("change", (e) => {
    let n = Number(e.target.value);
    if (!Number.isInteger(n)) n = MIN_NODES;
    n = Math.min(MAX_NODES, Math.max(MIN_NODES, n));
    e.target.value = n;
    while (state.nodes.length < n) state.nodes.push(defaultNodeName(state.nodes.length));
    state.nodes = state.nodes.slice(0, n);
    if (!state.nodes.includes(state.source)) state.source = state.nodes[0];
    if (!state.nodes.includes(state.target)) state.target = state.nodes[1] || state.nodes[0];
    renderNodeNames();
    refreshAllSelects();
    $("source-select").value = state.source;
    $("target-select").value = state.target;
    onDraftChanged();
  });

  $("source-select").addEventListener("change", (e) => { state.source = e.target.value; onDraftChanged(); });
  $("target-select").addEventListener("change", (e) => { state.target = e.target.value; onDraftChanged(); });
  $("att-limit").addEventListener("input", (e) => {
    state.attLimit = e.target.value === "" ? "" : Number(e.target.value);
    onDraftChanged();
  });
  $("maintenance-select").addEventListener("change", (e) => {
    state.maintenanceFiber = e.target.value === "" ? null : Number(e.target.value);
    renderMaintenanceSelect();
    onDraftChanged();
  });

  $("add-fiber").addEventListener("click", () => {
    if (state.fibers.length >= MAX_FIBERS) return;
    state.fibers.push({ a: state.nodes[0], b: state.nodes[1], length: 1, attenuation: 0 });
    renderFiberRows();
    onDraftChanged();
  });

  $("load-sample").addEventListener("click", () => {
    loadSample();
    onDraftChanged();
  });

  $("load-maintenance-sample").addEventListener("click", () => {
    loadMaintenanceSample();
    onDraftChanged();
  });

  $("submit").addEventListener("click", submitAdjudication);
  $("maintenance-submit").addEventListener("click", submitMaintenance);
  $("stage-before").addEventListener("click", () => setMaintStage("before"));
  $("stage-during").addEventListener("click", () => setMaintStage("during"));
}

function setMaintStage(stage) {
  state.maintStage = stage;
  updateStageButtons();
  drawTopology();
}

function loadSample() {
  state.nodes = SAMPLE.nodes.slice();
  state.fibers = SAMPLE.fibers.map((f) => ({ ...f }));
  state.source = SAMPLE.source;
  state.target = SAMPLE.target;
  state.attLimit = SAMPLE.attLimit;
  state.maintenanceFiber = SAMPLE.maintenanceFiber;
  renderAll();
}

function loadMaintenanceSample() {
  state.nodes = MAINTENANCE_SAMPLE.nodes.slice();
  state.fibers = MAINTENANCE_SAMPLE.fibers.map((f) => ({ ...f }));
  state.source = MAINTENANCE_SAMPLE.source;
  state.target = MAINTENANCE_SAMPLE.target;
  state.attLimit = MAINTENANCE_SAMPLE.attLimit;
  state.maintenanceFiber = MAINTENANCE_SAMPLE.maintenanceFiber;
  renderAll();
}

loadSample();
bindEvents();
drawTopology();
