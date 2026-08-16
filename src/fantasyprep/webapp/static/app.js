let currentState = null;
let pendingPickNumber = null;
let pendingWasCurrentPick = false;
let suggestions = [];
let highlightedIndex = -1;
let simRunning = false;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pickNumberFor(round, team, teams) {
  const posInRound = round % 2 === 1 ? team : teams - team + 1;
  return (round - 1) * teams + posInRound;
}

async function fetchState() {
  const res = await fetch("/api/state");
  return res.json();
}

function cellLabel(round, team) {
  return `${round}.${team}`;
}

function posChip(position) {
  return position ? `<span class="pos-chip pos-${position}">${position}</span>` : "";
}

function renderGrid(state) {
  const table = document.getElementById("draft-grid");
  table.innerHTML = "";
  if (!state.total_rounds) return;

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (let team = 1; team <= state.teams; team++) {
    const th = document.createElement("th");
    th.textContent = `Team ${team}`;
    if (team === state.my_draft_slot) th.classList.add("my-team");
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (let round = 1; round <= state.total_rounds; round++) {
    const tr = document.createElement("tr");
    for (let team = 1; team <= state.teams; team++) {
      const pickNum = pickNumberFor(round, team, state.teams);
      const td = document.createElement("td");
      td.className = "cell";
      if (team === state.my_draft_slot) td.classList.add("my-team");
      if (pickNum === state.current_pick) td.classList.add("current-pick");

      const info = state.picks[String(pickNum)];
      if (info) {
        td.classList.add("filled");
        if (info.position) td.classList.add(`pos-${info.position}`);
        td.innerHTML =
          `<div class="pick-num">${cellLabel(round, team)}</div>` +
          `<div class="player-name">${info.player}</div>` +
          `<div class="player-meta">${posChip(info.position)}<span class="team-label">${info.team || ""}</span></div>`;
        td.addEventListener("click", () => clearPick(pickNum));
      } else {
        td.innerHTML = `<div class="pick-num">${cellLabel(round, team)}</div><div class="claim">+</div>`;
        td.addEventListener("click", () => openPicker(pickNum, round, team));
      }
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
}

function render(state) {
  currentState = state;
  const hasSlot = state.my_draft_slot !== null && state.my_draft_slot !== undefined;

  document.getElementById("setup-screen").hidden = hasSlot;
  document.getElementById("app-main").hidden = !hasSlot;
  document.getElementById("status-bar").hidden = !hasSlot;
  document.getElementById("header-actions").hidden = !hasSlot;

  if (hasSlot) {
    document.getElementById("draft-slot-input").value = state.my_draft_slot;
    document.getElementById("status-pick").textContent =
      `Pick ${state.current_pick} of ${state.total_picks}`;
    document.getElementById("status-turn-badge").hidden = !state.next_pick_is_mine;
    renderGrid(state);
  }

  if (hasSlot && state.next_pick_is_mine) {
    // Constant recommendations: as soon as it's actually your turn, fetch
    // and show them automatically -- no need to remember to click the
    // button every pick. Still re-fetchable manually (e.g. after changing
    // num_sims, or just to recompute).
    loadRecommendations();
  } else {
    document.getElementById("recommend-list").hidden = true;
    document.getElementById("recommend-status").textContent = hasSlot
      ? "Recommendations appear automatically once it's your turn."
      : "";
  }
}

async function refresh() {
  render(await fetchState());
}

function roundTeamForPick(pickNum, teams) {
  const round = Math.floor((pickNum - 1) / teams) + 1;
  const posInRound = ((pickNum - 1) % teams) + 1;
  const team = round % 2 === 1 ? posInRound : teams - posInRound + 1;
  return { round, team };
}

function openPicker(pickNum, round, team) {
  pendingPickNumber = pickNum;
  pendingWasCurrentPick = currentState && pickNum === currentState.current_pick;
  document.getElementById("picker-title").textContent =
    `Assign pick ${cellLabel(round, team)}` + (pendingWasCurrentPick ? "" : " (keeper -- ahead of the current pick)");
  document.getElementById("picker-overlay").hidden = false;
  document.getElementById("player-search").value = "";
  suggestions = [];
  highlightedIndex = -1;
  renderSuggestions();
  document.getElementById("player-search").focus();
}

function openPickerForCurrentPick() {
  if (!currentState || pendingPickNumber !== null) return;
  const { round, team } = roundTeamForPick(currentState.current_pick, currentState.teams);
  openPicker(currentState.current_pick, round, team);
}

function closePicker() {
  pendingPickNumber = null;
  pendingWasCurrentPick = false;
  suggestions = [];
  highlightedIndex = -1;
  document.getElementById("picker-overlay").hidden = true;
}

function renderSuggestions() {
  const list = document.getElementById("player-suggestions");
  list.innerHTML = "";
  suggestions.forEach((p, i) => {
    const li = document.createElement("li");
    li.innerHTML =
      `<span class="sugg-name">${p.name}</span>` +
      `<span class="sugg-meta">${posChip(p.position)} ${p.team} &middot; ADP ${p.adp.toFixed(1)}</span>`;
    if (i === highlightedIndex) li.classList.add("highlighted");
    li.addEventListener("click", () => assignPick(pendingPickNumber, p.name));
    list.appendChild(li);
  });
}

async function assignPick(pickNum, playerName) {
  const wasCurrentPick = pendingWasCurrentPick;
  const res = await fetch(`/api/picks/${pickNum}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ player_name: playerName }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error);
    return;
  }
  closePicker();
  render(data);
  // Rapid live-draft entry: assigning the actual current pick immediately
  // reopens the picker for the new current pick, so typing+Enter can repeat
  // without touching the mouse. Out-of-order (keeper) entry does not chain.
  if (wasCurrentPick) openPickerForCurrentPick();
}

async function clearPick(pickNum) {
  if (!confirm(`Clear this pick?`)) return;
  const res = await fetch(`/api/picks/${pickNum}`, { method: "DELETE" });
  render(await res.json());
}

document.getElementById("setup-submit").addEventListener("click", async () => {
  const slot = parseInt(document.getElementById("draft-slot-input").value, 10);
  if (!slot) return;
  const res = await fetch("/api/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ my_draft_slot: slot }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error);
    return;
  }
  render(data);
});

document.getElementById("reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset the whole draft?")) return;
  const res = await fetch("/api/reset", { method: "POST" });
  render(await res.json());
});

document.getElementById("picker-cancel").addEventListener("click", closePicker);

document.getElementById("picker-overlay").addEventListener("click", (e) => {
  if (e.target.id === "picker-overlay") closePicker();  // click on the backdrop, not the modal card
});

let searchDebounce = null;
document.getElementById("player-search").addEventListener("input", (e) => {
  clearTimeout(searchDebounce);
  const query = e.target.value;
  searchDebounce = setTimeout(async () => {
    if (!query.trim()) {
      suggestions = [];
      highlightedIndex = -1;
      renderSuggestions();
      return;
    }
    const res = await fetch(`/api/players?q=${encodeURIComponent(query)}`);
    suggestions = await res.json();
    highlightedIndex = suggestions.length ? 0 : -1; // first result pre-highlighted -- Enter picks it instantly
    renderSuggestions();
  }, 200);
});

// Keyboard-driven entry: Up/Down cycle suggestions, Enter picks the
// highlighted (or first) one, Escape cancels. This is the fast path --
// mouse never needs to leave the keyboard-adjacent area during a live draft.
document.getElementById("player-search").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") {
    e.preventDefault();
    if (suggestions.length) {
      highlightedIndex = (highlightedIndex + 1) % suggestions.length;
      renderSuggestions();
    }
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (suggestions.length) {
      highlightedIndex = (highlightedIndex - 1 + suggestions.length) % suggestions.length;
      renderSuggestions();
    }
  } else if (e.key === "Enter") {
    e.preventDefault();
    const pick = suggestions[highlightedIndex] || suggestions[0];
    if (pick) assignPick(pendingPickNumber, pick.name);
  } else if (e.key === "Escape") {
    e.preventDefault();
    closePicker();
  }
});

// Global: Enter opens the picker for the current pick when nothing else is
// focused, so a whole draft can be logged without ever touching the mouse.
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && document.getElementById("picker-overlay").hidden && !simRunning) {
    const active = document.activeElement;
    const typingElsewhere = active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA");
    if (!typingElsewhere) {
      e.preventDefault();
      openPickerForCurrentPick();
    }
  }
});

function setSimulateButtons(running) {
  document.getElementById("simulate-btn").hidden = running;
  document.getElementById("simulate-stop-btn").hidden = !running;
}

document.getElementById("simulate-btn").addEventListener("click", async () => {
  closePicker();
  simRunning = true;
  setSimulateButtons(true);
  const status = document.getElementById("simulate-status");
  const speed = parseInt(document.getElementById("sim-speed").value, 10);
  const randomness = parseFloat(document.getElementById("sim-randomness").value);

  let lastPick = null;
  let data = null;
  while (simRunning) {
    status.textContent = `Simulating pick ${currentState.current_pick}...`;
    const res = await fetch("/api/simulate/step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ randomness }),
    });
    data = await res.json();
    if (!res.ok) {
      status.textContent = data.error || "Error simulating.";
      break;
    }
    render(data);

    if (data.next_pick_is_mine) {
      status.textContent = "Your turn!";
      break;
    }
    if (data.current_pick > data.total_picks) {
      status.textContent = "Draft complete.";
      break;
    }
    if (data.current_pick === lastPick) {
      status.textContent = "Stopped -- no eligible players left to auto-pick.";
      break;
    }
    lastPick = data.current_pick;

    if (speed > 0) await sleep(speed);
  }

  simRunning = false;
  setSimulateButtons(false);
  if (data && data.next_pick_is_mine) openPickerForCurrentPick();
});

document.getElementById("simulate-stop-btn").addEventListener("click", () => {
  simRunning = false;
  document.getElementById("simulate-status").textContent = "Stopped.";
});

let recommendInFlight = false;

async function loadRecommendations() {
  if (recommendInFlight) return;  // don't stack up requests (e.g. rapid state changes)
  recommendInFlight = true;

  const status = document.getElementById("recommend-status");
  const list = document.getElementById("recommend-list");
  status.textContent = "Simulating... this takes several seconds.";
  list.hidden = true;

  try {
    const res = await fetch("/api/recommend");
    const rows = await res.json();
    if (!res.ok) {
      status.textContent = rows.error || "Error running simulation.";
      return;
    }

    status.textContent = "";
    list.innerHTML = "";
    rows.forEach((row, i) => {
      const card = document.createElement("div");
      card.className = "rec-card";
      card.style.setProperty("--rank-color", `var(--${row.position.toLowerCase()}, var(--text-faint))`);
      card.innerHTML =
        `<span class="rec-rank">${i + 1}</span>` +
        `<div class="rec-main">` +
        `<div class="rec-player">${row.player}</div>` +
        `<div class="rec-meta">${posChip(row.position)}<span class="team-label">${row.team || ""}</span>` +
        `<span class="adp">ADP ${row.adp.toFixed(1)}</span></div>` +
        `</div>` +
        `<div class="rec-stats">` +
        `<div class="rec-expected">${row.expected.toFixed(0)}</div>` +
        `<div class="rec-range">${row.p25.toFixed(0)}&ndash;${row.p75.toFixed(0)}</div>` +
        `</div>`;
      list.appendChild(card);
    });
    list.hidden = false;
  } finally {
    recommendInFlight = false;
  }
}

document.getElementById("recommend-btn").addEventListener("click", loadRecommendations);

refresh();
