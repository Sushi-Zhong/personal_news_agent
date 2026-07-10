let mobileCategory = "";
let mobileViewMode = "chat";
let mobileSnapTimer = null;
let mobileTypingScrollY = 0;
const mobileState = {
  topic: "张雪机车",
  categoryScope: [],
};
const mobileBootstrapTopics = [
  { title: "张雪机车", category_scope: ["sports"], topic_type: "user" },
  { title: "SpaceX IPO 传闻", category_scope: ["tech", "economy"], topic_type: "system" },
  { title: "2026 世界杯开幕", category_scope: ["sports"], topic_type: "system" },
  { title: "AI 终端设备", category_scope: ["tech"], topic_type: "user" },
];
syncMobileChatContext();
syncMobileSessionState();
syncMobileBriefToggle();
syncMobileViewModeFromScroll();

document.querySelectorAll("[data-auth-mode-target]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.authModeTarget;
    document.querySelectorAll("[data-auth-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.authPanel !== target;
    });
  });
});

document.querySelector("[data-mobile-brief-toggle]")?.addEventListener("click", () => {
  const card = document.querySelector(".mobile-today-card");
  setMobileBriefExpanded(card?.hidden);
});

window.addEventListener("scroll", handleMobilePageScroll, { passive: true });
document.querySelector("#message")?.addEventListener("focus", handleMobileInputFocus);
document.querySelector("#message")?.addEventListener("blur", handleMobileInputBlur);
window.visualViewport?.addEventListener("resize", updateMobileKeyboardOffset);
window.visualViewport?.addEventListener("scroll", updateMobileKeyboardOffset);

async function refreshMobile() {
  await Promise.all([loadFeed(mobileCategory, 8), loadEvents("#events", mobileCategory, 5), loadTaskNotifications(), loadMobileTopics()]);
  updateMobileBrief();
}

document.querySelectorAll("#mobileTabs button").forEach((button) => {
  button.addEventListener("click", async () => {
    document.querySelectorAll("#mobileTabs button").forEach((node) => node.classList.remove("active"));
    button.classList.add("active");
    mobileCategory = button.dataset.category || "";
    mobileState.categoryScope = mobileCategory ? [mobileCategory] : [];
    syncMobileChatContext();
    await refreshMobile();
    setMobileViewMode("content");
  });
});

document.querySelector("#registerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const status = document.querySelector("#registerStatus");
  if (button) button.disabled = true;
  if (status) status.textContent = "正在创建账号并进行实名手机号核验。";
  try {
    const result = await registerFromForm(form);
    document.querySelector("#registerStatus").textContent = `已创建：${result.user.display_name}`;
    syncMobileSessionState();
    showOnboardingForm();
    document.querySelector(".auth-card details").open = true;
    await loadProfileIntoForm("#onboardingForm");
    await refreshMobile();
  } catch (error) {
    document.querySelector("#registerStatus").textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
});

document.querySelector("#onboardingForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const status = document.querySelector("#registerStatus");
  if (button) button.disabled = true;
  if (status) status.textContent = "正在保存初始化配置。";
  try {
    const result = await completeOnboardingFromForm(form);
    document.querySelector("#registerStatus").textContent = `初始化完成：${result.model.name}`;
    await refreshMobile();
  } catch (error) {
    document.querySelector("#registerStatus").textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
});

document.querySelector("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const status = document.querySelector("#registerStatus");
  if (button) button.disabled = true;
  if (status) status.textContent = "正在登录。";
  try {
    const result = await loginFromForm(form);
    document.querySelector("#registerStatus").textContent = `已登录：${result.user.display_name}`;
    syncMobileSessionState();
    await loadProfileIntoForm("#onboardingForm");
    await refreshMobile();
  } catch (error) {
    document.querySelector("#registerStatus").textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
});

document.querySelector("#chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#message");
  const message = input.value.trim();
  if (!message) return;
  await handleMobileAssistantInput(message);
  await loadMobileTopics();
  input.value = "";
});

document.querySelector("#editProfileMobile").addEventListener("click", async () => {
  document.querySelector(".auth-card details").open = true;
  showOnboardingForm();
  await loadProfileIntoForm("#onboardingForm");
});

document.querySelector("#enableBrowserPushMobile")?.addEventListener("click", async () => {
  await enableBrowserNotifications();
  await loadTaskNotifications();
  updateMobileBrief();
});

document.querySelectorAll("[data-mobile-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    const action = button.dataset.mobileAction;
    if (action === "brief") {
      await sendChat("基于我的兴趣和当前推荐，给我一版今日简报。");
    } else if (action === "deep") {
      await sendChat(`围绕${mobileState.topic}做一次深度挖掘，按最新进展、关键主体和不确定性总结。`);
    } else if (action === "track") {
      await createMobileTrackingTask();
    }
    updateMobileBrief();
  });
});

bindAskButtons();
bindNotificationReads();
window.handleAssistantInput = handleMobileAssistantInput;
loadOnboardingOptions("#onboardingForm").then(() => loadProfileIntoForm("#onboardingForm"));
startTaskPushPolling();
refreshMobile();

function showOnboardingForm() {
  const form = document.querySelector("#onboardingForm");
  if (form) form.hidden = false;
}

function setMobileBriefExpanded(expanded) {
  const card = document.querySelector(".mobile-today-card");
  const toggle = document.querySelector("[data-mobile-brief-toggle]");
  if (card) card.hidden = !expanded;
  if (toggle) {
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.classList.toggle("active", expanded);
    toggle.textContent = expanded ? "收起" : "今日";
  }
}

function syncMobileBriefToggle() {
  setMobileBriefExpanded(false);
}

function revealMobileFeed() {
  const target = document.querySelector(".mobile-content-group");
  if (!target) return;
  alignMobileSnapTarget(target);
}

function setMobileViewMode(mode, options = {}) {
  mobileViewMode = mode === "content" ? "content" : "chat";
  const target = mobileViewMode === "content"
    ? document.querySelector(".mobile-content-group")
    : document.querySelector(".mobile-agent-card");
  document.body.classList.toggle("mobile-chat-view", mobileViewMode === "chat");
  if (!target) return;
  alignMobileSnapTarget(target, options.instant ? "auto" : "smooth");
}

function syncMobileViewModeFromScroll() {
  if (document.body.classList.contains("mobile-typing")) {
    mobileViewMode = "chat";
    document.body.classList.add("mobile-chat-view");
    return;
  }
  const agent = document.querySelector(".mobile-agent-card");
  if (!agent) return;
  const rect = agent.getBoundingClientRect();
  mobileViewMode = rect.bottom > window.innerHeight * 0.35 ? "chat" : "content";
  document.body.classList.toggle("mobile-chat-view", mobileViewMode === "chat");
}

function handleMobilePageScroll() {
  syncMobileViewModeFromScroll();
  if (document.body.classList.contains("mobile-typing")) return;
  window.clearTimeout(mobileSnapTimer);
  mobileSnapTimer = window.setTimeout(snapMobileToNearestCard, 120);
}

function snapMobileToNearestCard() {
  if (document.body.classList.contains("mobile-typing")) return;
  const targets = [document.querySelector(".mobile-agent-card"), document.querySelector(".mobile-push-card")]
    .filter((node) => shouldSnapMobileCardTop(node));
  if (!targets.length) return;
  const nearest = targets.reduce((best, node) => {
    const distance = Math.abs(node.getBoundingClientRect().top - getMobileSnapTop());
    return !best || distance < best.distance ? { node, distance } : best;
  }, null);
  if (nearest) alignMobileSnapTarget(nearest.node);
}

function shouldSnapMobileCardTop(node) {
  if (!node) return false;
  const topDistance = node.getBoundingClientRect().top - getMobileSnapTop();
  if (node.classList.contains("mobile-agent-card")) {
    return Math.abs(topDistance) <= 500;
  }
  return Math.abs(topDistance) <= 200;
}

function getMobileSnapTop() {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--mobile-header-height")) || 0;
}

function alignMobileSnapTarget(target, behavior = "smooth") {
  if (!target) return;
  const targetTop = window.scrollY + target.getBoundingClientRect().top - getMobileSnapTop();
  window.scrollTo({ top: Math.max(0, targetTop), behavior });
}

function handleMobileInputFocus() {
  mobileTypingScrollY = window.scrollY;
  mobileViewMode = "chat";
  document.body.classList.add("mobile-typing");
  document.body.classList.add("mobile-chat-view");
  updateMobileKeyboardOffset();
  window.setTimeout(() => window.scrollTo({ top: mobileTypingScrollY, behavior: "auto" }), 60);
}

function handleMobileInputBlur() {
  document.body.classList.remove("mobile-typing");
  document.documentElement.style.setProperty("--mobile-keyboard-offset", "0px");
}

function updateMobileKeyboardOffset() {
  const viewport = window.visualViewport;
  const keyboardOffset = viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0;
  document.documentElement.style.setProperty("--mobile-keyboard-offset", `${Math.round(keyboardOffset)}px`);
}

async function handleMobileAssistantInput(message) {
  const command = parseAssistantCommand(message);
  if (!command) return sendChat(message);

  appendLocalTurn("user", message);
  const assistantNode = appendLocalTurn("assistant", "", "#messages", true);
  try {
    if (["search", "s", "news"].includes(command.name)) {
      applyMobileTopicCommand(command);
      return sendChatIntoTurn(`${mobileState.topic} 最新新闻，按来源搜索、正文抓取、证据合并和事件线处理。`, assistantNode);
    }
    if (["deep", "dive", "deep-dive"].includes(command.name)) {
      applyMobileTopicCommand(command);
      return sendChatIntoTurn(`围绕${mobileState.topic}做一次深度挖掘，按最新进展、关键主体和不确定性总结。`, assistantNode);
    }
    if (["topic", "t"].includes(command.name)) {
      applyMobileTopicCommand(command);
      setAssistantTurnText(assistantNode, `已切换：${mobileState.topic}`);
      return null;
    }
    if (["task", "track"].includes(command.name)) {
      applyMobileTopicCommand(command);
      const result = await createMobileTrackingTask({
        taskType: commandArg(command, "type"),
        schedule: commandArg(command, "every", "schedule", "cron"),
        delivery: commandArg(command, "push", "channel", "delivery"),
        silent: true,
      });
      setAssistantTurnText(assistantNode, `已保存跟踪：${mobileState.topic}`);
      return result;
    }
    if (["feed"].includes(command.name)) {
      mobileCategory = commandArg(command, "cat", "category") || commandText(command) || "";
      mobileState.categoryScope = mobileCategory ? [mobileCategory] : [];
      syncMobileChatContext();
      syncMobileTabs();
      await refreshMobile();
      setAssistantTurnText(assistantNode, `已更新信息流${mobileCategory ? `：${mobileCategory}` : "。"}。`);
      return null;
    }
    setAssistantTurnText(assistantNode, "可执行：/search、/topic、/task、/deep、/feed。");
    return null;
  } catch (error) {
    setAssistantTurnText(assistantNode, error.message);
    return null;
  }
}

async function loadMobileTopics() {
  const target = document.querySelector("[data-mobile-topic-list]");
  if (!target) return;
  try {
    const remote = activeUserId && activeUserId !== "default"
      ? await request(`/api/topics?user_id=${encodeURIComponent(activeUserId)}&limit=10`)
      : { items: [] };
    const items = mergeMobileTopics([...(remote.items || []), ...mobileBootstrapTopics]).slice(0, 8);
    target.innerHTML = items
      .map((item) => `<button type="button" class="${item.topic_type === "system" ? "system-topic" : "user-topic"}" data-mobile-topic="${escapeHtml(item.title)}" data-category-scope="${escapeHtml((item.category_scope || []).join(","))}">${escapeHtml(shortMobileTopic(item.title))}</button>`)
      .join("");
    bindMobileTopicButtons();
  } catch (error) {
    target.innerHTML = mobileBootstrapTopics
      .map((item) => `<button type="button" data-mobile-topic="${escapeHtml(item.title)}" data-category-scope="${escapeHtml((item.category_scope || []).join(","))}">${escapeHtml(shortMobileTopic(item.title))}</button>`)
      .join("");
    bindMobileTopicButtons();
  }
}

function bindMobileTopicButtons() {
  document.querySelectorAll("[data-mobile-topic]").forEach((button) => {
    if (button.dataset.bound === "true") return;
    button.dataset.bound = "true";
    button.addEventListener("click", async () => {
      document.querySelectorAll("[data-mobile-topic]").forEach((item) => item.classList.toggle("active", item === button));
      mobileState.topic = button.dataset.mobileTopic || mobileState.topic;
      mobileState.categoryScope = parseScope(button.dataset.categoryScope || "");
      mobileCategory = mobileState.categoryScope[0] || mobileCategory;
      syncMobileTabs();
      syncMobileChatContext();
      await sendChat(`${mobileState.topic} 最近有什么值得关注的变化？`);
    });
  });
}

function mergeMobileTopics(items) {
  const seen = new Set();
  return items.filter((item) => {
    if (!item.title || seen.has(item.title)) return false;
    seen.add(item.title);
    return true;
  });
}

function shortMobileTopic(title) {
  return title.replace("传闻", "").replace("2026 ", "").replace("的影响", "").trim();
}

function applyMobileTopicCommand(command) {
  const topic = commandText(command) || commandArg(command, "topic", "q", "query");
  const scope = commandScope(command, mobileState.categoryScope);
  if (topic) mobileState.topic = topic;
  mobileState.categoryScope = scope;
  mobileCategory = scope[0] || mobileCategory;
  syncMobileTabs();
  syncMobileChatContext();
}

function syncMobileChatContext() {
  window.currentChatContext = {
    topic: mobileState.topic,
    category_scope: mobileState.categoryScope,
    use_llm: true,
  };
  updateMobileBrief();
}

function syncMobileTabs() {
  document.querySelectorAll("#mobileTabs button").forEach((button) => {
    button.classList.toggle("active", (button.dataset.category || "") === mobileCategory);
  });
}

function syncMobileSessionState() {
  const loggedIn = activeUserId && activeUserId !== "default";
  document.body.classList.toggle("mobile-logged-in", loggedIn);
  document.body.classList.toggle("mobile-logged-out", !loggedIn);
  const account = document.querySelector(".auth-card details");
  if (account) account.open = !loggedIn;
}

async function createMobileTrackingTask(options = {}) {
  const result = await request("/api/tasks", {
    method: "POST",
    body: JSON.stringify({
      user_id: activeUserId,
      task_type: options.taskType || "topic_tracking",
      schedule: options.schedule || "*/20 * * * *",
      category_scope: mobileState.categoryScope,
      topics: [mobileState.topic],
      output_style: "事件线+关系网",
      delivery_channel: options.delivery || "in_app",
    }),
  });
  await loadTaskNotifications();
  updateMobileBrief();
  if (!options.silent) {
    appendLocalTurn("assistant", `已保存跟踪：${mobileState.topic}`);
  }
  return result;
}

function updateMobileBrief() {
  const topic = document.querySelector("[data-mobile-brief-topic]");
  const feedCount = document.querySelector("[data-mobile-feed-count]");
  const eventCount = document.querySelector("[data-mobile-event-count]");
  const trackCount = document.querySelector("[data-mobile-track-count]");
  if (topic) topic.textContent = mobileState.topic || "今日资讯";
  if (feedCount) feedCount.textContent = String(document.querySelectorAll("#feed .item").length);
  if (eventCount) eventCount.textContent = String(document.querySelectorAll("#events .item").length);
  if (trackCount) trackCount.textContent = String(document.querySelectorAll("[data-notifications] article").length);
}
