// local transcription app. plain javascript, no network access beyond the local server.

// helpers
const view = document.getElementById("view");
const player = document.getElementById("player");
let pollTimer = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

function fmtTime(seconds) {
  const s = Math.floor(seconds || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const pad = n => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${pad(m)}:${pad(r)}`;
}

function pct(score) {
  return score == null ? "" : `${Math.round(score * 100)}%`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return response.json();
}

function post(path, body) {
  return api(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {})
  });
}

function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(el.timer);
  el.timer = setTimeout(() => { el.hidden = true; }, 3500);
}

function meetingLink(kind, id) {
  return `#/${kind}/${encodeURIComponent(id)}`;
}

function stopAudio() {
  player.pause();
  document.querySelectorAll(".play.playing").forEach(b => b.classList.remove("playing"));
}

// inbox
let pendingFiles = [];

async function renderInbox() {
  view.innerHTML = `
    <div class="page">
      <h1>Inbox</h1>
      <div class="sub">Drop Plaud or Audio Hijack recordings to transcribe them. One file runs at a time.</div>
      <div class="drop" id="drop">
        <p>Drop m4a, mp3, or wav files here</p>
        <div class="row">
          <label class="btn">Choose files<input type="file" id="picker" multiple accept=".m4a,.mp3,.wav" hidden></label>
          <select id="source" title="Recording source, stored with any voice entries this recording adds">
            <option value="auto">Source: detect</option>
            <option value="plaud">Plaud</option>
            <option value="zoom">Zoom (Audio Hijack)</option>
            <option value="other">Other</option>
          </select>
        </div>
      </div>
      <div id="notice"></div>
      <div id="jobs"></div>
      <h2>Meetings</h2>
      <div class="list" id="meetings"><div class="empty">Loading</div></div>
    </div>`;

  const drop = document.getElementById("drop");
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => {
    e.preventDefault();
    drop.classList.remove("over");
    upload([...e.dataTransfer.files]);
  });
  document.getElementById("picker").addEventListener("change", e => upload([...e.target.files]));
  await refreshInbox();
  pollTimer = setInterval(refreshInbox, 1000);
}

async function upload(files, rerun = false) {
  if (!files.length) return;
  const form = new FormData();
  files.forEach(f => form.append("files", f));
  form.append("source_type", document.getElementById("source")?.value || "auto");
  form.append("rerun", rerun ? "true" : "false");
  toast(`Adding ${files.length} file${files.length > 1 ? "s" : ""}`);
  try {
    const result = await api("/api/upload", { method: "POST", body: form });
    const repeat = result.skipped.filter(s => s.reason === "already processed");
    const other = result.skipped.filter(s => s.reason !== "already processed");
    pendingFiles = files.filter(f => repeat.some(s => s.file === f.name));
    const notice = document.getElementById("notice");
    notice.innerHTML = "";
    if (repeat.length) {
      notice.innerHTML += `<div class="card"><b>Already processed:</b> ${repeat.map(s => esc(s.file)).join(", ")}.
        Processing again replaces the transcript and the names you confirmed.
        <div class="choices"><button class="btn small" id="rerun">Process again</button>
        <button class="btn small" id="keep">Keep the existing transcript</button></div></div>`;
      document.getElementById("rerun").onclick = () => { notice.innerHTML = ""; upload(pendingFiles, true); };
      document.getElementById("keep").onclick = () => { notice.innerHTML = ""; };
    }
    if (other.length) {
      notice.innerHTML += `<div class="card">Skipped ${other.map(s => `${esc(s.file)} (${esc(s.reason)})`).join(", ")}</div>`;
    }
    refreshInbox();
  } catch (err) {
    toast(`Upload failed: ${err.message}`);
  }
}

async function refreshInbox() {
  const state = await api("/api/state");
  updateCount(state.meetings);
  const jobsEl = document.getElementById("jobs");
  const meetingsEl = document.getElementById("meetings");
  if (!jobsEl) return;

  const jobs = state.jobs.filter(j => j.status !== "done");
  jobsEl.innerHTML = jobs.length ? `<h2>Processing</h2><div class="list">${jobs.map(job => `
    <div class="item">
      <div class="main">
        <div class="title">${esc(job.file)}</div>
        <div class="meta">${job.status === "failed" ? `<span class="badge failed">failed</span> ${esc(job.error)}`
          : job.status === "queued" ? "waiting for the current file" : esc(job.step)}${job.detail && job.status === "processing" ? `, ${esc(job.detail)}` : ""}</div>
        ${job.status === "processing" ? `<div class="bar"><div style="width:${Math.round(job.progress * 100)}%"></div></div>` : ""}
      </div>
      ${job.status === "failed" ? `<button class="btn small" data-dismiss="${job.job_id}">Dismiss</button>` : ""}
    </div>`).join("")}</div>` : "";
  jobsEl.querySelectorAll("[data-dismiss]").forEach(b => b.onclick = async () => {
    await post(`/api/jobs/${b.dataset.dismiss}/dismiss`);
    refreshInbox();
  });

  meetingsEl.innerHTML = state.meetings.length ? state.meetings.map(m => {
    const review = m.status !== "done";
    return `<div class="item">
      <div class="main">
        <div class="title">${esc(m.title || m.meeting_id)}</div>
        <div class="meta">${esc(m.date || "")} · ${m.minutes ? Math.round(m.minutes) + " min" : ""} · ${esc(m.source_type || "")}${m.split ? " · split channels" : ""}</div>
      </div>
      <span class="badge ${review ? "review" : "done"}">${review ? "needs review" : "done"}</span>
      <a class="btn small" href="${meetingLink("review", m.meeting_id)}">${review ? "Review" : "Speakers"}</a>
      <a class="btn small" href="${meetingLink("transcript", m.meeting_id)}">Transcript</a>
    </div>`;
  }).join("") : `<div class="empty">No meetings yet</div>`;
}

function updateCount(meetings) {
  const n = meetings.filter(m => m.status !== "done").length;
  document.getElementById("review-count").textContent = n ? n : "";
}

// review
let review = null;

async function renderReview(id) {
  view.innerHTML = `<div class="page"><div class="empty">Loading speakers</div></div>`;
  const [meeting, people] = await Promise.all([api(`/api/meetings/${encodeURIComponent(id)}`), api("/api/people")]);
  review = { meeting, people, decisions: {}, expanded: new Set() };
  drawReview();
}

function decisionText(spk, d) {
  if (!d) return "";
  if (d.type === "skip") return "Will be left unnamed";
  if (d.type === "same") {
    const target = review.meeting.speakers.find(s => s.speaker === d.target);
    return `Will be merged into ${esc(target ? target.label : d.target)}`;
  }
  const exists = review.people.some(p => p.name.toLowerCase() === d.name.toLowerCase());
  return `Will be named ${esc(d.name)}${exists ? "" : " (new person)"}${d.enroll ? "" : ", voice not added to profile"}`;
}

function speakerCard(spk) {
  const d = review.decisions[spk.speaker];
  const open = !spk.resolved || review.expanded.has(spk.speaker);
  const status = spk.status === "mic" ? "your mic channel"
    : spk.status === "matched" ? `matched ${pct(spk.score)}`
    : spk.status === "confirmed" ? "confirmed"
    : spk.status === "skipped" ? "left unnamed"
    : spk.best_name ? `unknown, closest ${esc(spk.best_name)} ${pct(spk.best_score)}` : "unknown";
  const head = `<div class="card-head">
      <span class="who">${esc(spk.label)}</span>
      <span class="meta">${status} · ${spk.minutes ?? 0} min</span>
      ${spk.resolved && !open ? `<button class="link" data-expand="${spk.speaker}">wrong?</button>` : ""}
    </div>`;
  if (!open) return `<div class="card collapsed ${d ? "decided" : ""}">${head}${d ? `<div class="decision">${decisionText(spk, d)}</div>` : ""}</div>`;

  const clips = spk.clips.map((c, i) => `<div class="clip">
      <button class="play" data-speaker="${spk.speaker}" data-clip="${i}" title="Play">&#9654;</button>
      <span class="at">${fmtTime(c.start)}</span>
      <span class="quote">${esc(c.text) || "<i>no words</i>"}</span>
    </div>`).join("");
  const others = review.meeting.speakers.filter(s => s.speaker !== spk.speaker && s.speaker !== "MIC");
  const suggestions = spk.suggestions.filter(s => s.score > 0.3).map(s =>
    `<button class="btn small ${d?.type === "name" && d.name === s.name ? "on" : ""}" data-suggest="${spk.speaker}" data-name="${esc(s.name)}">${esc(s.name)} <span class="hint">${pct(s.score)}</span></button>`).join("");
  const typed = d?.type === "name" ? d.name : "";
  return `<div class="card ${d ? "decided" : ""}" id="card-${spk.speaker}">
    ${head}
    <div class="clips">${clips}</div>
    <div class="choices">
      <span class="label">Who is this?</span>
      ${suggestions}
      <input type="text" list="people-list" placeholder="Search people or type a new name" data-name-input="${spk.speaker}" value="${esc(typed)}">
    </div>
    <div class="options">
      ${spk.speaker !== "MIC" && others.length ? `<label>Same person as
        <select data-same="${spk.speaker}"><option value="">choose</option>
        ${others.map(o => `<option value="${o.speaker}" ${d?.type === "same" && d.target === o.speaker ? "selected" : ""}>${esc(o.label)}</option>`).join("")}
        </select></label>` : ""}
      <label><input type="checkbox" data-noenroll="${spk.speaker}" ${d?.type === "name" && !d.enroll ? "checked" : ""}> Don't add to profile</label>
      <button class="btn small ${d?.type === "skip" ? "on" : ""}" data-skip="${spk.speaker}">Leave unnamed</button>
      ${d ? `<button class="link" data-clear="${spk.speaker}">undo</button>` : ""}
    </div>
    ${d ? `<div class="decision">${decisionText(spk, d)}</div>` : ""}
  </div>`;
}

function drawReview() {
  const m = review.meeting;
  const unresolved = m.speakers.filter(s => !s.resolved);
  const resolved = m.speakers.filter(s => s.resolved);
  const count = Object.keys(review.decisions).length;
  view.innerHTML = `
    <div class="page">
      <a class="crumb" href="#/inbox">Inbox</a>
      <h1>${esc(m.title || m.meeting_id)}</h1>
      <div class="sub">${esc(m.date || "")} · ${m.minutes ? Math.round(m.minutes) + " min" : ""} · ${m.speakers.length} speakers${m.split ? ` · ${esc(m.owner)} on their own channel` : ""}
        · <a href="${meetingLink("transcript", m.meeting_id)}">Transcript</a></div>
      ${unresolved.length ? `<h2>Needs a name</h2>${unresolved.map(speakerCard).join("")}` : `<div class="card">Every speaker is named. Nothing to review.</div>`}
      ${resolved.length ? `<h2>Identified</h2>${resolved.map(speakerCard).join("")}` : ""}
    </div>
    <datalist id="people-list">${review.people.map(p => `<option value="${esc(p.name)}">`).join("")}</datalist>
    <div class="applybar">
      <span class="hint">Names are saved only when you apply. Confirmed voices are added to that person's profile.</span>
      <button class="btn primary" id="apply" ${count ? "" : "disabled"}>Apply${count ? ` ${count} change${count > 1 ? "s" : ""}` : ""}</button>
    </div>`;
  bindReview();
}

function refreshCard(speaker) {
  const card = document.getElementById(`card-${speaker}`);
  const spk = review.meeting.speakers.find(s => s.speaker === speaker);
  const d = review.decisions[speaker];
  card.classList.toggle("decided", Boolean(d));
  card.querySelectorAll("[data-suggest]").forEach(b => b.classList.toggle("on", d?.type === "name" && d.name === b.dataset.name));
  let line = card.querySelector(".decision");
  if (!line) {
    line = document.createElement("div");
    line.className = "decision";
    card.appendChild(line);
  }
  line.innerHTML = decisionText(spk, d);
  const count = Object.keys(review.decisions).length;
  const button = document.getElementById("apply");
  button.disabled = !count;
  button.textContent = count ? `Apply ${count} change${count > 1 ? "s" : ""}` : "Apply";
}

function setDecision(speaker, decision) {
  if (decision) review.decisions[speaker] = decision;
  else delete review.decisions[speaker];
  drawReview();
}

function bindReview() {
  view.querySelectorAll("[data-expand]").forEach(b => b.onclick = () => {
    review.expanded.add(b.dataset.expand);
    drawReview();
  });
  view.querySelectorAll("[data-suggest]").forEach(b => b.onclick = () => {
    const enroll = !view.querySelector(`[data-noenroll="${b.dataset.suggest}"]`)?.checked;
    setDecision(b.dataset.suggest, { type: "name", name: b.dataset.name, enroll });
  });
  // typing updates the card in place, a full redraw here would swallow a click on apply
  view.querySelectorAll("[data-name-input]").forEach(input => input.oninput = () => {
    const name = input.value.trim();
    const speaker = input.dataset.nameInput;
    const enroll = !view.querySelector(`[data-noenroll="${speaker}"]`)?.checked;
    if (name) review.decisions[speaker] = { type: "name", name, enroll };
    else delete review.decisions[speaker];
    refreshCard(speaker);
  });
  view.querySelectorAll("[data-same]").forEach(sel => sel.onchange = () => {
    setDecision(sel.dataset.same, sel.value ? { type: "same", target: sel.value } : null);
  });
  view.querySelectorAll("[data-noenroll]").forEach(box => box.onchange = () => {
    const d = review.decisions[box.dataset.noenroll];
    if (d?.type === "name") { d.enroll = !box.checked; drawReview(); }
  });
  view.querySelectorAll("[data-skip]").forEach(b => b.onclick = () => {
    const current = review.decisions[b.dataset.skip];
    setDecision(b.dataset.skip, current?.type === "skip" ? null : { type: "skip" });
  });
  view.querySelectorAll("[data-clear]").forEach(b => b.onclick = () => setDecision(b.dataset.clear, null));
  view.querySelectorAll(".play").forEach(b => b.onclick = () => playClip(b));
  document.getElementById("apply").onclick = applyReview;
}

function playClip(button) {
  const spk = review.meeting.speakers.find(s => s.speaker === button.dataset.speaker);
  const clip = spk.clips[Number(button.dataset.clip)];
  const wasPlaying = button.classList.contains("playing");
  stopAudio();
  if (wasPlaying) return;
  const params = new URLSearchParams({ speaker: spk.speaker, start: clip.start, end: clip.end });
  player.src = `/api/meetings/${encodeURIComponent(review.meeting.meeting_id)}/clip?${params}`;
  player.onended = () => button.classList.remove("playing");
  player.play();
  button.classList.add("playing");
}

async function applyReview() {
  const body = { names: {}, no_enroll: [], skip: [], merges: [] };
  for (const [speaker, d] of Object.entries(review.decisions)) {
    if (d.type === "name") {
      body.names[speaker] = d.name;
      if (!d.enroll) body.no_enroll.push(speaker);
    } else if (d.type === "skip") {
      body.skip.push(speaker);
    } else if (d.type === "same") {
      if (review.decisions[d.target]?.type === "same" && review.decisions[d.target].target === speaker) {
        toast("Two speakers are each marked as the same as the other, keep only one");
        return;
      }
      body.merges.push([speaker, d.target]);
    }
  }
  // merge chains resolve in order, so a speaker merged into one that is itself merged goes first
  body.merges.sort((a, b) => (body.merges.some(m => m[0] === a[1]) ? -1 : 0) - (body.merges.some(m => m[0] === b[1]) ? -1 : 0));
  const button = document.getElementById("apply");
  button.disabled = true;
  button.textContent = "Saving";
  stopAudio();
  try {
    const meeting = await post(`/api/meetings/${encodeURIComponent(review.meeting.meeting_id)}/apply`, body);
    review = { meeting, people: await api("/api/people"), decisions: {}, expanded: new Set() };
    drawReview();
    toast(meeting.status === "done" ? "Saved. The transcript is updated." : "Saved. Some speakers still need a name.");
  } catch (err) {
    toast(`Could not apply: ${err.message}`);
    drawReview();
  }
}

// transcript
async function renderTranscript(id) {
  view.innerHTML = `<div class="page"><div class="empty">Loading transcript</div></div>`;
  const [meeting, data] = await Promise.all([
    api(`/api/meetings/${encodeURIComponent(id)}`), api(`/api/meetings/${encodeURIComponent(id)}/transcript`)
  ]);
  view.innerHTML = `
    <div class="page">
      <a class="crumb" href="#/inbox">Inbox</a>
      <h1>${esc(meeting.title || id)}</h1>
      <div class="sub">${esc(meeting.date || "")} · ${esc(meeting.speakers.map(s => s.label).join(", "))}</div>
      <div class="tools">
        <button class="btn small" id="reveal">Show in Finder</button>
        <button class="btn small" id="copy">Copy markdown</button>
        <a class="btn small" href="${meetingLink("review", id)}">Speakers</a>
        <span class="hint">Click a line to play from there</span>
      </div>
      <div id="turns">${data.turns.map((t, i) => `
        <div class="turn" data-i="${i}" data-start="${t.start}" data-end="${t.end}">
          <div class="who">${esc(t.name)}<span class="at">${fmtTime(t.start)}</span></div>
          <div>${esc(t.text)}</div>
        </div>`).join("")}</div>
    </div>`;
  const src = `/api/meetings/${encodeURIComponent(id)}/audio`;
  view.querySelectorAll(".turn").forEach(el => el.onclick = () => {
    if (!player.src.endsWith(src)) player.src = src;
    player.currentTime = Number(el.dataset.start);
    player.play();
  });
  player.ontimeupdate = () => {
    const now = player.currentTime;
    view.querySelectorAll(".turn").forEach(el => el.classList.toggle(
      "current", !player.paused && now >= Number(el.dataset.start) && now < Number(el.dataset.end) + 0.5));
  };
  document.getElementById("reveal").onclick = () => post(`/api/meetings/${encodeURIComponent(id)}/reveal`);
  document.getElementById("copy").onclick = async () => {
    await navigator.clipboard.writeText(data.markdown);
    toast("Copied");
  };
}

// people
async function renderPeople() {
  const people = await api("/api/people");
  view.innerHTML = `
    <div class="page">
      <h1>People</h1>
      <div class="sub">Voice profiles stored in data/library.db on this Mac. Renaming updates every past transcript.</div>
      <table>
        <tr><th>Name</th><th class="num">Meetings</th><th class="num">Minutes heard</th><th class="num">Profile audio</th><th>Sources</th><th>Last heard</th><th></th></tr>
        ${people.map(p => `<tr>
          <td data-name="${p.person_id}">${esc(p.name)}</td>
          <td class="num">${p.meetings}</td>
          <td class="num">${Math.round(p.minutes_heard)}</td>
          <td class="num">${p.entries} clip${p.entries === 1 ? "" : "s"}, ${Math.round(p.profile_seconds / 60 * 10) / 10} min</td>
          <td>${esc((p.sources || "").replaceAll(",", ", "))}</td>
          <td>${esc(p.last_heard || "")}</td>
          <td><button class="link" data-rename="${p.person_id}">Rename</button></td>
        </tr>`).join("")}
      </table>
    </div>`;
  view.querySelectorAll("[data-rename]").forEach(b => b.onclick = () => {
    const cell = view.querySelector(`[data-name="${b.dataset.rename}"]`);
    const old = cell.textContent;
    cell.innerHTML = `<input type="text" value="${esc(old)}" style="width:140px"> <button class="btn small">Save</button>`;
    const input = cell.querySelector("input");
    input.focus();
    const save = async () => {
      const name = input.value.trim();
      if (!name || name === old) return renderPeople();
      try {
        await post(`/api/people/${b.dataset.rename}/rename`, { name });
        toast(`Renamed to ${name}. Transcripts updated.`);
      } catch (err) {
        toast(err.message);
      }
      renderPeople();
    };
    cell.querySelector("button").onclick = save;
    input.onkeydown = e => { if (e.key === "Enter") save(); if (e.key === "Escape") renderPeople(); };
  });
}

// main
function route() {
  clearInterval(pollTimer);
  stopAudio();
  player.ontimeupdate = null;
  const [, page, id] = location.hash.split("/");
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle(
    "active", a.dataset.nav === (page === "people" ? "people" : "inbox")));
  const run = page === "review" ? renderReview(decodeURIComponent(id))
    : page === "transcript" ? renderTranscript(decodeURIComponent(id))
    : page === "people" ? renderPeople()
    : renderInbox();
  run.catch(err => { view.innerHTML = `<div class="page"><div class="card">Something went wrong: ${esc(err.message)}</div></div>`; });
}

// keep the window from opening a file dropped outside the drop zone
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => e.preventDefault());
window.addEventListener("hashchange", route);
if (!location.hash) location.hash = "#/inbox";
route();
api("/api/state").then(s => updateCount(s.meetings)).catch(() => {});
