"use strict";
const $ = id => document.getElementById(id);
let state;
let current = "feed";
let filter = "All";
let busy = false;
let messageTimer;

function node(tag, text, style) {
  const item = document.createElement(tag);
  if (text) item.textContent = text;
  if (style) item.className = style;
  return item;
}

function link(label, url) {
  const anchor = node("a", label);
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  return anchor;
}

function updateMessage(message, persistent = false) {
  clearTimeout(messageTimer);
  $("message").textContent = message;
  $("message").hidden = !message;
  if (message && !persistent) messageTimer = setTimeout(() => { $("message").hidden = true; }, 4500);
}

async function api(path, data) {
  const response = await fetch(path, data === undefined ? undefined : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request failed.");
  return result;
}

async function run(button, message, fn) {
  if (busy) return;
  busy = true;
  button.disabled = true;
  updateMessage(message, true);
  try {
    updateMessage(await fn());
  } catch (error) {
    updateMessage(error.message, true);
  } finally {
    busy = false;
    button.disabled = false;
  }
}

function show(view) {
  current = view;
  document.querySelectorAll(".view").forEach(section => { section.hidden = section.id !== view; });
  document.querySelectorAll("nav button").forEach(button => {
    if (button.dataset.view === view) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => show(button.dataset.view)));

function matchCard(match) {
  const card = node("article", "", "card");
  const top = node("div", "", "card-top");
  const meta = node("div");
  meta.append(node("span", match.kind, "kind " + match.kind.toLowerCase()),
              document.createTextNode("  " + [match.source, match.date].filter(Boolean).join(" · ")));
  top.append(meta);
  if (match.read) top.append(node("span", "Read", "read-label"));
  else if (!state.demo) {
    const read = node("button", "Mark read", "mark-read");
    read.addEventListener("click", () => run(read, "Updating…", async () => {
      state = await api("/api/read", {key: match.key});
      renderFeed();
      return "Marked as read.";
    }));
    top.append(read);
  }
  card.append(top);
  const title = node("h3");
  if (match.url) title.append(link(match.title, match.url));
  else title.textContent = match.title;
  card.append(title);
  if (match.text) {
    const excerpt = match.text.length > 280 ? match.text.slice(0, 280).replace(/\s+\S*$/, "") + "…" : match.text;
    card.append(node("p", excerpt, "excerpt"));
  }
  const why = node("details");
  why.append(node("summary", "Why it matched"));
  const reasons = node("ul");
  for (const reason of match.reasons) {
    const evidence = node("li", reason.topic + " · " + reason.where);
    if (reason.work) evidence.append(node("p", "Also appears in your work “" + reason.work + "”", "hint"));
    reasons.append(evidence);
  }
  why.append(reasons);
  if (match.files?.length) why.append(node("p", "Listed files: " + match.files.join(", "), "hint"));
  card.append(why);
  return card;
}

function renderFeed() {
  $("demo-banner").hidden = !state.demo;
  $("refresh").hidden = state.demo;
  const results = state.feed.filter(record => filter === "All" || record.kind === filter);
  $("feed-count").textContent = results.length + (results.length === 1 ? " match" : " matches");
  $("matches").replaceChildren(...(results.length ? results.map(matchCard) :
    [node("p", state.report.time ? "No matches in this view yet." : "Refresh the feed to look for your first matches.", "empty")]));
  const statuses = $("scan-status");
  statuses.replaceChildren();
  if (state.report.time) {
    statuses.append(node("p", "Last refreshed " + new Date(state.report.time * 1000).toLocaleString()));
    const sourceDetails = node("details");
    sourceDetails.append(node("summary", "Source coverage"));
    for (const source of state.report.sources) {
      sourceDetails.append(node("p", source.source + (source.ok
        ? " · Checked " + source.retrieved + " of " + source.available + " search results"
        : " · Unavailable")));
      if (source.note) sourceDetails.append(node("p", source.note));
    }
    statuses.append(sourceDetails);
  }
  $("rail-topics").replaceChildren(...(state.settings.topics.length ? state.settings.topics.map(topic => node("div", topic, "rail-topic"))
    : [node("p", "No topics followed yet.")]));
}
document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
  filter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach(option => option.setAttribute("aria-pressed", String(option === button)));
  renderFeed();
}));

function renderProfile() {
  const root = $("orcid-record");
  root.replaceChildren();
  if (!state.profile) return;
  const profile = state.profile;
  $("orcid-id").value = profile.id;
  const block = node("div", "", "profile-result");
  const label = profile.name || profile.id;
  const heading = node("p");
  heading.append(link(label, "https://orcid.org/" + profile.id), document.createTextNode(" · " + profile.total + " public works"));
  block.append(heading);
  if (profile.suggestions.length) {
    block.append(node("p", "Suggested topics. Click a topic to add it before saving.", "hint"));
    for (const suggestion of profile.suggestions) {
      const add = node("button", "+ " + suggestion, "chip");
      add.addEventListener("click", () => {
        const topics = $("topics").value.split(",").map(t => t.trim()).filter(Boolean);
        if (topics.some(topic => topic.toLowerCase() === suggestion.toLowerCase())) {
          updateMessage("This topic is already in your list.");
        } else if (topics.length >= 8) {
          updateMessage("Remove a topic before adding another.");
        } else {
          $("topics").value = [...topics, suggestion].join(", ");
          updateMessage("Topic added. Save topics to follow it.");
        }
      });
      block.append(add);
    }
  }
  const works = node("details");
  works.append(node("summary", "Imported work"));
  const list = node("ol");
  for (const work of profile.works) {
    const entry = node("li");
    if (work.doi && /^10\.\d{4,9}\/\S+$/.test(work.doi)) entry.append(link(work.title, "https://doi.org/" + work.doi));
    else entry.textContent = work.title;
    if (work.year) entry.append(document.createTextNode(" (" + work.year + ")"));
    list.append(entry);
  }
  works.append(list);
  if (!profile.works.length) works.append(node("p", "This profile has no public works. Add topics directly below.", "hint"));
  block.append(works);
  const disconnect = node("button", "Remove ORCID profile", "secondary");
  disconnect.addEventListener("click", () => run(disconnect, "Removing profile…", async () => {
    state = await api("/api/disconnect", {});
    $("orcid-id").value = "";
    renderProfile();
    renderFeed();
    return "ORCID profile removed.";
  }));
  block.append(disconnect);
  root.append(block);
}

function fillForm() {
  $("topics").value = state.settings.topics.join(", ");
  $("days").value = state.settings.days;
  $("auto-refresh").checked = state.settings.auto_refresh;
  $("email").value = state.settings.email;
  $("frequency").value = state.settings.frequency;
  renderProfile();
  renderMail();
}

function renderMail() {
  const status = state.mail.configured ? "SMTP configured." : "SMTP needs to be configured to send email. You can still preview a digest.";
  $("mail-status").textContent = state.mail.status ? status + " " + state.mail.status : status;
}

function savedTopics() {
  return {topics: state.settings.topics.join(", "), days: state.settings.days, auto_refresh: state.settings.auto_refresh};
}

function savedMail() {
  return {email: state.settings.email, frequency: state.settings.frequency};
}

$("orcid-form").addEventListener("submit", event => {
  event.preventDefault();
  run(event.submitter, "Importing public works…", async () => {
    state = await api("/api/orcid", {id: $("orcid-id").value});
    renderProfile();
    renderFeed();
    return "ORCID profile imported.";
  });
});

$("topic-form").addEventListener("submit", event => {
  event.preventDefault();
  run(event.submitter, "Saving topics…", async () => {
    const topics = {topics: $("topics").value, days: $("days").value, auto_refresh: $("auto-refresh").checked};
    state = await api("/api/settings", {...savedMail(), ...topics});
    $("topics").value = state.settings.topics.join(", ");
    renderFeed();
    show("feed");
    return "Topics saved.";
  });
});
$("digest-form").addEventListener("submit", event => {
  event.preventDefault();
  run(event.submitter, "Saving email preferences…", async () => {
    state = await api("/api/settings", {...savedTopics(), email: $("email").value, frequency: $("frequency").value});
    renderMail();
    return "Email preferences saved.";
  });
});
$("preview").addEventListener("click", () => run($("preview"), "Preparing preview…", async () => {
  const preview = await api("/api/digest");
  $("digest-preview").textContent = preview.text;
  $("digest-preview").hidden = false;
  return "";
}));
$("refresh").addEventListener("click", () => run($("refresh"), "Looking for papers and datasets…", async () => {
  state = await api("/api/refresh", {});
  renderFeed();
  return "Feed refreshed.";
}));

run($("refresh"), "Loading Conference…", async () => {
  state = await api("/api/state");
  fillForm();
  renderFeed();
  return "";
});
setInterval(async () => {
  if (!busy && current === "feed" && state && !$("matches").querySelector("details[open]")) {
    try {
      const next = await api("/api/state");
      if (!busy && current === "feed" && next.report.time !== state.report.time) {
        state = next;
        renderFeed();
      }
    } catch (_) { /* The next poll can recover after a disconnect. */ }
  }
}, 45000);
