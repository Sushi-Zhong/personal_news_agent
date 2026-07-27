if (activeUserId === "default") {
  window.location.replace(appUrl("/auth"));
} else {
  request(`/api/profile?user_id=${encodeURIComponent(activeUserId)}`)
    .then((data) => {
      if (!data.user) {
        localStorage.removeItem("pna_user_id");
        localStorage.removeItem("pna_user_name");
        localStorage.removeItem("pna_session_token");
        window.location.replace(appUrl("/auth"));
      }
    })
    .catch(() => {});
}

const topicStorageKey = () => `pna_current_topic:${activeUserId || "default"}`;
const TOPIC_CONTEXT_VERSION = 1;
const savedTopicContext = readSavedTopicContext();
if (!savedTopicContext.topic) {
  conversationId = null;
  localStorage.removeItem("pna_conversation_id");
}

const consoleState = {
  topic: savedTopicContext.topic,
  categoryScope: savedTopicContext.categoryScope,
  view: "event-line",
  topicPayload: null,
};
let pendingTopicFromNextMessage = false;
let topicLocked = Boolean(consoleState.topic);
let activeEventActionPopover = null;
let activeEventActionCleanup = null;
const bootstrapTopics = [
  { title: "NBA 总决赛", category_scope: ["sports"], topic_type: "user" },
  { title: "俄乌战争对农作物的影响", category_scope: ["politics", "economy"], topic_type: "user" },
  { title: "AI 终端设备", category_scope: ["tech"], topic_type: "user" },
];
syncChatContext();
syncContextDock();

document.querySelector("#refresh")?.addEventListener("click", () => refreshWeb());
document.querySelector("#feedCategory")?.addEventListener("change", () => loadFeedAndEvents());
document.querySelector("#newTopicConversation")?.addEventListener("click", startNewTopicConversation);

document.querySelector("#openConfig")?.addEventListener("click", async () => {
  document.querySelector("#configDialog").showModal();
  await loadProfileIntoForm("#onboardingForm");
});

document.querySelector("#onboardingForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await completeOnboardingFromForm(event.currentTarget);
    document.querySelector("#onboardingStatus").textContent = `已保存：${result.model.name}`;
    document.querySelector("#assistantPromptPreview").textContent = result.assistant_prompt;
    await refreshWeb();
  } catch (error) {
    document.querySelector("#onboardingStatus").textContent = error.message;
  }
});

document.querySelector("#topicForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  consoleState.topic = document.querySelector("#topicInput").value.trim() || consoleState.topic;
  topicLocked = Boolean(consoleState.topic);
  consoleState.categoryScope = parseScope(document.querySelector("#categoryScope").value);
  saveTopicContext();
  syncChatContext();
  syncContextDock();
  syncTaskTopic();
  await loadTopicView();
  await loadDueUrls();
});

document.querySelector("#nativeIngest")?.addEventListener("click", async () => {
  await runNativeIngest();
});

document.querySelector("#deepDive")?.addEventListener("click", async () => {
  await runDeepDive();
});

document.querySelector("#createTopicTask")?.addEventListener("click", async () => {
  await createTrackingTask(document.querySelector("#taskForm"));
});

document.querySelector("#generateReport")?.addEventListener("click", async () => {
  await generateTopicReport();
});

document.querySelector("#enableBrowserPush")?.addEventListener("click", async () => {
  await enableBrowserNotifications();
  await loadTaskNotifications();
});

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    const action = button.dataset.action;
    button.disabled = true;
    setStatus(`正在执行：${button.textContent.trim()}。`);
    try {
      if (action === "changes") {
        await sendChat(`${consoleState.topic}今天有哪些值得关注的新变化？`);
      } else if (action === "deep-dive-chat") {
        await runDeepDive();
        await sendChat(`围绕${consoleState.topic}做一次深度挖掘，按最新进展、关键主体和不确定性总结。`);
      } else if (action === "make-topic") {
        await loadTopicView();
        await sendChat(`把${consoleState.topic}整理成专题，给我事件线和关系网观察重点。`);
      } else if (action === "make-task") {
        await createTrackingTask(document.querySelector("#taskForm"));
        await sendChat(`已把${consoleState.topic}设为跟踪主题，告诉我后续应该重点盯哪些变化。`);
      } else if (action === "make-report") {
        await generateTopicReport();
      }
      setStatus(`已执行：${button.textContent.trim()}。`);
    } catch (error) {
      setStatus(error.message);
    } finally {
      button.disabled = false;
    }
  });
});

bindTopicCards();

document.querySelectorAll("[data-topic-view]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-topic-view]").forEach((item) => item.classList.toggle("active", item === button));
    consoleState.view = button.dataset.topicView;
    syncContextDock();
    renderTopicVisual(consoleState.topicPayload, consoleState.view);
  });
});

document.querySelector("#chatForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#message");
  const message = input.value.trim();
  if (!message) return;
  await handleAssistantInput(message);
  await loadTopics();
  input.value = "";
});

document.querySelector("#taskForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await createTrackingTask(event.currentTarget);
});

bindAskButtons();
bindSlashCommandMenu();
bindNotificationReads();
bindTaskActions();
bindRailTooltips();
window.handleAssistantInput = handleAssistantInput;
window.applyChatConversationContext = applyChatConversationContext;
window.handleChatResponseSideEffects = handleWebChatResponseSideEffects;
window.refreshTopics = loadTopics;
restoreChatMemory("#messages");
window.handleAssistantInput = handleAssistantInput;
loadOnboardingOptions("#onboardingForm").then(() => loadProfileIntoForm("#onboardingForm"));
startTaskPushPolling();
refreshWeb();

async function refreshWeb() {
  setStatus("正在刷新数据。");

  await Promise.all([
    loadSystemStatus(),
    loadSourceSummary(),
    loadFeedAndEvents(),
    loadDueUrls(),
    loadTasks(),
    loadTaskNotifications(),
    loadTopics(),
  ]);

  await loadTopicView();
  setStatus("已更新。");
}

async function handleAssistantInput(message) {
  const command = parseAssistantCommand(message);

  if (!command) {
    const response = await sendChat(message);
    await createTopicFromFirstMessage(message, response);
    return response;
  }


  appendLocalTurn("user", message);
  const assistantNode = appendLocalTurn("assistant", "", "#messages", true);
  try {
    if (["search", "s", "news"].includes(command.name)) {
      await applyTopicCommand(command);
      return sendChatIntoTurn(`${consoleState.topic} 最新新闻，按来源搜索、正文抓取、证据合并和事件线处理。`, assistantNode);
    }
    if (["deep", "dive", "deep-dive"].includes(command.name)) {
      await applyTopicCommand(command);
      await runDeepDive();
      return sendChatIntoTurn(`围绕${consoleState.topic}做一次深度挖掘，按最新进展、关键主体和不确定性总结。`, assistantNode);
    }
    if (["topic", "t"].includes(command.name)) {
      await applyTopicCommand(command);
      setAssistantTurnText(assistantNode, `已切换：${consoleState.topic}`);
      return null;
    }
    if (["task", "track"].includes(command.name)) {
      await applyTopicCommand(command, { reload: false });
      applyTaskCommand(command);
      const result = await createTrackingTask(document.querySelector("#taskForm"));
      setAssistantTurnText(assistantNode, result ? `已保存跟踪：${consoleState.topic}` : "保存失败。");
      return result;
    }
    if (["report", "r"].includes(command.name)) {
      await applyTopicCommand(command, { reload: false });
      const reportTopic = cleanSkillTopicTitle(commandText(command) || "");
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
      await applyTopicCommand(command, { reload: false });
      const briefTopic = commandText(command) || "";
      const briefCategory = commandArg(command, "category", "cat");
      const briefCommand = ["/brief", briefTopic, briefCategory ? `--category ${briefCategory}` : ""].filter(Boolean).join(" ");
      return sendChatIntoTurn(briefCommand, assistantNode);
    }
    if (["related"].includes(command.name)) {
      const relatedTopic = commandText(command) || commandArg(command, "topic", "q", "query");
      return runRelatedSearchIntoTurn(assistantNode, relatedTopic);
    }
    if (["factcheck", "verify"].includes(command.name)) {
      await applyTopicCommand(command, { reload: false });
      const claim = commandText(command) || consoleState.topic || "";
      const category = commandArg(command, "category", "cat") || (consoleState.categoryScope || []).join(",");
      const factCommand = ["/factcheck", claim, category ? `--category ${category}` : ""].filter(Boolean).join(" ");
      return sendChatIntoTurn(factCommand, assistantNode);
      await applyTopicCommand(command);
      const result = await generateTopicReport({ chatFollowup: false });
      setAssistantTurnText(assistantNode, result ? `报告已生成：${result.report_id}` : "报告生成失败。");
      return result;
    }
    if (["ingest", "source"].includes(command.name)) {
      await applyTopicCommand(command);
      const result = await runNativeIngest();
      setAssistantTurnText(assistantNode, result ? `源搜索入库完成：${consoleState.topic}` : "源搜索入库失败。");
      return result;
    }
    if (["feed"].includes(command.name)) {
      const category = commandArg(command, "cat", "category") || commandText(command);
      if (document.querySelector("#feedCategory")) document.querySelector("#feedCategory").value = category;
      await loadFeedAndEvents();
      setAssistantTurnText(assistantNode, `已更新信息流${category ? `：${category}` : "。"}。`);
      return null;
    }

    setAssistantTurnText(assistantNode, "可执行：/factcheck、/report、/brief、/related。");

    return null;
  } catch (error) {
    setAssistantTurnText(assistantNode, error.message);
    return null;
  }
}

async function runRelatedSearchIntoTurn(assistantNode, explicitTopic = "") {
  const currentTopic = consoleState.topic || document.querySelector("#topicInput")?.value?.trim() || "";
  const query = explicitTopic || currentTopic;
  const data = await request("/api/news/related", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: conversationId,
      user_id: activeUserId || "default",
      query: query || "当前关注",
      topic: currentTopic,
      category_scope: consoleState.categoryScope,
      max_queries: 8,
      allow_web_search: isWebSearchEnabled(),
    }),
  });
  conversationId = data.conversation_id;
  localStorage.setItem("pna_conversation_id", conversationId);
  setAssistantResponseHtml(assistantNode, chatResponseHtml(data));
  syncChatResponseContext(data);
  await notifyConversationHistoryChanged();
  return data;
}

async function createTopicFromFirstMessage(message, response, options = {}) {
  if (topicLocked && !pendingTopicFromNextMessage && options.force !== true) return null;
  if (!pendingTopicFromNextMessage && options.force !== true) return null;
  const createdConversationId = response?.conversation_id || conversationId;
  if (!createdConversationId) return null;
  if (isSafetyBlockedResponse(response)) {
    setStatus("这条内容未通过安全检查，未新增关注。");
    return null;
  }
  const title = canonicalTopicTitle(cleanSkillTopicTitle(options.title || response?.topic || response?.focus_object?.text || message)) || message;
  const categoryScope = options.categoryScope || response?.category_scope || consoleState.categoryScope || [];
  try {
    const result = await request("/api/topics", {
      method: "POST",
      body: JSON.stringify({
        user_id: activeUserId || "default",
        conversation_id: createdConversationId,
        title,
        category_scope: categoryScope,
        refresh_now: true,
      }),
    });
    const topic = result.topic || {};
    pendingTopicFromNextMessage = false;
    conversationId = createdConversationId;
    localStorage.setItem("pna_conversation_id", conversationId);
    consoleState.topic = topic.title || title;
    topicLocked = Boolean(consoleState.topic);
    consoleState.categoryScope = topic.category_scope || categoryScope;
    saveTopicContext();
    syncChatContext();
    syncContextDock();
    syncTaskTopic();
    await loadTopics();
    await loadTopicView();
    await loadDueUrls();
    setStatus(`已新建关注：${consoleState.topic}`);
    return result;
  } catch (error) {
    setStatus(error.message);
    return null;
  }
}

function isSafetyBlockedResponse(response) {
  return response?.context_relation === "query_moderation_blocked";
}

async function applyTopicCommand(command, options = {}) {
  const reload = options.reload !== false;
  const topic = commandText(command) || commandArg(command, "topic", "q", "query");
  const scope = commandScope(command, consoleState.categoryScope);
  if (topic && topicLocked && topic !== consoleState.topic) {
    setStatus(`当前对话主题已确定：${consoleState.topic}`);
    return null;
  }
  if (topic) {
    consoleState.topic = topic;
    topicLocked = true;
  }
  consoleState.categoryScope = scope;
  saveTopicContext();
  const topicInput = document.querySelector("#topicInput");
  const categorySelect = document.querySelector("#categoryScope");
  if (topicInput) topicInput.value = consoleState.topic;
  if (categorySelect) categorySelect.value = consoleState.categoryScope.join(",");
  syncChatContext();
  syncContextDock();
  syncTaskTopic();
  if (reload) await Promise.all([loadTopicView(), loadDueUrls()]);
}

function applyTaskCommand(command) {
  const form = document.querySelector("#taskForm");
  if (!form) return;
  const schedule = commandArg(command, "every", "schedule", "cron");
  const taskType = commandArg(command, "type");
  const delivery = commandArg(command, "push", "channel", "delivery");
  if (schedule) form.elements.schedule.value = schedule;
  if (taskType) form.elements.task_type.value = taskType;
  if (delivery && form.elements.delivery_channel) form.elements.delivery_channel.value = delivery;
  if (consoleState.categoryScope[0]) form.elements.category.value = consoleState.categoryScope[0];
  form.elements.topic.value = consoleState.topic;
}

async function loadSystemStatus() {
  try {
    const data = await request("/api/news/search/backend");
    const es = data.elasticsearch || {};
    const urlStore = data.crawl_url_store || {};
    document.querySelector("[data-es-status]").textContent = `ES ${es.ready ? "ready" : "down"} · ${es.cluster_status || "--"}`;
    document.querySelector("[data-mysql-status]").textContent = `MySQL ${urlStore.mysql_ready ? "ready" : "down"}`;
  } catch (error) {
    document.querySelector("[data-es-status]").textContent = "ES --";
    document.querySelector("[data-mysql-status]").textContent = "MySQL --";
  }
}

async function loadSourceSummary() {
  try {
    const data = await request("/api/sources/summary");
    document.querySelector("[data-source-count]").textContent = `源 ${data.source_count || 0}`;
    document.querySelector("[data-metric-sources]").textContent = data.source_count || 0;
    document.querySelector("[data-metric-crawlable]").textContent = data.crawlable_sources || 0;
    document.querySelector("[data-metric-searchable]").textContent = data.searchable_sources || 0;
    const tags = Object.entries(data.categories || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8);
    document.querySelector("[data-source-tags]").innerHTML = tags
      .map(([name, count]) => `<span>${escapeHtml(name)} ${count}</span>`)
      .join("");
  } catch (error) {
    document.querySelector("[data-source-tags]").textContent = error.message;
  }
}

async function loadFeedAndEvents() {
  const category = document.querySelector("#feedCategory")?.value || "";
  await Promise.all([loadFeed(category, 8, "#feed"), loadEvents("#events", category, 6)]);
  wireTitleEntryPrompts();
}

async function loadDueUrls() {
  const category = consoleState.categoryScope[0] || "";
  try {
    const data = await request(`/api/crawl/urls/due?limit=8${category ? `&category=${encodeURIComponent(category)}` : ""}`);
    renderDueUrls(data.items || []);
  } catch (error) {
    document.querySelector("[data-due-urls]").textContent = error.message;
  }
}

async function loadTopics() {
  const target = document.querySelector("[data-topic-list]");
  if (!target) {
    bindTopicCards();
    return;
  }
  const userId = activeUserId || "default";
  try {
    const data = await request(`/api/topics?user_id=${encodeURIComponent(userId)}&limit=50`);
    const items = mergeTopics([...(data.items || []), ...bootstrapTopics]);
    if (!items.length) {
      target.innerHTML = '<p class="rail-empty">暂无关注</p>';
      return;
    }
    target.innerHTML = items.map((item) => topicButtonHtml(item)).join("");
    bindTopicCards();
  } catch (error) {
    bindTopicCards();
  }
}

function bindRailTooltips() {
  let tooltip = null;
  const ensureTooltip = () => {
    if (tooltip) return tooltip;
    tooltip = document.createElement("div");
    tooltip.className = "rail-tooltip";
    document.body.appendChild(tooltip);
    return tooltip;
  };

  const tooltipText = (node) => (
    node?.dataset.tooltip
    || node?.dataset.topicTitle
    || node?.querySelector("span")?.textContent
    || ""
  ).trim();

  const shouldShowTooltip = (node) => {
    const label = node?.querySelector("span");
    return Boolean(label && (label.scrollWidth > label.clientWidth || tooltipText(node) !== label.textContent.trim()));
  };

  const positionTooltip = (node) => {
    const tip = ensureTooltip();
    const rect = node.getBoundingClientRect();
    const margin = 10;
    const width = tip.offsetWidth || 240;
    const left = Math.min(rect.right + margin, window.innerWidth - width - margin);
    const top = Math.min(Math.max(rect.top, margin), window.innerHeight - tip.offsetHeight - margin);
    tip.style.transform = `translate(${Math.max(margin, left)}px, ${Math.max(margin, top)}px)`;
  };

  document.addEventListener("pointerover", (event) => {
    const node = event.target.closest?.(".topic-card");
    if (!node || !shouldShowTooltip(node)) return;
    const tip = ensureTooltip();
    tip.textContent = tooltipText(node);
    positionTooltip(node);
    tip.classList.add("visible");
  });

  document.addEventListener("pointermove", (event) => {
    const node = event.target.closest?.(".topic-card");
    if (!node || !tooltip?.classList.contains("visible")) return;
    positionTooltip(node);
  });

  document.addEventListener("pointerout", (event) => {
    const node = event.target.closest?.(".topic-card");
    if (!node || node.contains(event.relatedTarget)) return;
    tooltip?.classList.remove("visible");
  });
}

function startNewTopicConversation() {
  pendingTopicFromNextMessage = true;
  topicLocked = false;
  conversationId = null;
  localStorage.removeItem("pna_conversation_id");
  clearSavedTopicContext();
  consoleState.topic = "";
  consoleState.categoryScope = [];
  consoleState.topicPayload = null;
  document.querySelectorAll(".topic-card").forEach((item) => item.classList.remove("active"));
  const messages = document.querySelector("#messages");
  if (messages) {
    messages.innerHTML = "";
    messages.appendChild(chatTurn("assistant", "请输入要关注的主题。你发出的第一句话会成为这组关注对话的主题。"));
  }
  const topicInput = document.querySelector("#topicInput");
  const categorySelect = document.querySelector("#categoryScope");
  const dialogContext = document.querySelector("[data-dialog-context]");
  if (topicInput) topicInput.value = "";
  if (categorySelect) categorySelect.value = "";
  if (dialogContext) dialogContext.textContent = "从一个新问题开始。";
  syncChatContext();
  syncContextDock();
  syncTaskTopic();
  document.querySelector("#message")?.focus();
}

async function loadTasks() {
  const target = document.querySelector("[data-task-list]");
  if (!target || !activeUserId || activeUserId === "default") return;
  try {
    const data = await request(`/api/tasks?user_id=${encodeURIComponent(activeUserId)}&limit=8`);
    renderTasks(data.items || []);
  } catch (error) {
    target.textContent = error.message;
  }
}

async function loadTopicView() {
  syncChatContext();
  if (!consoleState.topic) {
    consoleState.topicPayload = null;
    const heading = document.querySelector("[data-topic-heading]");
    const summary = document.querySelector("[data-topic-summary]");
    const visual = document.querySelector("[data-topic-visual]");
    const dialogContext = document.querySelector("[data-dialog-context]");
    if (heading) heading.textContent = "新对话";
    if (summary) summary.textContent = "发送第一句话后确定关注主题。";
    if (visual) visual.innerHTML = `<div class="empty-state">暂无主题</div>`;
    if (dialogContext) dialogContext.textContent = "新对话";
    document.querySelector("[data-topic-article-count]").textContent = "0";
    document.querySelector("[data-topic-event-count]").textContent = "0";
    document.querySelector("[data-topic-node-count]").textContent = "0";
    renderEvidence([]);
    renderInsightRail(null);
    syncContextDock();
    return;
  }
  try {
    const payload = await request("/api/topics/view", {
      method: "POST",
      body: JSON.stringify({
        topic: consoleState.topic,
        category_scope: consoleState.categoryScope,
        max_articles: 18,
      }),
    });
    consoleState.topicPayload = payload;
    renderTopicHeader(payload);
    renderTopicVisual(payload, consoleState.view);
    renderEvidence(payload.source_articles || []);
    renderInsightRail(payload);
  } catch (error) {
    document.querySelector("[data-topic-visual]").textContent = error.message;
  }
}

async function runNativeIngest() {
  setStatus("源搜索入库中。");
  const button = document.querySelector("#nativeIngest");
  button.disabled = true;
  try {
    const data = await request("/api/news/search/ingest", {
      method: "POST",
      body: JSON.stringify({
        query: consoleState.topic,
        category_scope: consoleState.categoryScope,
        max_results: 12,
        fetch_articles: 8,
        follow_depth: 1,
        follow_limit_per_article: 2,
      }),
    });
    setStatus(`入库完成：发现 ${data.discovered_count || 0}，索引 ${data.indexed_count || 0}。`);
    await Promise.all([loadTopicView(), loadDueUrls(), loadFeedAndEvents()]);
    return data;
  } catch (error) {
    setStatus(error.message);
    return null;
  } finally {
    button.disabled = false;
  }
}

async function runDeepDive() {
  setStatus("深度挖掘中。");
  const target = document.querySelector("[data-deep-dive-output]");
  target.textContent = "运行中";
  try {
    const data = await request("/api/news/deep-dive", {
      method: "POST",
      body: JSON.stringify({
        query: consoleState.topic,
        category_scope: consoleState.categoryScope,
        rounds: 2,
        breadth: 4,
        allow_web_search: isWebSearchEnabled(),
      }),
    });
    renderDeepDive(data);
    setStatus("深度挖掘完成。");
  } catch (error) {
    target.textContent = error.message;
    setStatus(error.message);
  }
}

async function createTrackingTask(form) {
  if (!form) return null;
  const formData = new FormData(form);
  const category = formData.get("category");
  const taskType = formData.get("task_type") || "topic_tracking";
  try {
    const data = await request("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        user_id: activeUserId,
        task_type: taskType,
        schedule: formData.get("schedule") || "*/20 * * * *",
        category_scope: category ? [category] : [],
        topics: [formData.get("topic") || consoleState.topic],
        output_style: "事件线+关系网",
        delivery_channel: formData.get("delivery_channel") || "in_app",
      }),
    });
    document.querySelector("[data-task-status]").textContent = `已保存：${nextRunLabel(data.next_run_at)}`;
    setStatus(`任务已保存：${data.id || "task"}`);
    await loadTasks();
    return data;
  } catch (error) {
    document.querySelector("[data-task-status]").textContent = error.message;
    setStatus(error.message);
    return null;
  }
}

function renderTasks(items) {
  const target = document.querySelector("[data-task-list]");
  if (!target) return;
  if (!items.length) {
    target.innerHTML = `<div class="empty-state compact-empty">暂无任务</div>`;
    return;
  }
  target.innerHTML = items
    .map(
      (item) => {
        const title = (item.topics || []).join("、") || taskTypeLabel(item.task_type);
        const enabled = item.enabled !== false;
        const meta = enabled ? `${taskTypeLabel(item.task_type)} · ${item.schedule_cron} · ${nextRunLabel(item.next_run_at)}` : "已禁用";
        return `<article class="${enabled ? "" : "disabled"}" role="button" tabindex="0" data-task-id="${escapeAttr(item.id)}" data-task-enabled="${enabled ? "true" : "false"}" data-task-title="${escapeAttr(title)}" data-task-meta="${escapeAttr(meta)}">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(meta)}</span>
      </article>`
      }
    )
    .join("");
}

function bindTaskActions() {
  document.addEventListener("click", (event) => {
    const item = event.target.closest("[data-task-id]");
    if (!item) return;
    showTaskActionPopover(item);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const item = event.target.closest("[data-task-id]");
    if (!item) return;
    event.preventDefault();
    showTaskActionPopover(item);
  });
}

function showTaskActionPopover(item) {
  closeEventActionPopover();
  const taskId = item.dataset.taskId || "";
  const enabled = item.dataset.taskEnabled !== "false";
  const title = item.dataset.taskTitle || "定时跟踪任务";
  const meta = item.dataset.taskMeta || "选择接下来要做的动作。";
  const actionLabel = enabled ? "禁用" : "启用";
  const rect = item.getBoundingClientRect();
  const popover = document.createElement("div");
  popover.className = "event-action-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", `定时跟踪操作：${title}`);
  popover.innerHTML = `
    <strong>${escapeHtml(title)}</strong>
    <p>${escapeHtml(meta)}</p>
    <div>
      <button type="button" data-task-popover-toggle>${actionLabel}</button>
      <button type="button" data-task-popover-delete>删除</button>
      <button type="button" data-task-popover-cancel>关闭</button>
    </div>
  `;
  document.body.appendChild(popover);
  const left = Math.min(window.innerWidth - popover.offsetWidth - 12, Math.max(12, rect.right + 8));
  const top = Math.min(window.innerHeight - popover.offsetHeight - 12, Math.max(12, rect.top));
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;

  popover.querySelector("[data-task-popover-toggle]")?.addEventListener("click", async () => {
    if (!taskId) return;
    await setTrackingTaskEnabled(taskId, !enabled);
    closeEventActionPopover();
  });
  popover.querySelector("[data-task-popover-delete]")?.addEventListener("click", async () => {
    if (!taskId) return;
    await deleteTrackingTask(taskId);
    closeEventActionPopover();
  });
  popover.querySelector("[data-task-popover-cancel]")?.addEventListener("click", closeEventActionPopover);

  const onPointerDown = (event) => {
    if (popover.contains(event.target) || item.contains(event.target)) return;
    closeEventActionPopover();
  };
  const onKeyDown = (event) => {
    if (event.key === "Escape") closeEventActionPopover();
  };
  setTimeout(() => {
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
  }, 0);
  activeEventActionPopover = popover;
  activeEventActionCleanup = () => {
    document.removeEventListener("pointerdown", onPointerDown);
    document.removeEventListener("keydown", onKeyDown);
  };
}

async function setTrackingTaskEnabled(taskId, enabled) {
  try {
    await request(`/api/tasks/${encodeURIComponent(taskId)}/enabled`, {
      method: "POST",
      body: JSON.stringify({ user_id: activeUserId, enabled }),
    });
    document.querySelector("[data-task-status]").textContent = enabled ? "任务已启用。" : "任务已禁用。";
    setStatus(enabled ? "定时跟踪任务已启用。" : "定时跟踪任务已禁用。");
    await loadTasks();
  } catch (error) {
    document.querySelector("[data-task-status]").textContent = error.message;
    setStatus(error.message);
  }
}

async function deleteTrackingTask(taskId) {
  try {
    await request(`/api/tasks/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(activeUserId)}`, {
      method: "DELETE",
    });
    document.querySelector("[data-task-status]").textContent = "任务已删除。";
    setStatus("定时跟踪任务已删除。");
    await loadTasks();
  } catch (error) {
    document.querySelector("[data-task-status]").textContent = error.message;
    setStatus(error.message);
  }
}

function bindTopicCards() {
  document.querySelectorAll(".topic-card").forEach((button) => {
    if (button.dataset.bound === "true") return;
    button.dataset.bound = "true";
    button.addEventListener("click", async () => {
      const selectedConversationId = button.dataset.conversationId || "";
      const selectedTopic = button.dataset.topicTitle || button.textContent.trim();
      const selectedScope = parseScope(button.dataset.categoryScope || "");
      if (!selectedConversationId && topicLocked && consoleState.topic && selectedTopic !== consoleState.topic) {
        document.querySelectorAll(".topic-card").forEach((item) => item.classList.toggle("active", item.dataset.topicTitle === consoleState.topic));
        setStatus(`当前对话主题已确定：${consoleState.topic}`);
        return;
      }
      document.querySelectorAll(".topic-card").forEach((item) => item.classList.toggle("active", item === button));
      pendingTopicFromNextMessage = false;
      if (selectedConversationId) {
        conversationId = selectedConversationId;
        localStorage.setItem("pna_conversation_id", conversationId);
      } else {
        conversationId = null;
        localStorage.removeItem("pna_conversation_id");
      }
      consoleState.topic = selectedTopic;
      topicLocked = Boolean(consoleState.topic);
      consoleState.categoryScope = selectedScope;
      saveTopicContext();
      const previousTopic = consoleState.topic;
      consoleState.topic = button.dataset.topicTitle || button.textContent.trim();
      consoleState.categoryScope = parseScope(button.dataset.categoryScope || "");
      saveTopicContext();
      syncChatContext();
      syncContextDock();
      document.querySelector("#topicInput").value = consoleState.topic;
      document.querySelector("#categoryScope").value = button.dataset.categoryScope || "";
      syncTaskTopic();
      const messages = document.querySelector("#messages");
      if (messages) {
        messages.innerHTML = "";
        messages.appendChild(chatTurn("assistant", `已切换到：${selectedTopic}`));
      }
      if (selectedConversationId) {
        const restored = await restoreChatMemory("#messages");
        if (!restored && messages) {
          messages.innerHTML = "";
          messages.appendChild(chatTurn("assistant", `已切换到：${selectedTopic}`));
        }
        consoleState.topic = selectedTopic;
        topicLocked = Boolean(consoleState.topic);
        consoleState.categoryScope = selectedScope;
        saveTopicContext();
        syncChatContext();
        syncContextDock();
        document.querySelector("#topicInput").value = consoleState.topic;
        document.querySelector("#categoryScope").value = button.dataset.categoryScope || "";
        syncTaskTopic();
      }
      await loadTopicView();
      await loadDueUrls();
      await loadTopicView();
      await loadDueUrls();
      if (previousTopic !== consoleState.topic) {
        await sendChat(`${consoleState.topic} 最近有什么值得关注的变化？`);
      }
    });
  });
}

function mergeTopics(items) {
  const seen = new Set();
  const merged = [];
  items.forEach((item) => {
    const title = item.title || "";
    if (!title) return;
    const identity = item.id || item.topic_id || item.conversation_id;
    const scope = (item.category_scope || []).join(",");
    const systemKey = item.topic_type === "system"
      ? `system:${canonicalTopicTitle(title)}:${scope}`
      : "";
    if (systemKey && seen.has(systemKey)) return;
    const key = identity
      ? `topic:${identity}`
      : `seed:${item.topic_type || "topic"}:${canonicalTopicTitle(title)}:${scope}`;
    if (seen.has(key)) return;
    seen.add(key);
    if (systemKey) seen.add(systemKey);
    if (!title || seen.has(title)) return;
    seen.add(title);
    merged.push(item);
  });
  return merged;
}

function canonicalTopicTitle(title) {
  let cleaned = String(title || "").replace(/\s+/g, " ").trim();
  const quoted =
    cleaned.match(/追踪[「“"《](.+?)[」”"》]/) ||
    cleaned.match(/围绕[「“"《](.+?)[」”"》]/) ||
    cleaned.match(/基于资讯[「“"《](.+?)[」”"》]/);
  if (quoted?.[1]) cleaned = quoted[1].trim();
  cleaned = cleaned
    .replace(/^追踪\s*/, "")
    .replace(/是否出现后续回应或新进展.*$/, "")
    .replace(/后续回应或新进展.*$/, "")
    .replace(/\s*有什么.*$/, "")
    .replace(/\s*有哪些.*$/, "")
    .replace(/\s*最近.*$/, "")
    .replace(/\s*值得关注.*$/, "")
    .replace(/\s*新变化.*$/, "")
    .replace(/\s*变化.*$/, "")
    .replace(/\s*消息.*$/, "")
    .replace(/^和其他(.+?)做对比呢?$/, "$1")
    .replace(/^其他(.+?)做对比呢?$/, "$1")
    .trim();
  return cleaned || String(title || "").trim();
}

function topicButtonHtml(item) {
  const title = item.title || "";
  const scope = (item.category_scope || []).join(",");
  const itemConversationId = item.conversation_id || "";
  const active = (itemConversationId && itemConversationId === conversationId) || title === consoleState.topic ? " active" : "";
  const kind = item.topic_type === "system" ? " system-topic" : " user-topic";
  const meta = scope ? scope.split(",").join(" / ") : (item.topic_type === "system" ? "system" : "all");
  return `<button class="topic-card${kind}${active}" type="button" data-topic-title="${escapeAttr(title)}" data-conversation-id="${escapeAttr(itemConversationId)}" data-category-scope="${escapeAttr(scope)}" data-tooltip="${escapeAttr(title)}"><span>${escapeHtml(shortTopicTitle(title))}</span><small>${escapeHtml(meta)}</small></button>`;
}

function shortTopicTitle(title) {
  return title
    .replace("传闻", "")
    .replace("2026 ", "")
    .replace("的影响", "")
    .trim();
}

function taskTypeLabel(value) {
  const labels = {
    topic_tracking: "专题跟踪",
    daily_digest: "每日摘要",
    weekly_digest: "每周摘要",
  };
  return labels[value] || value || "任务";
}

function nextRunLabel(value) {
  if (!value) return "未定时";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未定时";
  return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

async function generateTopicReport(options = {}) {
  const chatFollowup = options.chatFollowup !== false;
  setStatus("正在生成专题报告。");
  try {
    const data = await request("/api/reports", {
      method: "POST",
      body: JSON.stringify({
        user_id: activeUserId,
        topic: consoleState.topic,
        category_scope: consoleState.categoryScope,
        time_range: "30d",
        report_type: "timeline_analysis",
      }),
    });
    renderReportCard(data);
    if (chatFollowup) {
      await sendChat(`基于当前${consoleState.topic}专题报告，提炼最适合继续追问的三个问题。`);
    }
    setStatus(`报告已生成：${data.report_id}`);
    return data;
  } catch (error) {
    setStatus(error.message);
    return null;
  }
}

function renderReportCard(data) {
  const target = document.querySelector("[data-deep-dive-output]");
  if (!target) return;
  const sections = data.sections || {};
  const summary = sections["一、结论摘要"] || sections.summary || "";
  const reportId = data.report_id || "";
  const userId = activeUserId || "default";
  const base = reportId ? appUrl(`/api/reports/${encodeURIComponent(reportId)}/download?user_id=${encodeURIComponent(userId)}`) : "";
  target.innerHTML = `<div class="deep-section">
    <strong>${escapeHtml(data.topic || consoleState.topic)}</strong>
    <p>${escapeHtml(summary)}</p>
    <span>${escapeHtml(reportId)}</span>
    ${base ? `<div class="report-downloads"><a href="${base}&format=pdf" download>下载 PDF</a><a href="${base}&format=docx" download>下载 Word</a></div>` : ""}
  </div>`;
}

function renderTopicHeader(payload) {
  const events = payload.event_line?.items || [];
  const nodes = payload.relation_graph?.nodes || [];
  const articles = payload.source_articles || [];
  const latest = events[events.length - 1];
  document.querySelector("[data-topic-heading]").textContent = payload.topic?.title || consoleState.topic;
  const dialogContext = document.querySelector("[data-dialog-context]");
  const dialogText = `${payload.topic?.title || consoleState.topic} · ${articles.length || payload.build?.article_count || 0} 条证据，${events.length} 个事件。`;
  if (dialogContext) {
    dialogContext.textContent = dialogText;
    dialogContext.title = dialogText;
  }
  document.querySelector("[data-dialog-context]").textContent = `${payload.topic?.title || consoleState.topic} · ${articles.length || payload.build?.article_count || 0} 条证据，${events.length} 个事件。`;
  document.querySelector("[data-topic-summary]").textContent = latest
    ? `${latest.date} · ${latest.title}`
    : `${payload.build?.article_count || 0} 条资料用于构建专题。`;
  document.querySelector("[data-topic-article-count]").textContent = articles.length || payload.build?.article_count || 0;
  document.querySelector("[data-topic-event-count]").textContent = events.length;
  document.querySelector("[data-topic-node-count]").textContent = nodes.length;
  syncContextDock();
  updateAgentBrief(payload);
}

function renderTopicVisual(payload, viewType = "event-line") {
  const target = document.querySelector("[data-topic-visual]");
  if (!target || !payload) return;
  target.innerHTML = viewType === "relation-graph" ? relationGraphHtml(payload.relation_graph) : eventLineHtml(payload.event_line);
}

function eventLineHtml(eventLine) {
  const items = eventLine?.items || [];
  if (!items.length) return `<div class="empty-state">暂无事件</div>`;
  return `<div class="topic-event-line console-event-line">
    ${items
      .map((item) => {
        const tags = [...(item.actors || []), ...(item.keywords || [])].slice(0, 4);
        const dateLabel = item.date_source === "fetched_at" ? `抓取 ${item.date}` : item.date;
        return `<article class="topic-event ${escapeAttr(item.stage)}">
          <time>${escapeHtml(dateLabel)}</time>
          <div>
            <strong>${escapeHtml(item.title)}</strong>
            <p>${escapeHtml(item.summary)}</p>
            <span>${escapeHtml(tags.join(" / "))}</span>
          </div>
        </article>`;
      })
      .join("")}
  </div>`;
}

function relationGraphHtml(graph) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  if (!nodes.length) return `<div class="empty-state">暂无关系节点</div>`;
  const positions = graphPositions(nodes, 760, 420);
  const lines = edges
    .map((edge) => {
      const source = positions[edge.source];
      const target = positions[edge.target];
      if (!source || !target) return "";
      return `<line x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" stroke-width="${Math.min(5, 1 + edge.weight)}"><title>${escapeHtml(edge.label || "")}</title></line>`;
    })
    .join("");
  const circles = nodes
    .map((node) => {
      const pos = positions[node.id];
      const radius = node.type === "topic" ? 46 : Math.max(25, Math.min(38, 22 + node.weight * 3));
      return `<g class="graph-node ${escapeAttr(node.type)}" transform="translate(${pos.x}, ${pos.y})">
        <circle r="${radius}"></circle>
        <text text-anchor="middle" dominant-baseline="middle">${escapeHtml(shortLabel(node.label, 9))}</text>
        <title>${escapeHtml(`${node.label} · ${node.weight}`)}</title>
      </g>`;
    })
    .join("");
  return `<svg class="topic-relation-graph console-graph" viewBox="0 0 760 420" role="img" aria-label="专题关系网">${lines}${circles}</svg>`;
}

function renderEvidence(articles) {
  const target = document.querySelector("[data-evidence-strip]");
  if (!articles.length) {
    target.innerHTML = `<div class="empty-state">暂无来源证据</div>`;
    return;
  }
  target.innerHTML = articles
    .slice(0, 8)
    .map(
      (item) => `<article>
        <strong>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml(item.source_id)} · ${escapeHtml(articleDateLabel(item))}</span>
      </article>`
    )
    .join("");
}

function renderInsightRail(payload) {
  const articles = payload?.source_articles || [];
  const events = payload?.event_line?.items || [];
  const nodes = (payload?.relation_graph?.nodes || []).filter((node) => node.id !== "topic");
  renderFollowUps(events, nodes);
  renderEntitySources(nodes, articles);
}

function handleWebChatResponseSideEffects(response) {
  const articles = responseScopedArticles(response);
  const events = response?.event_line?.items || [];
  const nodes = responseScopedNodes(response, articles);
  renderResponseScopedFeed(articles);
  renderFollowUps(events, nodes, null, response?.focus_object?.text || response?.topic || "");
  renderEntitySources(nodes, articles);
  return response;
}

function responseScopedArticles(response) {
  const candidates = response?.evidence?.length
    ? response.evidence
    : response?.recommendations?.length
      ? response.recommendations
      : response?.skill_result?.data?.sources || [];
  return (candidates || []).filter((item) => item && item.title);
}

function responseScopedNodes(response, articles) {
  const nodes = [];
  const focus = response?.focus_object?.text || response?.topic || "";
  if (focus) nodes.push({ label: focus });
  articles.forEach((item) => {
    const label = item.source_id || item.source || "";
    if (label && !nodes.some((node) => node.label === label)) nodes.push({ label });
  });
  return nodes;
}

function renderResponseScopedFeed(articles) {
  const target = document.querySelector("#feed");
  const count = document.querySelector("[data-related-article-count]");
  if (count) count.textContent = `${articles.length} 条`;
  if (!target) return;
  if (!articles.length) {
    target.innerHTML = `<div class="empty-state compact-empty">本轮暂无相关资讯</div>`;
    return;
  }
  target.innerHTML = articles.slice(0, 8).map((item) => itemHtml(item)).join("");
  wireTitleEntryPrompts();
}

function renderFollowUps(events, nodes, attentionSuggestion = null, topicOverride = "") {
  const target = document.querySelector("[data-follow-up-list]");
  if (!target) return;
  const latest = events[events.length - 1];
  const leadEntity = nodes[0]?.label || "关键主体";
  const topic = topicOverride || consoleState.topic;
  const questions = [
    latest ? `追踪「${latest.title}」是否出现后续回应或新进展。` : `继续观察「${topic}」是否出现新的权威来源。`,
    `关注${leadEntity}相关的政策、数据或执行动作。`,
    "对比不同来源的说法是否一致，留意争议、反转和补充证据。",
    "观察后续是否影响市场、行业、公众服务或地方执行。",
  ];
  const buttons = questions
    .slice(0, 4)
    .map((item) => `<button type="button" data-ask="${escapeAttr(item)}">${escapeHtml(item)}</button>`);
  if (attentionSuggestion?.topic) {
    buttons.unshift(`<p>${escapeHtml(attentionSuggestion.message || `这条追问更像新的关注：${attentionSuggestion.topic}`)}</p>`);
  }
  target.innerHTML = buttons.join("");
}

function renderEntitySources(nodes, articles) {
  const entityTarget = document.querySelector("[data-entity-list]");
  const sourceTarget = document.querySelector("[data-source-list]");
  if (entityTarget) {
    const entities = nodes.filter((node) => !isSystemEntityLabel(node.label)).slice(0, 8);
    entityTarget.innerHTML = `<strong>相关主体</strong>${entities.length ? entities.map((node) => `<span>${escapeHtml(node.label)}</span>`).join("") : "<p>暂无</p>"}`;
  }
  if (sourceTarget) {
    const sources = [];
    articles.forEach((item) => {
      if (!item.source_id || sources.some((source) => source.id === item.source_id)) return;
      sources.push({ id: item.source_id, url: item.url || "" });
    });
    sourceTarget.innerHTML = `<strong>来源网站</strong>${
      sources.length
        ? sources.slice(0, 8).map((source) => source.url ? `<a href="${escapeAttr(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.id)}</a>` : `<span>${escapeHtml(source.id)}</span>`).join("")
        : "<p>暂无</p>"
    }`;
  }
}

function isSystemEntityLabel(value) {
  const text = String(value || "").replace(/\s+/g, "");
  if (!text) return true;
  return [
    "暂无足够入库资料",
    "后续将通过源搜索",
    "抓取和大模型抽取补全",
  ].some((marker) => text.includes(marker));
}

function renderDueUrls(items) {
  const target = document.querySelector("[data-due-urls]");
  if (!items.length) {
    target.innerHTML = `<div class="empty-state">暂无待抓 URL</div>`;
    return;
  }
  target.innerHTML = items
    .slice(0, 8)
    .map(
      (item) => `<article>
        <strong>${escapeHtml(item.title || item.url)}</strong>
        <span>${escapeHtml(item.source_id)} · ${escapeHtml(item.status)} · ${escapeHtml(item.fetch_count ?? 0)} 次</span>
      </article>`
    )
    .join("");
}

function renderDeepDive(data) {
  const target = document.querySelector("[data-deep-dive-output]");
  const queries = (data.expanded_queries || data.queries || []).map((item) => (typeof item === "string" ? item : item.query || item.rationale || ""));
  const items = data.items || data.results || [];
  const rounds = data.rounds || [];
  target.innerHTML = `
    <div class="deep-section">
      <strong>扩展词</strong>
      <p>${escapeHtml(queries.slice(0, 8).join(" / ") || "暂无")}</p>
    </div>
    <div class="deep-section">
      <strong>结果</strong>
      <p>${escapeHtml(String(items.length || rounds.length || 0))} 条</p>
    </div>
  `;
}

function graphPositions(nodes, width, height) {
  const positions = {};
  const center = { x: width / 2, y: height / 2 };
  const outer = nodes.filter((node) => node.id !== "topic");
  positions.topic = center;
  outer.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(1, outer.length) - Math.PI / 2;
    const rx = width * 0.36;
    const ry = height * 0.34;
    positions[node.id] = { x: center.x + Math.cos(angle) * rx, y: center.y + Math.sin(angle) * ry };
  });
  return positions;
}

function parseScope(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function readSavedTopicContext() {
  try {
    const payload = JSON.parse(localStorage.getItem(topicStorageKey()) || "{}");
    if (payload.version !== TOPIC_CONTEXT_VERSION) return { topic: "", categoryScope: [] };
    return {
      topic: String(payload.topic || "").trim(),
      categoryScope: Array.isArray(payload.category_scope) ? payload.category_scope : [],
    };
  } catch (error) {
    return { topic: "", categoryScope: [] };
  }
}

function saveTopicContext() {
  if (!consoleState.topic) {
    clearSavedTopicContext();
    return;
  }
  localStorage.setItem(topicStorageKey(), JSON.stringify({
    version: TOPIC_CONTEXT_VERSION,
    topic: consoleState.topic,
    category_scope: consoleState.categoryScope || [],
  }));
}

function clearSavedTopicContext() {
  localStorage.removeItem(topicStorageKey());
}

function currentTopicLabel() {
  return consoleState.topic || "新对话";
}

function syncTaskTopic() {
  const form = document.querySelector("#taskForm");
  if (!form) return;
  form.elements.topic.value = consoleState.topic;
  if (consoleState.categoryScope[0]) form.elements.category.value = consoleState.categoryScope[0];
}

function syncChatContext() {
  window.currentChatContext = {
    topic: consoleState.topic,
    category_scope: consoleState.categoryScope,
    use_llm: true,
    allow_web_search: isWebSearchEnabled(),
  };
}

function applyChatConversationContext(context = {}) {
  if (Object.prototype.hasOwnProperty.call(context, "topic")) {
    const nextTopic = cleanSkillTopicTitle(context.topic || "");
    if (nextTopic && topicLocked && nextTopic !== consoleState.topic) return;
    if (nextTopic) {
      consoleState.topic = nextTopic;
      if (!pendingTopicFromNextMessage) topicLocked = true;
    }
  }
  if (Array.isArray(context.category_scope)) consoleState.categoryScope = context.category_scope;
  saveTopicContext();
  const topicInput = document.querySelector("#topicInput");
  const categorySelect = document.querySelector("#categoryScope");
  if (topicInput) topicInput.value = consoleState.topic;
  if (categorySelect) categorySelect.value = consoleState.categoryScope.join(",");
  syncChatContext();
  syncContextDock();
  syncTaskTopic();
}

function cleanSkillTopicTitle(value) {
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
function syncContextDock() {
  const topic = document.querySelector("[data-current-topic-chip]");
  const category = document.querySelector("[data-current-category-chip]");
  const view = document.querySelector("[data-current-view-chip]");
  if (topic) {
    topic.textContent = currentTopicLabel();
    topic.title = topic.textContent;
  }
  if (category) {
    category.textContent = consoleState.categoryScope.join(" / ") || "all";
    category.title = category.textContent;
  }
  if (topic) topic.textContent = currentTopicLabel();
  if (category) category.textContent = consoleState.categoryScope.join(" / ") || "all";
  if (view) view.textContent = consoleState.view === "relation-graph" ? "关系网" : "事件线";
  updateAgentBrief(consoleState.topicPayload);
}

function updateAgentBrief(payload) {
  const topic = document.querySelector("[data-agent-topic]");
  const category = document.querySelector("[data-agent-category]");
  const evidence = document.querySelector("[data-agent-evidence]");
  const next = document.querySelector("[data-agent-next]");
  const articles = payload?.source_articles || [];
  const events = payload?.event_line?.items || [];
  if (topic) {
    topic.textContent = currentTopicLabel();
    topic.title = topic.textContent;
  }
  if (category) {
    category.textContent = consoleState.categoryScope.join(" / ") || "all";
    category.title = category.textContent;
  }
  if (topic) topic.textContent = currentTopicLabel();
  if (category) category.textContent = consoleState.categoryScope.join(" / ") || "all";
  if (evidence) evidence.textContent = String(articles.length || payload?.build?.article_count || 0);
  if (next) next.textContent = events.length >= 3 ? "报告" : "深挖";
}

function setStatus(message) {
  const target = document.querySelector("[data-command-status]");
  if (target) target.textContent = message;
}

function wireTitleEntryPrompts() {
  document.querySelectorAll("#feed .item, #events .item").forEach((item) => {
    if (item.dataset.wiredAsk === "1") return;
    const title = item.querySelector(".title")?.textContent?.trim();
    if (!title) return;
    item.dataset.wiredAsk = "1";
    item.tabIndex = 0;
    item.setAttribute("role", "button");
    item.setAttribute("aria-label", `在对话中查看：${title}`);
    const isEventCard = Boolean(item.closest("#events"));
    const ask = isEventCard
      ? `围绕热点事件“${title}”展开，告诉我发生了什么、为什么重要、后续看什么。`
      : `基于资讯“${title}”继续深挖，给我结论、证据和后续观察点。`;
    const run = () => runFeedEventAsk(item, title, ask, isEventCard);
    item.addEventListener("click", (event) => {
      if (isEventCard) {
        event.preventDefault();
        showEventActionPopover(item, title, ask);
        return;
      }
      run();
    });
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (isEventCard) {
          showEventActionPopover(item, title, ask);
        } else {
          run();
        }
      }
    });
  });
}

function showEventActionPopover(item, title, ask) {
  closeEventActionPopover();
  const sourceUrl = item.dataset.eventUrl || "";
  const sourceTitle = item.dataset.eventSourceTitle || title;
  const rect = item.getBoundingClientRect();
  const popover = document.createElement("div");
  popover.className = "event-action-popover";
  popover.setAttribute("role", "dialog");
  popover.setAttribute("aria-label", `热点事件操作：${title}`);
  popover.innerHTML = `
    <strong>${escapeHtml(title)}</strong>
    <p>${escapeHtml(sourceTitle || "选择接下来要做的动作。")}</p>
    <div>
      <button type="button" data-event-popover-send>发送到对话框</button>
      <button type="button" data-event-popover-open ${sourceUrl ? "" : "disabled"}>打开原网址</button>
    </div>
  `;
  document.body.appendChild(popover);
  const left = Math.min(window.innerWidth - popover.offsetWidth - 12, Math.max(12, rect.right + 8));
  const top = Math.min(window.innerHeight - popover.offsetHeight - 12, Math.max(12, rect.top));
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;

  popover.querySelector("[data-event-popover-send]")?.addEventListener("click", async () => {
    closeEventActionPopover();
    await runFeedEventAsk(item, title, ask, true);
  });
  popover.querySelector("[data-event-popover-open]")?.addEventListener("click", () => {
    if (!sourceUrl) return;
    closeEventActionPopover();
    window.open(sourceUrl, "_blank", "noopener,noreferrer");
  });

  const onPointerDown = (event) => {
    if (popover.contains(event.target) || item.contains(event.target)) return;
    closeEventActionPopover();
  };
  const onKeyDown = (event) => {
    if (event.key === "Escape") closeEventActionPopover();
  };
  setTimeout(() => {
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
  }, 0);
  activeEventActionPopover = popover;
  activeEventActionCleanup = () => {
    document.removeEventListener("pointerdown", onPointerDown);
    document.removeEventListener("keydown", onKeyDown);
  };
}

function closeEventActionPopover() {
  if (activeEventActionCleanup) activeEventActionCleanup();
  activeEventActionCleanup = null;
  if (activeEventActionPopover) activeEventActionPopover.remove();
  activeEventActionPopover = null;
}

async function runFeedEventAsk(item, title, ask, isEventCard) {
  const category = isEventCard ? eventCardCategory(item) : feedItemCategory(item);
  const categoryScope = category ? [category] : [];
  if (!topicLocked) {
    consoleState.topic = title;
    topicLocked = Boolean(consoleState.topic);
    consoleState.categoryScope = categoryScope;
  }
  const topicInput = document.querySelector("#topicInput");
  const categorySelect = document.querySelector("#categoryScope");
  if (topicInput) topicInput.value = consoleState.topic;
  if (categorySelect) categorySelect.value = consoleState.categoryScope.join(",");
  syncChatContext();
  syncContextDock();
  syncTaskTopic();
  const response = await sendChat(ask);
  await createTopicFromFirstMessage(ask, response, { title, categoryScope });
  return response;
}

function feedItemCategory(item) {
  const meta = item.querySelector(".meta")?.textContent || "";
  const parts = meta.split("·").map((part) => part.trim()).filter(Boolean);
  return isKnownCategory(parts[1]) ? parts[1] : "";
}

function eventCardCategory(item) {
  const meta = item.querySelector(".meta")?.textContent || "";
  const category = meta.split("·")[0]?.trim();
  return isKnownCategory(category) ? category : "";
}

function isKnownCategory(category) {
  return ["politics", "economy", "tech", "auto", "game", "anime", "entertainment", "sports"].includes(String(category || "").trim());
}

function shortLabel(value, maxLength) {
  const text = String(value || "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function articleDateLabel(item) {
  if (item.published_at) return item.published_at.slice(0, 10);
  if (item.fetched_at) return `抓取 ${item.fetched_at.slice(0, 10)}`;
  return "--";
}
