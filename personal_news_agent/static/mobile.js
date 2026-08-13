let mobileCategory = "";
let mobileViewMode = "chat";
let mobileSnapTimer = null;
let mobileTypingScrollY = 0;
let activeMobilePopover = null;
let activeMobilePopoverCleanup = null;
const mobileState = {
  topic: "",
  categoryScope: [],
};
let mobileTopicLocked = Boolean(mobileState.topic);
const mobileBootstrapTopics = [
  { title: "科技公司上市观察", category_scope: ["tech", "economy"], topic_type: "system" },
  { title: "大型体育赛事运营", category_scope: ["sports"], topic_type: "system" },
];
syncMobileChatContext();
syncMobileSessionState();
syncMobileBriefToggle();
syncMobileViewModeFromScroll();
bindSlashCommandMenu();
window.applyChatConversationContext = applyMobileChatConversationContext;
window.refreshTopics = loadMobileTopics;
restoreChatMemory("#messages");

document.querySelectorAll("[data-auth-mode-target]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.authModeTarget;
    document.querySelectorAll("[data-auth-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.authPanel !== target;
    });
  });
});

bindRegistrationCodeForm("#registerForm", "#registerStatus");

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
  const button = form.querySelector('button[type="submit"]');
  const status = document.querySelector("#registerStatus");
  if (!ensureRegistrationChallenge(form, status)) return;
  if (button) button.disabled = true;
  if (status) status.textContent = "正在验证手机号并创建账号。";
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
  const button = form.querySelector('button[type="submit"]');
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
    const profileData = await loadProfileIntoForm("#onboardingForm");
    if (!profileData?.profile?.onboarding_completed) {
      showOnboardingForm();
      document.querySelector(".auth-card details").open = true;
      document.querySelector("#registerStatus").textContent = "登录成功，请先选择关注板块和兴趣。";
    }
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
  lockMobileTopicFromQuery(message);
  input.value = "";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await handleMobileAssistantInput(message);
  await loadMobileTopics();
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
      showMobileTrackingPopover(button);
    }
    updateMobileBrief();
  });
});

document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const trackButton = target.closest("[data-mobile-track-popover]");
  if (trackButton) {
    event.preventDefault();
    showMobileTrackingPopover(trackButton);
    return;
  }
  const topicButton = target.closest("[data-mobile-topic-popover]");
  if (topicButton) {
    event.preventDefault();
    showMobileTopicPopover(topicButton);
    return;
  }
  const taskButton = target.closest("[data-mobile-task-list]");
  if (taskButton) {
    event.preventDefault();
    showMobileTaskListPopover(taskButton);
    return;
  }
  const eventItem = target.closest("#events .item");
  if (eventItem) {
    event.preventDefault();
    showMobileEventActionPopover(eventItem);
  }
});

bindAskButtons();
bindNotificationReads();
window.handleAssistantInput = handleMobileAssistantInput;
window.handleChatResponseSideEffects = handleMobileChatResponseSideEffects;
initializeMobileProfileState();
startTaskPushPolling();
refreshMobile();

async function initializeMobileProfileState() {
  await loadOnboardingOptions("#onboardingForm");
  if (!activeUserId || activeUserId === "default") return;
  try {
    const data = await loadProfileIntoForm("#onboardingForm");
    if (!data?.profile?.onboarding_completed) {
      showOnboardingForm();
      document.querySelector(".auth-card details").open = true;
      const status = document.querySelector("#registerStatus");
      if (status) status.textContent = "请选择关注板块和兴趣，完成首次初始化。";
    }
  } catch (error) {
    const status = document.querySelector("#registerStatus");
    if (status) status.textContent = "个人配置暂时无法载入，可稍后重试。";
  }
}

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
    if (["schedule"].includes(command.name)) {
      return sendChatIntoTurn(message, assistantNode);
    }
    if (["report", "r"].includes(command.name)) {
      applyMobileTopicCommand(command);
      const reportTopic = cleanMobileSkillTopicTitle(commandText(command) || "");
      const category = commandArg(command, "category", "cat");
      const timeRange = commandArg(command, "time-range", "time", "range") || "30d";
      const reportCommand = [
        "/report",
        reportTopic,
        category ? `--category ${category}` : "",
        timeRange ? `--time-range ${timeRange}` : "",
      ].filter(Boolean).join(" ");
      return sendChatIntoTurn(reportCommand, assistantNode);
    }
    if (["brief"].includes(command.name)) {
      applyMobileTopicCommand(command);
      const briefTopic = commandText(command) || "";
      const briefCategory = commandArg(command, "category", "cat");
      const briefCommand = ["/brief", briefTopic, briefCategory ? `--category ${briefCategory}` : ""].filter(Boolean).join(" ");
      return sendChatIntoTurn(briefCommand, assistantNode);
    }
    if (["related"].includes(command.name)) {
      const relatedTopic = commandText(command) || commandArg(command, "topic", "q", "query");
      return runMobileRelatedSearchIntoTurn(assistantNode, relatedTopic);
    }
    if (["factcheck", "verify"].includes(command.name)) {
      applyMobileTopicCommand(command);
      const claim = commandText(command) || mobileState.topic || "";
      const category = commandArg(command, "category", "cat") || (mobileState.categoryScope || []).join(",");
      const factCommand = ["/factcheck", claim, category ? `--category ${category}` : ""].filter(Boolean).join(" ");
      return sendChatIntoTurn(factCommand, assistantNode);
    }
    if (["map", "graph"].includes(command.name)) {
      applyMobileTopicCommand(command);
      const mapTopic = commandText(command) || mobileState.topic || "";
      const category = commandArg(command, "category", "cat") || (mobileState.categoryScope || []).join(",");
      const mapCommand = ["/map", mapTopic, category ? `--category ${category}` : ""].filter(Boolean).join(" ");
      return sendChatIntoTurn(mapCommand, assistantNode);
    }
    if (["schedule"].includes(command.name)) {
      const taskDescription = commandText(command) || "";
      return sendChatIntoTurn(["/schedule", taskDescription].filter(Boolean).join(" "), assistantNode);
    }
    if (["sources"].includes(command.name)) {
      const sourceCategory = commandText(command) || commandArg(command, "category", "cat") || "";
      return sendChatIntoTurn(["/sources", sourceCategory].filter(Boolean).join(" "), assistantNode);
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
    setAssistantTurnText(assistantNode, "可执行：/factcheck、/map、/report、/brief、/related、/schedule、/sources。");
    return null;
  } catch (error) {
    setAssistantTurnText(assistantNode, error.message);
    return null;
  }
}

async function runMobileRelatedSearchIntoTurn(assistantNode, explicitTopic = "") {
  const relatedCommand = ["/related", explicitTopic].filter(Boolean).join(" ");
  return sendChatIntoTurn(relatedCommand, assistantNode);
}

async function loadMobileTopics() {
  const target = document.querySelector("[data-mobile-topic-list]");
  if (!target) return;
  try {
    const userId = activeUserId || "default";
    const topicConversationId = conversationId || "__new__";
    const remote = await request(
      `/api/topics?user_id=${encodeURIComponent(userId)}&conversation_id=${encodeURIComponent(topicConversationId)}&limit=10`,
    );
    const persisted = remote.items || [];
    renderMobileTopicRail(target, composeMobileTopicItems(persisted));
    try {
      const recommended = await request(
        `/api/topics/recommended?user_id=${encodeURIComponent(userId)}&limit=5&window_hours=24&refresh_window_hours=6`,
      );
      renderMobileTopicRail(target, composeMobileTopicItems(persisted, recommended.items || []));
    } catch (error) {
      // Keep persisted topics and static fallbacks visible.
    }
  } catch (error) {
    renderMobileTopicRail(target, mobileBootstrapTopics);
  }
}

function composeMobileTopicItems(persisted, recommended = []) {
  const userTopics = persisted.filter((item) => item.topic_type !== "system");
  const systemTopics = persisted.filter((item) => item.topic_type === "system");
  return [...userTopics, ...recommended, ...systemTopics, ...mobileBootstrapTopics];
}

function renderMobileTopicRail(target, items) {
  const merged = mergeMobileTopics(items).slice(0, 8);
  target.innerHTML = merged
    .map((item) => `<button type="button" class="${mobileTopicKind(item)}" data-mobile-topic="${escapeHtml(item.title)}" data-category-scope="${escapeHtml((item.category_scope || []).join(","))}">${escapeHtml(shortMobileTopic(item.title))}</button>`)
    .join("");
  bindMobileTopicButtons();
}

function bindMobileTopicButtons() {
  document.querySelectorAll("[data-mobile-topic]").forEach((button) => {
    if (button.dataset.bound === "true") return;
    button.dataset.bound = "true";
    button.addEventListener("click", async () => {
      document.querySelectorAll("[data-mobile-topic]").forEach((item) => item.classList.toggle("active", item === button));
      setMobileTopic(button.dataset.mobileTopic || mobileState.topic);
      mobileState.categoryScope = parseMobileScope(button.dataset.categoryScope || "");
      mobileCategory = mobileState.categoryScope[0] || mobileCategory;
      syncMobileTabs();
      syncMobileChatContext();
      await sendChat(`${mobileState.topic} 最近有什么值得关注的变化？`);
    });
  });
}

async function showMobileTopicPopover(anchor) {
  closeMobilePopover();
  const popover = document.createElement("div");
  popover.className = "event-action-popover mobile-topic-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", "关注");
  popover.innerHTML = `
    <strong>关注</strong>
    <button type="button" class="topic-card add" data-mobile-topic-new><span>新建关注</span><small>下一句作为主题</small></button>
    <div data-mobile-topic-popover-list><article><span>正在加载</span></article></div>
  `;
  document.body.appendChild(popover);
  positionMobilePopover(anchor, popover);
  bindMobilePopoverDismiss(popover, anchor);
  popover.querySelector("[data-mobile-topic-new]")?.addEventListener("click", startNewMobileTopicConversation);
  try {
    const userId = activeUserId || "default";
    const data = await request(`/api/topics?user_id=${encodeURIComponent(userId)}&limit=20`);
    renderMobileTopicPopoverList(
      popover.querySelector("[data-mobile-topic-popover-list]"),
      mergeMobileTopics([...(data.items || []), ...mobileBootstrapTopics]),
    );
    try {
      const recommended = await request(
        `/api/topics/recommended?user_id=${encodeURIComponent(userId)}&limit=6&window_hours=24&refresh_window_hours=6`,
      );
      renderMobileTopicPopoverList(
        popover.querySelector("[data-mobile-topic-popover-list]"),
        mergeMobileTopics([...(data.items || []), ...(recommended.items || []), ...mobileBootstrapTopics]),
      );
    } catch (error) {
      // The persisted list is already rendered.
    }
  } catch (error) {
    renderMobileTopicPopoverList(popover.querySelector("[data-mobile-topic-popover-list]"), mobileBootstrapTopics);
  }
}

function renderMobileTopicPopoverList(target, items) {
  if (!target) return;
  if (!items.length) {
    target.innerHTML = `<article><span>暂无关注</span></article>`;
    return;
  }
  target.innerHTML = items
    .map((item) => {
      const title = item.title || "";
      const scope = (item.category_scope || []).join(",");
      const conversation = item.conversation_id || "";
      const active = (conversation && conversation === conversationId) || title === mobileState.topic ? " active" : "";
      const kind = ` ${mobileTopicKind(item)}`;
      const evidenceLevel = item.evidence_level || "";
      const evidenceLabel = item.evidence_label || (evidenceLevel === "lead" ? "新线索" : "热点");
      const meta = item.topic_type === "recommended"
        ? (evidenceLevel === "lead"
          ? `${evidenceLabel} · 单源待确认`
          : `${evidenceLabel} ${Number(item.hot_score || 0).toFixed(2)} · ${item.source_count || 1} 源/${item.article_count || 1} 报`)
        : (scope ? scope.split(",").join(" / ") : (item.topic_type === "system" ? "system" : "all"));
      return `<button type="button" class="topic-card${kind}${active}" data-mobile-topic-select="${escapeAttr(title)}" data-conversation-id="${escapeAttr(conversation)}" data-category-scope="${escapeAttr(scope)}"><span>${escapeHtml(shortMobileTopic(title))}</span><small>${escapeHtml(meta)}</small></button>`;
    })
    .join("");
  target.querySelectorAll("[data-mobile-topic-select]").forEach((button) => {
    button.addEventListener("click", () => selectMobileTopicFromPopover(button));
  });
}

function mobileTopicKind(item) {
  if (item.topic_type === "system") return "system-topic";
  if (item.topic_type === "recommended") return "recommended-topic";
  return "user-topic";
}

async function selectMobileTopicFromPopover(button) {
  const selectedTopic = button.dataset.mobileTopicSelect || "";
  const selectedConversationId = button.dataset.conversationId || "";
  setMobileTopic(selectedTopic || mobileState.topic);
  mobileState.categoryScope = parseMobileScope(button.dataset.categoryScope || "");
  mobileCategory = mobileState.categoryScope[0] || "";
  if (selectedConversationId) {
    conversationId = selectedConversationId;
    localStorage.setItem("pna_conversation_id", conversationId);
  } else {
    conversationId = null;
    localStorage.removeItem("pna_conversation_id");
  }
  syncMobileTabs();
  syncMobileChatContext();
  const messages = document.querySelector("#messages");
  if (messages) {
    messages.innerHTML = "";
    messages.appendChild(chatTurn("assistant", `已切换到：${mobileState.topic}`));
  }
  if (selectedConversationId) {
    await restoreChatMemory("#messages");
  }
  closeMobilePopover();
  await loadMobileTopics();
}

async function startNewMobileTopicConversation() {
  conversationId = null;
  localStorage.removeItem("pna_conversation_id");
  mobileState.topic = "";
  mobileState.categoryScope = [];
  mobileTopicLocked = false;
  mobileCategory = "";
  syncMobileTabs();
  syncMobileChatContext();
  const messages = document.querySelector("#messages");
  if (messages) {
    messages.innerHTML = "";
    messages.appendChild(chatTurn("assistant", "请输入要关注的主题。你发出的第一句话会成为这组关注对话的主题。"));
  }
  closeMobilePopover();
  await loadMobileTopics();
}

function mergeMobileTopics(items) {
  return items.reduce((merged, incoming) => {
    if (!incoming.title) return merged;
    const index = merged.findIndex((existing) => sameRecommendedEvent(existing, incoming));
    if (index >= 0) merged[index] = incoming;
    else merged.push(incoming);
    return merged;
  }, []);
}

function sameRecommendedEvent(existing, incoming) {
  if (existing.id && existing.id === incoming.id) return true;
  if (existing.event_key && existing.event_key === incoming.event_key) return true;
  return articleIdsOverlap(existing.article_ids, incoming.article_ids);
}

function articleIdsOverlap(left = [], right = []) {
  const articleIds = new Set(left || []);
  return (right || []).some((articleId) => articleIds.has(articleId));
}

function parseMobileScope(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function shortMobileTopic(title) {
  return title.replace("传闻", "").replace("2026 ", "").replace("的影响", "").trim();
}

function applyMobileTopicCommand(command) {
  const topic = commandText(command) || commandArg(command, "topic", "q", "query");
  const scope = commandScope(command, mobileState.categoryScope);
  if (topic) setMobileTopic(topic);
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
    allow_web_search: isWebSearchEnabled(),
    model_key: getChatModelKey(),
  };
  updateMobileBrief();
}

function applyMobileChatConversationContext(context = {}) {
  if (context.topic) {
    setMobileTopic(cleanMobileSkillTopicTitle(context.topic));
  }
  if (Array.isArray(context.category_scope)) mobileState.categoryScope = context.category_scope;
  mobileCategory = mobileState.categoryScope[0] || "";
  syncMobileTabs();
  syncMobileChatContext();
}

function cleanMobileSkillTopicTitle(value) {
  let topic = String(value || "").replace(/\s+/g, " ").trim();
  const prefixes = ["专题报告：", "专题报告:", "事实核查：", "事实核查:", "继续核查：", "继续核查:"];
  let changed = true;
  while (changed) {
    changed = false;
    for (const prefix of prefixes) {
      if (topic.startsWith(prefix)) {
        topic = topic.slice(prefix.length).trim();
        changed = true;
      }
    }
  }
  return topic;
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
  const topic = options.topic || mobileState.topic;
  const categoryScope = options.categoryScope || mobileState.categoryScope;
  const result = await request("/api/tasks", {
    method: "POST",
    body: JSON.stringify({
      user_id: activeUserId,
      task_type: options.taskType || "topic_tracking",
      schedule: options.schedule || "*/20 * * * *",
      category_scope: categoryScope,
      topics: [topic],
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

function showMobileTrackingPopover(anchor) {
  closeMobilePopover();
  const popover = document.createElement("div");
  popover.className = "event-action-popover mobile-task-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", "定时跟踪");
  popover.innerHTML = `
    <strong>定时跟踪</strong>
    <form class="mobile-task-form" data-mobile-track-form>
      <input name="topic" value="${escapeAttr(mobileState.topic || "")}" placeholder="专题" />
      <select name="category">
        ${mobileCategoryOptions((mobileState.categoryScope || [])[0])}
      </select>
      <select name="task_type">
        <option value="topic_tracking">专题跟踪</option>
        <option value="daily_digest">每日摘要</option>
        <option value="weekly_digest">每周摘要</option>
      </select>
      <input name="schedule" value="*/20 * * * *" placeholder="*/20 * * * *" />
      <select name="delivery_channel">
        <option value="in_app">应用内</option>
        <option value="browser">浏览器提醒</option>
      </select>
      <button type="submit">保存任务</button>
    </form>
    <p data-mobile-task-popover-status></p>
  `;
  document.body.appendChild(popover);
  positionMobilePopover(anchor, popover);
  popover.querySelector("[data-mobile-track-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const status = popover.querySelector("[data-mobile-task-popover-status]");
    const category = form.elements.category.value;
    if (status) status.textContent = "正在保存。";
    try {
      const result = await createMobileTrackingTask({
        topic: form.elements.topic.value.trim() || mobileState.topic,
        categoryScope: category ? [category] : [],
        taskType: form.elements.task_type.value,
        schedule: form.elements.schedule.value,
        delivery: form.elements.delivery_channel.value,
        silent: true,
      });
      if (status) status.textContent = `已保存：${nextMobileRunLabel(result.next_run_at)}`;
      await refreshMobileTaskListCount();
    } catch (error) {
      if (status) status.textContent = error.message;
    }
  });
  bindMobilePopoverDismiss(popover, anchor);
}

async function showMobileTaskListPopover(anchor) {
  closeMobilePopover();
  const popover = document.createElement("div");
  popover.className = "event-action-popover mobile-task-popover mobile-task-list-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", "任务");
  popover.innerHTML = `<strong>任务</strong><div class="task-list title-only-list" data-mobile-task-list-body><article><span>正在加载</span></article></div>`;
  document.body.appendChild(popover);
  positionMobilePopover(anchor, popover);
  bindMobilePopoverDismiss(popover, anchor);
  try {
    const data = await request(`/api/tasks?user_id=${encodeURIComponent(activeUserId || "default")}&limit=20`);
    renderMobileTaskList(popover.querySelector("[data-mobile-task-list-body]"), data.items || []);
  } catch (error) {
    popover.querySelector("[data-mobile-task-list-body]").innerHTML = `<article><span>${escapeHtml(error.message)}</span></article>`;
  }
}

function renderMobileTaskList(target, items) {
  if (!target) return;
  if (!items.length) {
    target.innerHTML = `<article><span>暂无任务</span></article>`;
    return;
  }
  target.innerHTML = items
    .map((item) => {
      const title = (item.topics || []).join("、") || mobileTaskTypeLabel(item.task_type);
      const enabled = item.enabled !== false;
      const meta = enabled ? `${mobileTaskTypeLabel(item.task_type)} · ${item.schedule_cron} · ${nextMobileRunLabel(item.next_run_at)}` : "已禁用";
      return `<article class="${enabled ? "" : "disabled"}" role="button" tabindex="0" data-mobile-task-id="${escapeAttr(item.id)}" data-task-enabled="${enabled ? "true" : "false"}" data-task-title="${escapeAttr(title)}" data-task-meta="${escapeAttr(meta)}">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(meta)}</span>
      </article>`;
    })
    .join("");
  target.querySelectorAll("[data-mobile-task-id]").forEach((item) => {
    item.addEventListener("click", () => showMobileTaskActionPopover(item));
    item.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      showMobileTaskActionPopover(item);
    });
  });
}

function showMobileTaskActionPopover(item) {
  const rect = item.getBoundingClientRect();
  const taskId = item.dataset.mobileTaskId || "";
  const enabled = item.dataset.taskEnabled !== "false";
  const title = item.dataset.taskTitle || "定时跟踪任务";
  const meta = item.dataset.taskMeta || "选择接下来要做的动作。";
  closeMobilePopover();
  const popover = document.createElement("div");
  popover.className = "event-action-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", `定时跟踪操作：${title}`);
  popover.innerHTML = `
    <strong>${escapeHtml(title)}</strong>
    <p>${escapeHtml(meta)}</p>
    <div>
      <button type="button" data-mobile-task-toggle>${enabled ? "禁用" : "启用"}</button>
      <button type="button" data-mobile-task-delete>删除</button>
      <button type="button" data-mobile-task-cancel>关闭</button>
    </div>
  `;
  document.body.appendChild(popover);
  popover.style.left = `${Math.min(window.innerWidth - popover.offsetWidth - 12, Math.max(12, rect.right + 8))}px`;
  popover.style.top = `${Math.min(window.innerHeight - popover.offsetHeight - 12, Math.max(12, rect.top))}px`;
  popover.querySelector("[data-mobile-task-toggle]")?.addEventListener("click", async () => {
    await setMobileTrackingTaskEnabled(taskId, !enabled);
    closeMobilePopover();
  });
  popover.querySelector("[data-mobile-task-delete]")?.addEventListener("click", async () => {
    await deleteMobileTrackingTask(taskId);
    closeMobilePopover();
  });
  popover.querySelector("[data-mobile-task-cancel]")?.addEventListener("click", closeMobilePopover);
  bindMobilePopoverDismiss(popover, null);
}

function showMobileEventActionPopover(item) {
  closeMobilePopover();
  const title = item.querySelector(".title")?.textContent?.trim() || "";
  if (!title) return;
  const sourceUrl = item.dataset.eventUrl || "";
  const sourceTitle = item.dataset.eventSourceTitle || title;
  const ask = `围绕热点事件“${title}”展开，告诉我发生了什么、为什么重要、后续看什么。`;
  const popover = document.createElement("div");
  popover.className = "event-action-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", `热点事件操作：${title}`);
  popover.innerHTML = `
    <strong>${escapeHtml(title)}</strong>
    <p>${escapeHtml(sourceTitle || "选择接下来要做的动作。")}</p>
    <div>
      <button type="button" data-mobile-event-send>发送到对话框</button>
      <button type="button" data-mobile-event-open ${sourceUrl ? "" : "disabled"}>打开原网址</button>
    </div>
  `;
  document.body.appendChild(popover);
  positionMobilePopover(item, popover);
  popover.querySelector("[data-mobile-event-send]")?.addEventListener("click", async () => {
    closeMobilePopover();
    await sendChat(ask);
    updateMobileBrief();
  });
  popover.querySelector("[data-mobile-event-open]")?.addEventListener("click", () => {
    if (!sourceUrl) return;
    closeMobilePopover();
    window.open(sourceUrl, "_blank", "noopener,noreferrer");
  });
  bindMobilePopoverDismiss(popover, item);
}

async function setMobileTrackingTaskEnabled(taskId, enabled) {
  if (!taskId) return;
  await request(`/api/tasks/${encodeURIComponent(taskId)}/enabled`, {
    method: "POST",
    body: JSON.stringify({ user_id: activeUserId || "default", enabled }),
  });
  await refreshMobileTaskListCount();
}

async function deleteMobileTrackingTask(taskId) {
  if (!taskId) return;
  await request(`/api/tasks/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(activeUserId || "default")}`, {
    method: "DELETE",
  });
  await refreshMobileTaskListCount();
}

async function refreshMobileTaskListCount() {
  await loadTaskNotifications();
  updateMobileBrief();
}

function closeMobilePopover() {
  if (activeMobilePopoverCleanup) activeMobilePopoverCleanup();
  activeMobilePopoverCleanup = null;
  if (activeMobilePopover) activeMobilePopover.remove();
  activeMobilePopover = null;
}

function bindMobilePopoverDismiss(popover, anchor) {
  const onPointerDown = (event) => {
    if (popover.contains(event.target) || anchor?.contains(event.target)) return;
    closeMobilePopover();
  };
  const onKeyDown = (event) => {
    if (event.key === "Escape") closeMobilePopover();
  };
  setTimeout(() => {
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
  }, 0);
  activeMobilePopover = popover;
  activeMobilePopoverCleanup = () => {
    document.removeEventListener("pointerdown", onPointerDown);
    document.removeEventListener("keydown", onKeyDown);
  };
}

function positionMobilePopover(anchor, popover) {
  const rect = anchor.getBoundingClientRect();
  const left = Math.min(window.innerWidth - popover.offsetWidth - 12, Math.max(12, rect.right - popover.offsetWidth));
  const top = Math.min(window.innerHeight - popover.offsetHeight - 12, Math.max(12, rect.bottom + 8));
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

function mobileCategoryOptions(selected = "") {
  const items = [
    ["sports", "体育"],
    ["politics", "时政"],
    ["economy", "经济"],
    ["tech", "科技"],
    ["auto", "汽车"],
    ["game", "游戏"],
    ["anime", "动漫"],
    ["entertainment", "娱乐"],
    ["", "全部"],
  ];
  return items.map(([value, label]) => `<option value="${escapeAttr(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function mobileTaskTypeLabel(value) {
  const labels = {
    topic_tracking: "专题跟踪",
    daily_digest: "每日摘要",
    weekly_digest: "每周摘要",
  };
  return labels[value] || value || "任务";
}

function nextMobileRunLabel(value) {
  if (!value) return "未定时";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未定时";
  return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function updateMobileBrief() {
  const topic = document.querySelector("[data-mobile-brief-topic]");
  const agentTopic = document.querySelector("[data-mobile-agent-topic]");
  const feedCount = document.querySelector("[data-mobile-feed-count]");
  const eventCount = document.querySelector("[data-mobile-event-count]");
  const trackCount = document.querySelector("[data-mobile-track-count]");
  const topicLabel = mobileState.topic || "新对话";
  if (topic) topic.textContent = topicLabel;
  if (agentTopic) agentTopic.textContent = topicLabel;
  if (feedCount) feedCount.textContent = String(document.querySelectorAll("#feed .item").length);
  if (eventCount) eventCount.textContent = String(document.querySelectorAll("#events .item").length);
  if (trackCount) trackCount.textContent = String(document.querySelectorAll("[data-notifications] article").length);
}

function handleMobileChatResponseSideEffects(response) {
  renderMobileResponseScopedFeed(mobileResponseScopedArticles(response));
  return response;
}

function mobileResponseScopedArticles(response) {
  const candidates = response?.evidence?.length
    ? response.evidence
    : response?.recommendations?.length
      ? response.recommendations
      : response?.skill_result?.data?.sources || [];
  return (candidates || []).filter((item) => item && item.title);
}

function renderMobileResponseScopedFeed(articles) {
  const target = document.querySelector("#feed");
  if (!target) return;
  if (!articles.length) {
    target.innerHTML = `<div class="empty-state compact-empty">本轮暂无相关资讯</div>`;
  } else {
    target.innerHTML = articles.slice(0, 8).map((item) => itemHtml(item)).join("");
  }
  updateMobileBrief();
}

function lockMobileTopicFromQuery(message) {
  const title = mobileQueryTopicTitle(message);
  if (!title) return;
  setMobileTopic(title);
}

function setMobileTopic(value) {
  const title = cleanMobileSkillTopicTitle(value).trim();
  if (!title || ["新对话", "当前关注", "今日资讯"].includes(title)) return;
  if (mobileTopicLocked && title !== mobileState.topic) return;
  mobileState.topic = title;
  mobileTopicLocked = true;
  updateMobileBrief();
}

function mobileQueryTopicTitle(message) {
  const command = parseAssistantCommand(message);
  const raw = command
    ? commandText(command) || commandArg(command, "topic", "q", "query") || ""
    : message;
  return cleanMobileSkillTopicTitle(raw).trim();
}
