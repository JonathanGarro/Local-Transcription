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
    const error = new Error(detail);
    error.status = response.status;
    throw error;
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

  if (renaming) return;
  const writing = new Set(state.jobs.filter(j => j.kind === "note" && ["queued", "processing"].includes(j.status)).map(j => j.meeting_id));
  const inbox = state.meetings.filter(m => !m.archived);
  meetingsEl.innerHTML = inbox.length ? inbox.map(m => {
    const review = m.status !== "done";
    const stage = review ? ["review", "needs review"] : writing.has(m.meeting_id) ? ["work", "writing note"]
      : m.note ? ["ready", "ready to send"] : ["review", "no note yet"];
    return `<div class="item">
      <div class="main">
        <div class="title"><span class="rename" data-rename="${esc(m.meeting_id)}" title="Click to rename">${esc(m.title || m.meeting_id)}</span></div>
        <div class="meta">${esc(m.date || "")} · ${m.minutes ? Math.round(m.minutes) + " min" : ""} · ${esc(m.source_type || "")}${m.split ? " · split channels" : ""}</div>
      </div>
      <span class="badge ${stage[0]}">${stage[1]}</span>
      <a class="btn small" href="${meetingLink("review", m.meeting_id)}">${review ? "Review" : "Speakers"}</a>
      <a class="btn small" href="${meetingLink("transcript", m.meeting_id)}">Transcript</a>
      ${review ? "" : `<a class="btn small" href="${meetingLink("transcript", m.meeting_id)}/note">Note</a>`}
    </div>`;
  }).join("") : `<div class="empty">${state.meetings.length ? "Nothing waiting. Sent meetings are in the Archive." : "No meetings yet"}</div>`;
  meetingsEl.querySelectorAll("[data-rename]").forEach(el => renamable(el, el.dataset.rename));
}

// click a meeting title to rename it. the inbox poll leaves the list alone while editing
let renaming = false;

// onSaved runs after a rename, for pages that show the title elsewhere too
function renamable(el, id, onSaved) {
  el.classList.add("rename");
  el.title = "Click to rename";
  el.onclick = () => {
    if (renaming) return;
    renaming = true;
    const before = el.textContent;
    const input = document.createElement("input");
    input.type = "text";
    input.className = "rename-input";
    input.value = before;
    el.replaceWith(input);
    input.focus();
    input.select();
    let finished = false;
    const finish = async save => {
      if (finished) return;
      finished = true;
      const value = input.value.trim();
      if (save && value && value !== before) {
        try {
          // save note typing first, or it would write the old heading back over the new one
          await flushNote?.();
          const saved = (await post(`/api/meetings/${encodeURIComponent(id)}/title`, { name: value })).title;
          el.textContent = saved;
          if (review?.meeting?.meeting_id === id) review.meeting.title = saved;
          toast("Renamed");
          onSaved?.();
        } catch (err) {
          toast(`Rename failed: ${err.message}`);
        }
      }
      input.replaceWith(el);
      renaming = false;
    };
    input.onkeydown = e => {
      if (e.key === "Enter") finish(true);
      if (e.key === "Escape") finish(false);
    };
    input.onblur = () => finish(true);
  };
}

function crumb(meeting) {
  return meeting.archived ? `<a class="crumb" href="#/archive">Archive</a>` : `<a class="crumb" href="#/inbox">Inbox</a>`;
}

// archive
async function renderArchive() {
  const state = await api("/api/state");
  const sent = state.meetings.filter(m => m.archived)
    .sort((a, b) => (b.date || "").localeCompare(a.date || "") || (b.archived || "").localeCompare(a.archived || ""));
  view.innerHTML = `
    <div class="page">
      <h1>Archive</h1>
      <div class="sub">Meetings whose notes were sent to Obsidian, and notes imported from it.</div>
      ${sent.length ? `<div class="tools"><input type="text" id="archive-filter" placeholder="Filter by title or project"></div>` : ""}
      <div class="list" id="archive-list"></div>
    </div>`;
  const list = document.getElementById("archive-list");
  // imported notes have no recording, so only the note opens. once their recording is
  // processed they have a transcript and speakers, but the note stays the obsidian one
  const row = m => m.source_type === "obsidian" ? `<div class="item">
      <div class="main">
        <div class="title">${esc(m.title || m.meeting_id)}</div>
        <div class="meta">${esc(m.date || "")} · imported from Obsidian${m.project ? ` · ${esc(m.project)}` : ""}</div>
      </div>
      <a class="btn small" href="${meetingLink("imported", m.meeting_id)}">Note</a>
    </div>` : `<div class="item">
      <div class="main">
        <div class="title">${esc(m.title || m.meeting_id)}</div>
        <div class="meta">${esc(m.date || "")} · ${m.note ? `sent ${esc((m.archived || "").slice(0, 10))} as ${esc((m.sent_path || "").split("/").pop())}`
          : "imported from Obsidian, recording processed here"}${m.project ? ` · ${esc(m.project)}` : ""}</div>
      </div>
      ${m.status !== "done" ? `<span class="badge review">needs review</span>
        <a class="btn small" href="${meetingLink("review", m.meeting_id)}">Review</a>` : ""}
      <a class="btn small" href="${meetingLink("transcript", m.meeting_id)}">Transcript</a>
      ${m.note ? `<a class="btn small" href="${meetingLink("transcript", m.meeting_id)}/note">Note</a>
        <button class="btn small" data-unarchive="${esc(m.meeting_id)}">Move to Inbox</button>`
        : `<a class="btn small" href="${meetingLink("imported", m.meeting_id)}">Note</a>`}
    </div>`;
  const draw = () => {
    const query = (document.getElementById("archive-filter")?.value || "").trim().toLowerCase();
    const shown = sent.filter(m => !query || `${m.title} ${m.project || ""}`.toLowerCase().includes(query));
    list.innerHTML = shown.length ? shown.map(row).join("")
      : `<div class="empty">${sent.length ? "No matches" : "Nothing sent yet"}</div>`;
    list.querySelectorAll("[data-unarchive]").forEach(b => b.onclick = async () => {
      await post(`/api/meetings/${encodeURIComponent(b.dataset.unarchive)}/unarchive`, {});
      toast("Moved to the Inbox");
      renderArchive();
    });
  };
  const filter = document.getElementById("archive-filter");
  if (filter) filter.oninput = draw;
  draw();
}

// a note imported from obsidian, read from the vault. obsidian is the only copy, so it is not editable here
async function renderImported(id) {
  const enc = encodeURIComponent(id);
  const m = await api(`/api/meetings/${enc}/imported`);
  view.innerHTML = `
    <div class="page">
      <a class="crumb" href="#/archive">Archive</a>
      <h1>${esc(m.title || id)}</h1>
      <div class="sub">${esc(m.date || "")}${m.people.length ? ` · ${esc(m.people.join(", "))}` : ""}${m.project ? ` · ${esc(m.project)}` : ""}</div>
      <div class="tools">
        <button class="btn small" id="open-obsidian">Open in Obsidian</button>
        <button class="btn small" id="copy-note" ${m.markdown == null ? "disabled" : ""}>Copy note</button>
        ${m.processed ? `<a class="btn small" href="${meetingLink("transcript", id)}">Transcript</a>
          <a class="btn small" href="${meetingLink("review", id)}">Speakers</a>` : ""}
        <span class="hint">${m.processed ? "Imported from Obsidian, recording processed here." : "Imported from Obsidian with no recording."} Edit the note in Obsidian.</span>
      </div>
      ${m.markdown == null ? `<div class="card">The note was moved or deleted in Obsidian: ${esc(m.sent_path)}</div>`
        : `<textarea class="note" id="note-text" readonly>${esc(m.markdown)}</textarea>`}
    </div>`;
  const text = document.getElementById("note-text");
  if (text) requestAnimationFrame(() => { text.style.height = `${text.scrollHeight + 2}px`; });
  document.getElementById("open-obsidian").onclick = () =>
    post(`/api/meetings/${enc}/open-obsidian`, {}).catch(err => toast(err.message));
  document.getElementById("copy-note").onclick = async () => {
    await navigator.clipboard.writeText(m.markdown);
    toast("Copied");
  };
}

function updateCount(meetings) {
  const n = meetings.filter(m => m.status !== "done" && !m.archived).length;
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
    `<button class="btn small ${d?.type === "name" && d.name === s.name ? "on" : ""}" data-suggest="${spk.speaker}" data-name="${esc(s.name)}">${esc(s.name)} <span class="hint">${s.source === "note" ? `from note ${pct(s.score)}` : pct(s.score)}</span></button>`).join("");
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
      ${crumb(m)}
      <h1><span data-rename="${esc(m.meeting_id)}">${esc(m.title || m.meeting_id)}</span></h1>
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
  view.querySelectorAll("h1 [data-rename]").forEach(el => renamable(el, el.dataset.rename));
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

// transcript and note
async function renderTranscript(id, tab = "transcript") {
  view.innerHTML = `<div class="page"><div class="empty">Loading transcript</div></div>`;
  const enc = encodeURIComponent(id);
  const [meeting, data] = await Promise.all([api(`/api/meetings/${enc}`), api(`/api/meetings/${enc}/transcript`)]);
  view.innerHTML = `
    <div class="page">
      ${crumb(meeting)}
      <h1><span data-rename="${esc(id)}">${esc(meeting.title || id)}</span></h1>
      <div class="sub">${esc(meeting.date || "")} · ${esc(meeting.speakers.map(s => s.label).join(", "))}</div>
      <div class="tabs">
        <a href="${meetingLink("transcript", id)}" class="${tab === "transcript" ? "active" : ""}">Transcript</a>
        <a href="${!meeting.note && meeting.sent_path ? meetingLink("imported", id) : `${meetingLink("transcript", id)}/note`}" class="${tab === "note" ? "active" : ""}">Note</a>
        <a href="${meetingLink("review", id)}">Speakers</a>
      </div>
      <div id="tab"></div>
    </div>`;
  // the transcript and note headings carry the title, so reload the tab after a rename
  view.querySelectorAll("h1 [data-rename]").forEach(el => renamable(el, el.dataset.rename, route));
  const body = document.getElementById("tab");
  if (tab === "note") return renderNote(id, body);

  body.innerHTML = `
    <div class="tools">
      <button class="btn small" id="reveal">Show in Finder</button>
      <button class="btn small" id="copy">Copy markdown</button>
      <span class="hint">Click a line to play from there</span>
    </div>
    <div id="turns">${data.turns.map((t, i) => `
      <div class="turn" data-i="${i}" data-start="${t.start}" data-end="${t.end}">
        <div class="who">${esc(t.name)}<span class="at">${fmtTime(t.start)}</span></div>
        <div>${esc(t.text)}</div>
      </div>`).join("")}</div>`;
  const src = `/api/meetings/${enc}/audio`;
  body.querySelectorAll(".turn").forEach(el => el.onclick = () => {
    if (!player.src.endsWith(src)) player.src = src;
    player.currentTime = Number(el.dataset.start);
    player.play();
  });
  player.ontimeupdate = () => {
    const now = player.currentTime;
    body.querySelectorAll(".turn").forEach(el => el.classList.toggle(
      "current", !player.paused && now >= Number(el.dataset.start) && now < Number(el.dataset.end) + 0.5));
  };
  document.getElementById("reveal").onclick = () => post(`/api/meetings/${enc}/reveal`);
  document.getElementById("copy").onclick = async () => {
    await navigator.clipboard.writeText(data.markdown);
    toast("Copied");
  };
}

// project search: in-progress projects first, then a to z (the server sends that order).
// a name that is not in the list is still allowed and gets added
function projectPicker(input, list, projects) {
  let shown = [], active = -1;
  const draw = () => {
    const query = input.value.trim().toLowerCase();
    shown = projects.filter(p => !query || p.name.toLowerCase().includes(query));
    active = Math.min(active, shown.length - 1);
    const live = shown.filter(p => p.status === "In-Progress");
    const rest = shown.filter(p => p.status !== "In-Progress");
    const row = p => {
      const i = shown.indexOf(p);
      return `<div class="combo-item ${i === active ? "active" : ""}" data-i="${i}">
        <span>${esc(p.name)}</span>${p.status && p.status !== "In-Progress" ? `<span class="hint">${esc(p.status)}</span>` : ""}</div>`;
    };
    list.innerHTML = (live.length ? `<div class="combo-head">In progress</div>${live.map(row).join("")}` : "")
      + (rest.length ? `<div class="combo-head">${live.length ? "Everything else" : "Projects"}</div>${rest.map(row).join("")}` : "")
      + (query && !projects.some(p => p.name.toLowerCase() === query) ? `<div class="combo-new">Enter adds "${esc(input.value.trim())}" as a new project</div>` : "");
    list.hidden = !list.innerHTML;
    list.querySelector(".combo-item.active")?.scrollIntoView({ block: "nearest" });
  };
  const pick = p => {
    input.value = p.name;
    list.hidden = true;
    input.dispatchEvent(new Event("change"));
  };
  input.onfocus = () => { active = -1; draw(); };
  input.oninput = () => { active = -1; draw(); };
  input.onblur = () => setTimeout(() => { list.hidden = true; }, 150);
  input.onkeydown = e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) draw();
      active = Math.max(0, Math.min(shown.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
      draw();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && shown[active]) pick(shown[active]);
      else { list.hidden = true; input.blur(); }
    } else if (e.key === "Escape") {
      list.hidden = true;
    }
  };
  list.onmousedown = e => {
    const item = e.target.closest(".combo-item");
    if (item) { e.preventDefault(); pick(shown[+item.dataset.i]); }
  };
}

// git style view of what was edited, with changed words highlighted inside each line
// what the send card says about a sent note, depending on which copy changed
function vaultMessage(note) {
  const sent = `<b>Sent to Obsidian</b> on ${esc((note.archived || "").slice(0, 10) || "an earlier day")} as ${esc(note.sent_path.split("/").pop())}.`;
  return {
    same: `${sent} This matches the Obsidian copy, which is the master copy now. Edit it in Obsidian.`,
    pulled: `${sent} The Obsidian copy had changed, so it was loaded here.`,
    "app-ahead": `${sent} You edited it here after sending. Send again to update Obsidian, or use the Obsidian version to drop these edits. Show changes compares the two.`,
    conflict: `${sent} <b>It changed in both places.</b> Obsidian has edits that are not here, and this copy has edits that are not in Obsidian. Show changes compares them. Use the Obsidian version to keep its edits, or send again to replace them.`,
    missing: `${sent} The Obsidian copy was moved or deleted. Send again to put it back.`,
  }[note.vault] || sent;
}

function diffView(diff) {
  if (diff.base === "obsidian" && !diff.rows.length) return `<div class="card">This matches the Obsidian copy.</div>`;
  const base = diff.base === "obsidian" ? "the Obsidian copy (red is Obsidian, green is this copy)"
    : diff.base === "original" ? "the note before your first edit"
    : "a fresh copy of the generated note. This note was edited before the app kept the original, so format updates since then show up too";
  if (!diff.rows.length) return `<div class="card">No changes compared with ${base}.</div>`;
  const sign = { add: "+", del: "-", ctx: " " };
  const rows = diff.rows.map(r => r.kind === "gap"
    ? `<div class="diff-row gap"><span class="ln"></span><span class="ln"></span><span class="sign"></span><span class="code">@@ line ${r.new}</span></div>`
    : `<div class="diff-row ${r.kind}"><span class="ln">${r.old || ""}</span><span class="ln">${r.new || ""}</span><span class="sign">${sign[r.kind]}</span><span class="code">${r.parts.map(([t, changed]) => changed ? `<mark>${esc(t)}</mark>` : esc(t)).join("") || " "}</span></div>`).join("");
  return `<div class="hint diff-summary"><span class="plus">+${diff.added}</span> <span class="minus">-${diff.removed}</span> lines, compared with ${base}.</div>
    <div class="diff">${rows}</div>`;
}

// the note text box saves itself a moment after typing stops, and when it loses focus
let flushNote = null;
let noteSaved = null;

function noteEditor(text, enc, status, path) {
  let timer = null, saved = text.value;
  const grow = () => {
    text.style.height = "auto";
    text.style.height = `${text.scrollHeight + 2}px`;
  };
  const save = async () => {
    clearTimeout(timer);
    if (text.value === saved) return;
    const value = text.value;
    status.textContent = "Saving";
    try {
      await post(`/api/meetings/${enc}/note/text`, { markdown: value });
      saved = value;
      status.textContent = `Saved to ${path}`;
      noteSaved?.();
    } catch (err) {
      status.textContent = `Not saved: ${err.message}`;
    }
  };
  text.oninput = () => {
    grow();
    status.textContent = "Editing";
    clearTimeout(timer);
    timer = setTimeout(save, 800);
  };
  text.onblur = save;
  text.onkeydown = e => {
    // tab indents like obsidian sub-bullets, cmd+s saves right away
    if (e.key === "Tab") {
      e.preventDefault();
      document.execCommand("insertText", false, "\t");
    } else if (e.key === "s" && e.metaKey) {
      e.preventDefault();
      save();
    }
  };
  requestAnimationFrame(grow);
  return save;
}

async function renderNote(id, body) {
  await flushNote?.();
  flushNote = null;
  noteSaved = null;
  const enc = encodeURIComponent(id);
  const [note, lookups] = await Promise.all([api(`/api/meetings/${enc}/note`), api("/api/lookups")]);
  const job = note.job;
  const backend = note.backend || lookups.default_backend;
  body.innerHTML = `
    <div class="card">
      <div class="form">
        <label>Model
          <select id="backend">
            <option value="local" ${backend === "local" ? "selected" : ""}>Local, on this Mac</option>
            <option value="claude" ${backend === "claude" ? "selected" : ""} ${lookups.claude_ready ? "" : "disabled"}>Claude API, sends the transcript text${lookups.claude_ready ? "" : " (needs ANTHROPIC_API_KEY)"}</option>
          </select>
        </label>
        <label>Project
          <span class="combo">
            <input type="text" id="project" autocomplete="off" value="${esc(note.project || "")}"
              placeholder="${lookups.projects.length ? "Search projects" : "None yet, type to add"}">
            <div class="combo-list" id="project-list" hidden></div>
          </span>
        </label>
        <button class="btn ${note.exists ? "" : "primary"}" id="write" ${job ? "disabled" : ""}>${note.exists ? "Rewrite note" : "Write note"}</button>
      </div>
      ${job ? `<div class="meta" style="margin-top:12px">${job.status === "queued" ? "Waiting for the current job" : esc(job.step)}${job.detail ? `, ${esc(job.detail)}` : ""}</div>
        <div class="bar"><div style="width:${Math.round(job.progress * 100)}%"></div></div>` : ""}
      ${note.exists && !job ? `<div class="hint" style="margin-top:10px">Written by ${esc(note.model)} on ${esc((note.created || "").replace("T", " "))}. ${note.edited ? `You edited it on ${esc(note.edited.replace("T", " "))}. Your edits are kept, and changing the project only updates its line.` : "Click the note to edit it. Changing the project updates the note without rewriting it."}</div>` : ""}
    </div>
    ${note.exists && !job ? `<div class="card send">
        <div class="main">
          ${note.sent_path ? vaultMessage(note)
          : `<b>Review the note, then send it.</b> It is saved to ${note.notes_dir ? esc(note.notes_dir.split("/").slice(-2).join("/")) : "your meeting notes folder"} and the meeting moves to the Archive.`}
          <div class="hint" id="send-status"></div>
        </div>
        ${["app-ahead", "conflict"].includes(note.vault) ? `<button class="btn" id="pull-note">Use Obsidian version</button>` : ""}
        <button class="btn ${note.sent_path && note.vault !== "app-ahead" && note.vault !== "missing" ? "" : "primary"}" id="send-note" ${note.notes_dir ? "" : "disabled title=\"Set MEETING_NOTES_DIR in .env\""}>${note.sent_path ? "Send again" : "Send to Obsidian"}</button>
      </div>` : ""}
    ${note.exists ? `<div class="tools">
        <button class="btn small" id="open-obsidian" ${note.sent_path ? "" : "disabled title=\"Send the note to Obsidian first\""}>Open in Obsidian</button>
        <button class="btn small" id="copy-note">Copy note</button>
        <button class="btn small" id="reveal-note">Show in Finder</button>
        ${note.sent_path ? `<button class="btn small" id="show-changes">Show changes</button>`
          : note.edited ? `<button class="btn small" id="show-changes">Show changes</button>
          <button class="btn small" id="discard-note">Discard my edits</button>` : ""}
        <span class="hint" id="note-status">${esc(note.path)}</span>
      </div>
      <div id="note-diff" hidden></div>
      <textarea class="note" id="note-text" spellcheck="true">${esc(note.markdown)}</textarea>` : job ? "" : `<div class="empty">No note yet. Names come from the confirmed speakers, so finish the review first.</div>`}`;

  const writeButton = document.getElementById("write");
  writeButton.onclick = async () => {
    // rewriting replaces hand edits, so ask with a second click
    if (note.edited && !writeButton.dataset.confirm) {
      writeButton.dataset.confirm = "1";
      writeButton.textContent = "Replace my edits?";
      setTimeout(() => {
        delete writeButton.dataset.confirm;
        writeButton.textContent = "Rewrite note";
      }, 4000);
      return;
    }
    await flushNote?.();
    const project = document.getElementById("project").value.trim();
    try {
      await post(`/api/meetings/${enc}/note`, { backend: document.getElementById("backend").value, project: project || null });
      renderNote(id, body);
    } catch (err) {
      toast(err.message);
    }
  };
  const projectInput = document.getElementById("project");
  projectPicker(projectInput, document.getElementById("project-list"), lookups.projects);
  projectInput.onchange = async () => {
    if (!note.exists || job) return;
    await flushNote?.();
    await post(`/api/meetings/${enc}/project`, { name: projectInput.value.trim() });
    toast(projectInput.value.trim() ? `Filed under ${projectInput.value.trim()}` : "Project cleared");
    renderNote(id, body);
  };
  if (note.exists) {
    const text = document.getElementById("note-text");
    flushNote = noteEditor(text, enc, document.getElementById("note-status"), note.path);
    document.getElementById("copy-note").onclick = async () => {
      await navigator.clipboard.writeText(text.value);
      toast("Copied");
    };
    const send = document.getElementById("send-note");
    if (send) send.onclick = async () => {
      const replace = send.dataset.confirm === "1";
      await flushNote?.();
      try {
        const result = await post(`/api/meetings/${enc}/note/send`, { replace });
        toast(note.sent_path ? "Sent again" : "Sent to Obsidian and moved to the Archive");
        // the output folder was renamed to match the note, so the meeting has a new id
        location.hash = `${meetingLink("transcript", result.meeting_id)}/note`;
        if (result.meeting_id === id) route();
      } catch (err) {
        if (err.status !== 409) return toast(err.message);
        // a note with this name is already in the vault, replace only on a second click
        send.dataset.confirm = "1";
        send.textContent = "Replace it";
        document.getElementById("send-status").textContent = `${err.message}. Click Replace it to overwrite that file, or change the heading first.`;
      }
    };
    const changes = document.getElementById("show-changes");
    const panel = document.getElementById("note-diff");
    const drawDiff = async () => { panel.innerHTML = diffView(await api(`/api/meetings/${enc}/note/diff`)); };
    // while the changes are open they refresh each time the note saves
    noteSaved = () => { if (!panel.hidden) drawDiff(); };
    if (changes) changes.onclick = async () => {
      const showing = panel.hidden;
      if (showing) {
        await flushNote?.();
        await drawDiff();
      }
      panel.hidden = !showing;
      changes.textContent = showing ? "Hide changes" : "Show changes";
      changes.classList.toggle("on", showing);
    };
    const pull = document.getElementById("pull-note");
    if (pull) pull.onclick = async () => {
      // replaces edits made here, so ask with a second click
      if (!pull.dataset.confirm) {
        pull.dataset.confirm = "1";
        pull.textContent = "Replace the app copy?";
        setTimeout(() => { delete pull.dataset.confirm; pull.textContent = "Use Obsidian version"; }, 4000);
        return;
      }
      flushNote = null;
      await post(`/api/meetings/${enc}/note/pull`, {});
      toast("Loaded the Obsidian version");
      renderNote(id, body);
    };
    if (note.vault === "pulled") toast("Loaded the newer version from Obsidian");
    const discard = document.getElementById("discard-note");
    if (discard) discard.onclick = async () => {
      if (!discard.dataset.confirm) {
        discard.dataset.confirm = "1";
        discard.textContent = "Discard them?";
        setTimeout(() => { delete discard.dataset.confirm; discard.textContent = "Discard my edits"; }, 4000);
        return;
      }
      flushNote = null;
      await post(`/api/meetings/${enc}/note/discard`, {});
      toast("Back to the written note");
      renderNote(id, body);
    };
    document.getElementById("reveal-note").onclick = () => post(`/api/meetings/${enc}/reveal`, {});
    document.getElementById("open-obsidian").onclick = () =>
      post(`/api/meetings/${enc}/open-obsidian`, {}).catch(err => toast(err.message));
  }
  if (job) {
    pollTimer = setTimeout(() => { if (document.body.contains(body)) renderNote(id, body); }, 1500);
  }
}

// people
async function renderPeople(editing = null) {
  const [people, lookups] = await Promise.all([api("/api/people"), api("/api/lookups")]);
  view.innerHTML = `
    <div class="page">
      <h1>People</h1>
      <div class="sub">Names and voices are stored in data/library.db on this Mac. Organizations and teams come from each person's note in Obsidian, and saving them here writes them there. Changes update every past transcript and note.</div>
      <table>
        <tr><th>Name</th><th>Organizations</th><th>Teams</th><th class="num">Meetings</th><th class="num">Voice clips</th><th>Last heard</th><th></th></tr>
        ${people.map(p => p.person_id === editing ? editRow(p) : `<tr>
          <td><button class="link expand" data-person="${p.person_id}" aria-expanded="false">${esc(p.name)}</button></td>
          <td>${esc(p.organizations || "")}</td>
          <td>${esc(p.teams || "")}</td>
          <td class="num">${p.meetings}</td>
          <td class="num" title="${Math.round(p.profile_seconds / 60 * 10) / 10} min of audio">${p.entries}</td>
          <td>${esc(p.last_heard || "")}</td>
          <td><button class="link" data-edit="${p.person_id}">Edit</button></td>
        </tr>`).join("")}
      </table>
      <datalist id="org-list">${lookups.organizations.map(o => `<option value="${esc(o)}">`).join("")}</datalist>
      <datalist id="team-list">${lookups.teams.map(t => `<option value="${esc(t)}">`).join("")}</datalist>
    </div>`;
  view.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => renderPeople(b.dataset.edit));
  view.querySelectorAll("[data-person]").forEach(b => b.onclick = () => togglePerson(b));
  if (editing) bindEdit(people.find(p => p.person_id === editing));
}

const TAGGED = { confirmed: "confirmed", matched: "voice match", mic: "mic", imported: "from note", skipped: "left unnamed" };

// the meetings a person is tagged in, in a row that opens under theirs
async function togglePerson(button) {
  const row = button.closest("tr");
  if (row.nextElementSibling?.classList.contains("meetings-row")) {
    row.nextElementSibling.remove();
    button.setAttribute("aria-expanded", "false");
    return;
  }
  button.setAttribute("aria-expanded", "true");
  const detail = document.createElement("tr");
  detail.className = "meetings-row";
  detail.innerHTML = `<td colspan="7"><div class="empty">Loading</div></td>`;
  row.after(detail);
  const meetings = await api(`/api/people/${button.dataset.person}/meetings`);
  // imported notes without a processed recording only have the note to open
  const link = m => meetingLink(m.source_type === "obsidian" ? "imported" : "transcript", m.meeting_id);
  detail.firstElementChild.innerHTML = meetings.length ? `<table class="inner">
      <tr><th>Date</th><th>Meeting</th><th>Project</th><th>Tagged by</th><th class="num">Minutes</th></tr>
      ${meetings.map(m => `<tr>
        <td>${esc(m.date || "")}</td>
        <td><a href="${link(m)}">${esc(m.title || m.meeting_id)}</a>${m.meeting_status === "needs review"
          ? ` <span class="badge review">needs review</span>` : ""}</td>
        <td>${esc(m.project || "")}</td>
        <td>${esc((m.status || "").split(",").map(s => TAGGED[s] || s).join(", "))}</td>
        <td class="num">${m.minutes ? Math.round(m.minutes * 10) / 10 : ""}</td>
      </tr>`).join("")}
    </table>` : `<div class="empty">Not tagged in any meetings yet</div>`;
}

function editRow(p) {
  return `<tr class="editing"><td colspan="7">
    <div class="form">
      <label>Name<input type="text" id="e-name" value="${esc(p.name)}"></label>
    </div>
    <div class="form">
      <label>Organizations
        <span class="chips" id="e-orgs">
          <input type="text" id="e-org" list="org-list" placeholder="Add an organization, then Enter"></span>
      </label>
    </div>
    <div class="form">
      <label>Teams
        <span class="chips" id="e-teams">
          <input type="text" id="e-team" list="team-list" placeholder="Add a team, then Enter"></span>
      </label>
    </div>
    <div class="form">
      <button class="btn primary" id="e-save">Save</button>
      <button class="btn" id="e-cancel">Cancel</button>
      <span class="hint">The name shows in voice matching, transcripts, and notes, where it is a link.</span>
    </div>
  </td></tr>`;
}

// a list of chips with a text box that adds one on Enter. returns { values, add }
function bindChips(chipsId, inputId, initial) {
  const state = { values: initial ? initial.split(", ") : [] };
  const chips = document.getElementById(chipsId);
  const input = document.getElementById(inputId);
  const draw = () => {
    chips.querySelectorAll(".chip").forEach(c => c.remove());
    state.values.forEach(v => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `${esc(v)}<button title="Remove">x</button>`;
      chip.querySelector("button").onclick = () => { state.values = state.values.filter(x => x !== v); draw(); };
      chips.insertBefore(chip, input);
    });
  };
  state.add = () => {
    const value = input.value.trim();
    if (value && !state.values.some(v => v.toLowerCase() === value.toLowerCase())) state.values.push(value);
    input.value = "";
    draw();
  };
  draw();
  input.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); state.add(); } };
  input.onchange = state.add;
  return state;
}

function bindEdit(p) {
  const orgs = bindChips("e-orgs", "e-org", p.organizations);
  const teams = bindChips("e-teams", "e-team", p.teams);
  document.getElementById("e-cancel").onclick = () => renderPeople();
  document.getElementById("e-save").onclick = async () => {
    orgs.add();
    teams.add();
    const name = document.getElementById("e-name").value.trim();
    try {
      // details first, so they land in the note under the current name
      await post(`/api/people/${p.person_id}/details`, {
        organizations: orgs.values,
        teams: teams.values
      });
      if (name && name !== p.name) await post(`/api/people/${p.person_id}/rename`, { name });
      toast("Saved. Transcripts and notes updated.");
      renderPeople();
    } catch (err) {
      toast(err.message);
    }
  };
}

// main
function route() {
  // save any note edits before leaving the page
  flushNote?.();
  flushNote = null;
  clearInterval(pollTimer);
  clearTimeout(pollTimer);
  stopAudio();
  player.ontimeupdate = null;
  const [, page, id, tab] = location.hash.split("/");
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle(
    "active", a.dataset.nav === (["people", "archive"].includes(page) ? page : page === "imported" ? "archive" : "inbox")));
  const run = page === "review" ? renderReview(decodeURIComponent(id))
    : page === "transcript" ? renderTranscript(decodeURIComponent(id), tab === "note" ? "note" : "transcript")
    : page === "imported" ? renderImported(decodeURIComponent(id))
    : page === "people" ? renderPeople()
    : page === "archive" ? renderArchive()
    : renderInbox();
  run.catch(err => { view.innerHTML = `<div class="page"><div class="card">Something went wrong: ${esc(err.message)}</div></div>`; });
}

// keep the window from opening a file dropped outside the drop zone
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => e.preventDefault());
window.addEventListener("hashchange", route);

// in the app window the page runs under the title bar, so it leaves room for the window buttons
const framed = () => document.documentElement.classList.add("framed");
if (window.pywebview) framed();
window.addEventListener("pywebviewready", framed);

// scrollbars show only while scrolling
document.addEventListener("scroll", e => {
  const el = e.target === document ? document.documentElement : e.target;
  el.classList.add("scrolling");
  clearTimeout(el.scrollTimer);
  el.scrollTimer = setTimeout(() => el.classList.remove("scrolling"), 900);
}, true);
if (!location.hash) location.hash = "#/inbox";
route();
api("/api/state").then(s => updateCount(s.meetings)).catch(() => {});
