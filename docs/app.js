const SIG = { "높음": "var(--high)", "보통": "var(--mid)", "낮음": "var(--low)" };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let DATA = null;

async function boot() {
  try {
    const res = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    DATA = await res.json();
  } catch (e) {
    $("briefBody").textContent = "데이터를 불러오지 못했습니다. data.json이 생성됐는지 확인하세요.";
    return;
  }

  $("baseDate").textContent = DATA.base_date || "—";
  $("stamp").textContent = `갱신 ${fmtStamp(DATA.generated_at)}`;

  const sel = $("bu");
  ["전체", ...(DATA.bus || [])].forEach((bu) => {
    const o = document.createElement("option");
    o.value = bu;
    o.textContent = bu;
    sel.appendChild(o);
  });

  const initial = new URLSearchParams(location.search).get("bu");
  sel.value = (DATA.bus || []).includes(initial) || initial === "전체" ? initial : "전체";

  sel.addEventListener("change", () => {
    const url = new URL(location);
    url.searchParams.set("bu", sel.value);
    history.replaceState(null, "", url);
    render(sel.value);
  });

  render(sel.value);
}

function fmtStamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function render(bu) {
  const items = (DATA.items || []).filter((i) => bu === "전체" || i.bu === bu);

  $("count").textContent = `${items.length}건`;
  $("briefBody").textContent = (DATA.briefings || {})[bu] || "브리핑이 없습니다.";

  const hasItems = items.length > 0;
  $("board").hidden = !hasItems;
  $("cards").hidden = !hasItems;
  $("empty").hidden = hasItems;
  if (!hasItems) {
    $("boardBody").innerHTML = "";
    $("cards").innerHTML = "";
    return;
  }

  renderTable(items);
  renderCards(items);
}

/* 구분(작업 유형)이 같은 연속 행을 하나의 셀로 병합 */
function groupRuns(items) {
  const runs = [];
  items.forEach((it) => {
    const last = runs[runs.length - 1];
    if (last && last.key === it.category) last.rows.push(it);
    else runs.push({ key: it.category, rows: [it] });
  });
  return runs;
}

function summaryHTML(item) {
  if (!item.summary || !item.summary.length) {
    return `<span class="none">요약 없음</span>`;
  }
  return `<ul>${item.summary.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>`;
}

function titleCell(item) {
  const label = `${item.icon ? esc(item.icon) + " " : ""}${esc(item.title)}`;
  const link = item.url
    ? `<a href="${esc(item.url)}" target="_blank" rel="noopener">${label}</a>`
    : `<span>${label}</span>`;
  const due = item.due ? `<span class="due">마감 ${esc(item.due)}</span>` : "";
  return link + due;
}

function renderTable(items) {
  const body = $("boardBody");
  body.innerHTML = "";

  groupRuns(items).forEach((run) => {
    run.rows.forEach((item, idx) => {
      const tr = document.createElement("tr");
      tr.style.setProperty("--sig", SIG[item.priority] || "var(--line)");
      if (item.priority === "높음") tr.classList.add("is-high");

      if (idx === 0) {
        const td = document.createElement("td");
        td.className = "c-group";
        td.rowSpan = run.rows.length;
        td.innerHTML = `${esc(run.key)}<small>${run.rows.length}건</small>`;
        tr.appendChild(td);
      }

      tr.insertAdjacentHTML("beforeend", `
        <td class="c-title">${titleCell(item)}</td>
        <td class="c-pri"><span class="pri">${esc(item.priority || "-")}</span></td>
        <td class="c-sum">${summaryHTML(item)}</td>
      `);

      body.appendChild(tr);
    });
  });
}

function renderCards(items) {
  $("cards").innerHTML = items.map((item) => `
    <article class="card" style="--sig:${SIG[item.priority] || "var(--line)"}">
      <div class="card__top">
        <span class="card__group">${esc(item.category)}</span>
        <span class="pri">${esc(item.priority || "-")}</span>
      </div>
      <a class="card__title" href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.title)}</a>
      ${item.due ? `<span class="card__due">마감 ${esc(item.due)}</span>` : ""}
      ${item.summary && item.summary.length
        ? `<ul>${item.summary.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>`
        : ""}
    </article>
  `).join("");
}

boot();
