let activeUserId = localStorage.getItem("pna_user_id") || "default";
let conversationId = localStorage.getItem("pna_conversation_id") || null;
let webSearchEnabled = localStorage.getItem(webSearchPreferenceKey()) === "1";
const TOPIC_DRIFT_NOTICE = "提示：这条追问和当前关注主题关联较弱，我会照常回答，但不会因此更改当前主题或新增关注卡片。";
const MULTI_FOCUS_DRIFT_NOTICE = "提示：这条消息里包含多个彼此关联较弱的热点，我会照常分别回答，但不会把它们合并成同一个主题或新增关注卡片。";
const TOPIC_DRIFT_NOTICES = [TOPIC_DRIFT_NOTICE, MULTI_FOCUS_DRIFT_NOTICE];

function webSearchPreferenceKey() {
  return `pna_web_search_enabled:${activeUserId || "default"}`;
}

function isWebSearchEnabled() {
  return webSearchEnabled;
}

function setWebSearchEnabled(enabled) {
  webSearchEnabled = Boolean(enabled);
  localStorage.setItem(webSearchPreferenceKey(), webSearchEnabled ? "1" : "0");
  document.querySelectorAll("[data-web-search-toggle]").forEach((input) => {
    input.checked = webSearchEnabled;
  });
  if (window.currentChatContext) window.currentChatContext.allow_web_search = webSearchEnabled;
}

function bindWebSearchToggles() {
  document.querySelectorAll("[data-web-search-toggle]").forEach((input) => {
    input.checked = webSearchEnabled;
    input.addEventListener("change", () => setWebSearchEnabled(input.checked));
  });
}

bindWebSearchToggles();

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(await formatApiError(response));
  return response.json();
}

async function formatApiError(response) {
  const fallback = `请求失败：${response.status}`;
  let payload;
  try {
    payload = await response.json();
  } catch (error) {
    try {
      return (await response.text()) || fallback;
    } catch (innerError) {
      return fallback;
    }
  }
  const detail = payload?.detail;
  if (Array.isArray(detail)) {
    return detail.map(validationErrorMessage).join("；") || fallback;
  }
  if (typeof detail === "string") return friendlyErrorMessage(detail);
  if (detail) return friendlyErrorMessage(JSON.stringify(detail));
  return fallback;
}

function validationErrorMessage(item) {
  const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
  const labels = {
    username: "用户名",
    password: "密码",
    confirm_password: "确认密码",
    real_name: "真实姓名",
    mobile: "手机号",
  };
  const label = labels[field] || field || "输入";
  if (item.type === "string_too_short" && item.ctx?.min_length) return `${label}至少需要 ${item.ctx.min_length} 个字符`;
  if (item.type === "string_too_long" && item.ctx?.max_length) return `${label}不能超过 ${item.ctx.max_length} 个字符`;
  return `${label}格式不正确`;
}

function friendlyErrorMessage(message) {
  const known = {
    "username already registered": "用户名已注册，请换一个用户名或直接登录。",
    "mobile already registered": "手机号已注册，请直接登录或换一个手机号。",
    "password and confirm_password do not match": "两次输入的密码不一致。",
    "mobile format is invalid": "手机号格式不正确，请输入 11 位中国大陆手机号。",
    "username must be at least 3 characters": "用户名至少需要 3 个字符。",
    "invalid email or password": "用户名或密码不正确。",
  };
  return known[message] || message;
}

async function runFormAction(form, statusSelector, loadingText, action) {
  const status = document.querySelector(statusSelector);
  const buttons = Array.from(form.querySelectorAll("button"));
  if (status) status.textContent = loadingText;
  form.setAttribute("aria-busy", "true");
  buttons.forEach((button) => {
    button.disabled = true;
  });
  try {
    return await action();
  } finally {
    form.removeAttribute("aria-busy");
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
}

function saveSession(authResult) {
  if (!authResult || !authResult.user) return;
  activeUserId = authResult.user.id;
  localStorage.setItem("pna_user_id", activeUserId);
  localStorage.setItem("pna_user_name", authResult.user.display_name || "");
  if (authResult.session) {
    localStorage.setItem("pna_session_token", authResult.session.token);
  }
  renderUser();
}

function renderUser() {
  document.querySelectorAll("[data-user-name]").forEach((node) => {
    node.textContent = localStorage.getItem("pna_user_name") || (activeUserId === "default" ? "未注册用户" : activeUserId);
  });
}

function itemHtml(item) {
  return `<article class="item">
    <div class="title">${escapeHtml(item.title)}</div>
    <div class="meta">${escapeHtml(item.source || item.source_id)} · ${escapeHtml(item.category)} · ${escapeHtml(item.recommend_reason || "")}</div>
    <div class="summary">${escapeHtml(item.summary || "")}</div>
  </article>`;
}

function prototypeItemHtml(item, actionText = "追问") {
  const ask = `${item.title || ""} 继续展开说说`;
  return `<article class="prototype-item">
    <div>
      <div class="title">${escapeHtml(item.title)}</div>
      <div class="meta">${escapeHtml(item.source || item.source_id)} · ${escapeHtml(item.category)}</div>
      <div class="summary">${escapeHtml(item.summary || item.recommend_reason || "")}</div>
    </div>
    <button data-ask="${escapeAttr(ask)}">${escapeHtml(actionText)}</button>
  </article>`;
}

function eventHtml(item) {
  return `<article class="item" data-event-title="${escapeAttr(item.title || "")}" data-event-url="${escapeAttr(item.source_url || "")}" data-event-source-title="${escapeAttr(item.source_title || "")}">
    <div class="title">${escapeHtml(item.title)}</div>
    <div class="meta">${escapeHtml(item.category)} · ${item.article_count}篇 · 热度${item.hot_score}</div>
    <div class="summary">${escapeHtml((item.keywords || []).join(" / "))}</div>
  </article>`;
}

async function registerFromForm(form) {
  const formData = new FormData(form);
  const payload = {
    username: formData.get("username"),
    password: formData.get("password"),
    confirm_password: formData.get("confirm_password"),
    real_name: formData.get("real_name"),
    mobile: formData.get("mobile"),
  };
  const result = await request("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  saveSession(result);
  return result;
}

async function loadOnboardingOptions(formSelector) {
  const form = document.querySelector(formSelector);
  if (!form) return;
  const data = await request("/api/onboarding/options");
  const categories = form.querySelector("[data-category-options]");
  if (categories) {
    categories.innerHTML = data.categories
      .map((item) => {
        const checked = data.default_categories.includes(item.key) ? "checked" : "";
        const disabledLabel = item.implemented ? "" : "（后续）";
        return `<label><input type="checkbox" name="preferred_categories" value="${escapeAttr(item.key)}" ${checked} /> ${escapeHtml(item.name)}${disabledLabel}</label>`;
      })
      .join("");
  }
  const modelSelect = form.querySelector("[name=model_key]");
  if (modelSelect) {
    modelSelect.innerHTML = data.models
      .map((item) => `<option value="${escapeAttr(item.key)}">${escapeHtml(item.name)} · ${escapeHtml(item.provider_model)}</option>`)
      .join("");
    modelSelect.value = data.default_model;
  }
  const styleSelect = form.querySelector("[name=output_style]");
  if (styleSelect && data.output_styles) {
    styleSelect.innerHTML = data.output_styles.map((item) => `<option value="${escapeAttr(item.name)}">${escapeHtml(item.name)}</option>`).join("");
  }
}

async function loadProfileIntoForm(formSelector) {
  const form = document.querySelector(formSelector);
  if (!form || !activeUserId || activeUserId === "default") return null;
  const data = await request(`/api/profile?user_id=${encodeURIComponent(activeUserId)}`);
  const profile = data.profile || {};
  if (form.elements.display_name && data.user) form.elements.display_name.value = data.user.display_name || "";
  if (form.elements.self_description) form.elements.self_description.value = profile.self_description || "";
  if (form.elements.age) form.elements.age.value = profile.age || "";
  if (form.elements.gender) form.elements.gender.value = profile.gender || "不透露";
  if (form.elements.zodiac) form.elements.zodiac.value = profile.zodiac || "不透露";
  if (form.elements.watch_keywords) form.elements.watch_keywords.value = (profile.interests || []).join(", ");
  if (form.elements.negative_keywords) form.elements.negative_keywords.value = (profile.negative_interests || []).join(", ");
  if (form.elements.model_key && profile.model_key) form.elements.model_key.value = profile.model_key;
  if (form.elements.output_style && profile.output_style) form.elements.output_style.value = profile.output_style;
  form.querySelectorAll("[name=preferred_categories]").forEach((input) => {
    input.checked = (profile.preferred_categories || []).includes(input.value);
  });
  return data;
}

async function completeOnboardingFromForm(form) {
  const formData = new FormData(form);
  const payload = {
    user_id: activeUserId,
    display_name: formData.get("display_name") || null,
    self_description: formData.get("self_description") || "",
    age: formData.get("age") ? Number(formData.get("age")) : null,
    gender: formData.get("gender") || "不透露",
    zodiac: formData.get("zodiac") || "不透露",
    preferred_categories: formData.getAll("preferred_categories"),
    watch_keywords: splitKeywords(formData.get("watch_keywords")),
    negative_keywords: splitKeywords(formData.get("negative_keywords")),
    model_key: formData.get("model_key") || "yuanrong-personal-assistant",
    output_style: formData.get("output_style") || "简洁分析型",
  };
  const result = await request("/api/onboarding/complete", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (payload.display_name) {
    localStorage.setItem("pna_user_name", payload.display_name);
    renderUser();
  }
  return result;
}

function splitKeywords(value) {
  return String(value || "")
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function loadPrototypeFeeds() {
  const data = await request(`/api/feed?limit=8&user_id=${encodeURIComponent(activeUserId)}`);
  const items = data.items || [];
  renderPrototypeList("[data-story-feed]", items.slice(0, 4), "发酵");
  renderPrototypeList("[data-radar-feed]", items.slice(0, 5), "定位");
  renderPrototypeList("[data-brief-feed]", items.slice(0, 4), "播报");
  renderPrototypeList("[data-source-feed]", items.slice(0, 6), "引用");
}

function renderPrototypeList(selector, items, actionText) {
  document.querySelectorAll(selector).forEach((target) => {
    target.innerHTML = items.map((item) => prototypeItemHtml(item, actionText)).join("");
  });
}

async function loginFromForm(form) {
  const formData = new FormData(form);
  const result = await request("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username: formData.get("username"), password: formData.get("password") }),
  });
  saveSession(result);
  return result;
}

async function renderRealNameStatus(targetSelector) {
  const target = document.querySelector(targetSelector);
  if (!target) return;
  const status = await request("/api/auth/realname/status");
  target.textContent =
    status.provider === "mock"
      ? "实名手机认证：演示环境使用 mock 核验；正式环境需接入运营商三要素服务。"
      : `实名手机认证：${status.provider_name || status.provider}`;
}

async function renderWechatStatus(targetSelector) {
  const target = document.querySelector(targetSelector);
  if (!target) return;
  const status = await request("/api/auth/wechat/status");
  if (!status.configured) {
    target.textContent = "微信登录未配置：需要 WECHAT_APP_ID、WECHAT_APP_SECRET、WECHAT_REDIRECT_URI。";
    return;
  }
  const login = await request("/api/auth/wechat/login-url");
  target.innerHTML = `<a class="button-link" href="${escapeAttr(login.url)}">微信登录</a>`;
}

async function loadFeed(category = "", limit = 10, target = "#feed") {
  const data = await request(`/api/feed?limit=${limit}&user_id=${encodeURIComponent(activeUserId)}${category ? `&category=${category}` : ""}`);
  if (target === "#feed") {
    const count = document.querySelector("[data-related-article-count]");
    if (count) count.textContent = `${(data.items || []).length} 条`;
  }
  document.querySelector(target).innerHTML = data.items.map(itemHtml).join("");
}

async function loadEvents(target = "#events", category = "", limit = 8) {
  const data = await request(`/api/events?limit=${limit}${category ? `&category=${category}` : ""}`);
  document.querySelector(target).innerHTML = data.items.map(eventHtml).join("");
}

async function sendChat(message, target = "#messages") {
  const targetNode = document.querySelector(target) || document.querySelector("#messages");
  targetNode.classList.add("chat-stream");
  const userNode = chatTurn("user", message);
  targetNode.appendChild(userNode);
  const assistantNode = chatTurn("assistant", "", true);
  targetNode.appendChild(assistantNode);
  scrollChatTurnToTop(targetNode, userNode);
  return sendChatIntoTurn(message, assistantNode, target);
}

async function sendChatIntoTurn(message, assistantNode, target = "#messages") {
  const chatContext = window.currentChatContext || {};
  const targetNode = document.querySelector(target) || document.querySelector("#messages");
  targetNode.classList.add("chat-stream");
  const payload = {
    conversation_id: conversationId,
    user_id: activeUserId || "default",
    message,
    topic: chatContext.topic || null,
    category_scope: chatContext.category_scope || null,
    use_llm: Boolean(chatContext.use_llm),
    allow_web_search: Boolean(chatContext.allow_web_search),
  };
  try {
    const streamed = await streamChat(payload, assistantNode, targetNode);
    if (streamed) return streamed;
  } catch (error) {
    assistantNode.innerHTML = `<div class="trace-loading">流式连接中断，切换为普通请求...</div>`;
  }
  const data = await request("/api/chat", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  conversationId = data.conversation_id;
  localStorage.setItem("pna_conversation_id", conversationId);
  setAssistantResponseHtml(assistantNode, chatResponseHtml(data));
  syncChatResponseContext(data);
  await notifyConversationHistoryChanged();
  return data;
}

function focusFromChatMessage(message) {
  const text = String(message || "");
  const quoted =
    text.match(/围绕热点事件[“"《](.+?)[”"》]/) ||
    text.match(/基于资讯[“"《](.+?)[”"》]/);
  if (!quoted) return "";
  return cleanFocusTitle(quoted[1]);
}

function cleanFocusTitle(value) {
  let text = String(value || "").trim();
  text = text.replace(/^.*?相关热点[:：]/, "");
  text = text.replace(/^(围绕)?热点事件/, "");
  text = text.replace(/展开.*$/, "");
  text = text.replace(/[-—]+(中新网|人民网|新华网|央视网|中国新闻网|中国共产党新闻网)\s*$/, "");
  return text.trim().slice(0, 120);
}

async function notifyConversationHistoryChanged() {
  const refreshers = [window.refreshTopics].filter(
    (refresh) => typeof refresh === "function",
  );
  for (const refresh of refreshers) {
    try {
      await refresh();
    } catch (error) {
      // 回答已经保存，侧栏刷新失败不应影响本轮回答。
    }
  }
}

function chatMemoryKey() {
  const page = window.location.pathname || "/";
  return `pna_chat_memory:${activeUserId || "default"}:${page}`;
}

async function restoreChatMemory(target = "#messages") {
  if (!conversationId) return false;
  const targetNode = document.querySelector(target) || document.querySelector("#messages");
  if (!targetNode) return false;
  try {
    const data = await request(
      `/api/chat/conversations/${encodeURIComponent(conversationId)}?user_id=${encodeURIComponent(activeUserId || "default")}&limit=40`,
    );
    if (!data.turns?.length) return false;
    targetNode.innerHTML = "";
    targetNode.classList.add("chat-stream");
    data.turns.forEach((turn) => {
      targetNode.appendChild(chatTurn("user", turn.user_message || ""));
      const node = chatTurn("assistant", "");
      setAssistantResponseHtml(node, chatResponseHtml(turn.response || {
        conversation_id: conversationId,
        answer: turn.assistant_answer || "",
        context_relation: "restored_history",
      }));
      targetNode.appendChild(node);
    });
    localStorage.removeItem(chatMemoryKey());
    if (typeof window.applyChatConversationContext === "function") {
      window.applyChatConversationContext(data.context || {});
    }
    targetNode.scrollTop = targetNode.scrollHeight;
    return true;
  } catch (error) {
    return false;
  }
}

function syncChatResponseContext(response) {
  localStorage.removeItem(chatMemoryKey());
  if (isTopicChatResponse(response) && typeof window.applyChatConversationContext === "function") {
    window.applyChatConversationContext({
      topic: response?.topic || response?.focus_object?.text || null,
      category_scope: response?.category_scope || [],
    });
  }
  if (typeof window.handleChatResponseSideEffects === "function") {
    Promise.resolve(window.handleChatResponseSideEffects(response)).catch(() => {});
  }
}

function isTopicChatResponse(response) {
  if (!response) return false;
  if (response.context_relation === "query_moderation_blocked") return false;
  if (response.focus_object?.type === "topic") return Boolean(response.topic || response.focus_object?.text);
  return response.context_relation === "topic_agent_created";
}

function appendLocalTurn(role, text, target = "#messages", loading = false) {
  const targetNode = document.querySelector(target) || document.querySelector("#messages");
  targetNode.classList.add("chat-stream");
  const node = loading ? chatTurn("assistant", "", true) : chatTurn(role, text);
  targetNode.appendChild(node);
  if (role === "user") {
    scrollChatTurnToTop(targetNode, node);
  }
  return node;
}

function setAssistantTurnText(node, text) {
  if (!node) return;
  setAssistantResponseHtml(node, `<div class="assistant-markdown"><p>${escapeHtml(text)}</p></div>`);
}

function setAssistantResponseHtml(node, html) {
  if (!node) return;
  node.innerHTML = `${html}${turnActionsHtml("assistant")}`;
  syncForcedRelationButtons(node);
  mountRelatedMindMaps(node);
}

async function streamChat(payload, assistantNode, targetNode) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) return null;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const state = { research_trace: [], answer: "", stream_status: "连接已建立。" };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const event = parseSseEvent(part);
      if (!event) continue;
      if (event.type === "start") {
        conversationId = event.conversation_id || conversationId;
        if (conversationId) localStorage.setItem("pna_conversation_id", conversationId);
        state.stream_status = event.message || "开始处理。";
      } else if (event.type === "trace" && event.item) {
        state.research_trace.push(event.item);
        state.stream_status = event.item.message || event.item.stage || "执行中。";
      } else if (event.type === "final" && event.response) {
        conversationId = event.response.conversation_id || conversationId;
        if (conversationId) localStorage.setItem("pna_conversation_id", conversationId);
        setAssistantResponseHtml(assistantNode, chatResponseHtml(event.response));
        syncChatResponseContext(event.response);
        await notifyConversationHistoryChanged();
        return event.response;
      } else if (event.type === "error") {
        throw new Error(event.message || "流式请求失败");
      }
      assistantNode.innerHTML = chatStreamingHtml(state);
    }
  }
  return null;
}

function scrollChatTurnToTop(targetNode, turnNode) {
  if (!targetNode || !turnNode) return;
  requestAnimationFrame(() => {
    const targetRect = targetNode.getBoundingClientRect();
    const turnRect = turnNode.getBoundingClientRect();
    targetNode.scrollTop += turnRect.top - targetRect.top;
  });
}

function parseSseEvent(block) {
  const lines = block.split(/\r?\n/);
  let type = "message";
  let data = "";
  lines.forEach((line) => {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    if (line.startsWith("data:")) data += line.slice(5).trim();
  });
  if (!data) return { type };
  try {
    const parsed = JSON.parse(data);
    return { type, ...parsed };
  } catch (error) {
    return { type, message: data };
  }
}

function chatTurn(role, text, loading = false) {
  const wrapper = document.createElement("article");
  wrapper.className = `chat-turn ${role === "user" ? "chat-user" : "chat-assistant"}`;
  if (loading) {
    wrapper.innerHTML = `<div class="trace-loading">搜集线索中...</div>`;
  } else {
    wrapper.innerHTML = `<div class="chat-bubble">${escapeHtml(text)}</div>${turnActionsHtml(role)}`;
  }
  return wrapper;
}

function turnActionsHtml(role) {
  const edit = role === "user"
    ? '<button type="button" data-turn-action="edit" title="编辑并重新发送" aria-label="编辑并重新发送">✎</button>'
    : "";
  const relation = role === "assistant"
    ? '<button type="button" class="relation-toggle" data-turn-action="mark-related" title="强制标记为有关">有关</button><button type="button" class="relation-toggle" data-turn-action="mark-unrelated" title="强制标记为无关">无关</button>'
    : "";
  return `<div class="turn-actions" aria-label="消息操作">${edit}${relation}<button type="button" data-turn-action="copy" title="复制" aria-label="复制">⧉</button></div>`;
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-turn-action]");
  if (!button) return;
  const turn = button.closest(".chat-turn");
  if (!turn) return;
  const action = button.dataset.turnAction;
  if (action === "edit") {
    const input = document.querySelector("#message");
    const text = turn.querySelector(".chat-bubble")?.textContent?.trim() || "";
    if (!input || !text) return;
    input.value = text;
    input.focus();
    input.select?.();
    return;
  }
  if (action === "copy") {
    const text = turnCopyText(turn);
    if (!text) return;
    await copyTurnText(text);
    const original = button.textContent;
    button.textContent = "✓";
    button.setAttribute("aria-label", "已复制");
    window.setTimeout(() => {
      button.textContent = original;
      button.setAttribute("aria-label", "复制");
    }, 1200);
    return;
  }
  if (action === "mark-related") {
    await forceTurnRelation(turn, "related");
    return;
  }
  if (action === "mark-unrelated") {
    await forceTurnRelation(turn, "unrelated");
  }
});

async function forceTurnRelation(turn, relation) {
  if (!turn || !turn.classList.contains("chat-assistant")) return;
  const turnId = responseTurnId(turn);
  if (turnId) {
    try {
      const data = await request(`/api/chat/turns/${encodeURIComponent(turnId)}/relation`, {
        method: "POST",
        body: JSON.stringify({ user_id: activeUserId || "default", relation }),
      });
      if (data.response) {
        setAssistantResponseHtml(turn, chatResponseHtml(data.response));
        return;
      }
    } catch (error) {
      // 如果后端更新失败，继续使用本地显示切换，避免按钮无响应。
    }
  }
  turn.dataset.forcedRelation = relation;
  syncForcedRelationButtons(turn);
  if (relation === "related") {
    removeTurnDriftNotice(turn);
  } else {
    ensureTurnDriftNotice(turn);
  }
}

function ensureTurnDriftNotice(turn) {
  const markdown = turn.querySelector(".assistant-markdown");
  if (markdown) {
    if (turnHasDriftNotice(turn)) return;
    markdown.insertAdjacentHTML("afterbegin", `<blockquote class="manual-relation-notice">${escapeHtml(TOPIC_DRIFT_NOTICE)}</blockquote>`);
    return;
  }
  const bubble = turn.querySelector(".chat-bubble");
  if (!bubble || bubble.dataset.manualRelationNotice === "true") return;
  bubble.dataset.originalText = bubble.textContent || "";
  bubble.dataset.manualRelationNotice = "true";
  bubble.textContent = `${TOPIC_DRIFT_NOTICE}\n\n${bubble.dataset.originalText}`;
}

function removeTurnDriftNotice(turn) {
  turn.querySelectorAll("blockquote").forEach((node) => {
    if (TOPIC_DRIFT_NOTICES.some((notice) => node.textContent.includes(notice))) node.remove();
  });
  const bubble = turn.querySelector(".chat-bubble");
  if (bubble?.dataset.manualRelationNotice === "true") {
    bubble.textContent = bubble.dataset.originalText || "";
    delete bubble.dataset.manualRelationNotice;
    delete bubble.dataset.originalText;
  }
}

function turnHasDriftNotice(turn) {
  return Array.from(turn.querySelectorAll("blockquote")).some((node) =>
    TOPIC_DRIFT_NOTICES.some((notice) => node.textContent.includes(notice))
  );
}

function responseTurnId(turn) {
  return turn.querySelector("[data-response-turn-id]")?.dataset.responseTurnId || "";
}

function syncForcedRelationButtons(turn) {
  const relation = turn.dataset.forcedRelation || turn.querySelector("[data-response-turn-id]")?.dataset.forcedRelation || "";
  turn.querySelectorAll(".relation-toggle").forEach((button) => {
    button.classList.toggle("active", Boolean(relation) && button.dataset.turnAction === `mark-${relation}`);
  });
}

function turnCopyText(turn) {
  if (turn.classList.contains("chat-user")) {
    return turn.querySelector(".chat-bubble")?.textContent?.trim() || "";
  }
  return (
    turn.querySelector(".assistant-markdown")?.textContent?.trim()
    || turn.querySelector(".chat-bubble")?.textContent?.trim()
    || ""
  );
}

async function copyTurnText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (error) {
      // 非安全上下文中继续使用本地复制回退。
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function chatResponseHtml(data) {
  const trace = renderResearchTrace(data.research_trace || []);
  const mindMap = renderChatMindMapPlaceholder(data.mind_map);
  const isRelated = data.context_relation === "related_search" || data.mind_map?.type === "related_mind_map";
  const isFactcheck = data.skill_result?.command === "/factcheck";
  const answerText = isFactcheck
    ? stripFactcheckEvidenceMarkdown(data.markdown || data.answer || "")
    : isRelated
      ? stripRelatedGeneratedMarkdown(data.markdown || data.answer || "", true)
      : data.markdown || data.answer || "";
  const answer = renderMarkdown(answerText);
  const reportDownloads = renderReportDownloads(data);
  const evidenceIndex = isRelated ? renderChatEvidenceIndex(data.evidence || []) : "";
  const factcheckEvidence = isFactcheck ? renderFactcheckEvidenceCards(data.skill_result?.data?.evidence || []) : "";
  const timeline = renderChatEventLine(data.event_line, data.evidence || []);
  const responseMeta = data.turn_id
    ? `<span hidden data-response-turn-id="${escapeAttr(data.turn_id)}" data-forced-relation="${escapeAttr(data.forced_relation || "")}"></span>`
    : "";
  return `${responseMeta}${trace}${mindMap}<div class="assistant-markdown">${answer}</div>${reportDownloads}${evidenceIndex}${factcheckEvidence}${timeline}`;
}

function renderReportDownloads(data) {
  const result = data?.skill_result || {};
  const payload = result.data || {};
  const reportId = payload.report_id;
  if (result.command !== "/report" || !reportId) return "";
  const userId = activeUserId || "default";
  const base = `/api/reports/${encodeURIComponent(reportId)}/download?user_id=${encodeURIComponent(userId)}`;
  return `<div class="report-downloads" aria-label="报告下载">
    <a href="${base}&format=pdf" download>下载 PDF</a>
    <a href="${base}&format=docx" download>下载 Word</a>
  </div>`;
}

function chatStreamingHtml(state) {
  const trace = renderResearchTrace(state.research_trace || []);
  return `${trace}<div class="stream-status">${escapeHtml(state.stream_status || "执行中。")}</div>`;
}

function renderResearchTrace(items) {
  if (!items.length) return "";
  return `<div class="research-trace">${items
    .map((item) => {
      const count = Number.isFinite(Number(item.count)) ? Number(item.count) : "";
      return `<div class="trace-step ${escapeAttr(item.status || "")}"><strong>${escapeHtml(item.stage || "")}</strong><span>${escapeHtml(count)}</span><p>${escapeHtml(item.message || "")}</p></div>`;
    })
    .join("")}</div>`;
}

function renderChatEventLine(eventLine, evidenceItems = []) {
  const items = (eventLine && eventLine.items) || [];
  if (!items.length) return "";
  return `<div class="chat-event-line">${items
    .slice(0, 6)
    .map((item) => renderChatEvent(item, evidenceItems))
    .join("")}</div>`;
}

function renderChatEvent(item, evidenceItems) {
  const url = eventItemUrl(item, evidenceItems);
  const tag = url ? "a" : "div";
  const attrs = url ? ` href="${escapeAttr(url)}" target="_blank" rel="noreferrer"` : "";
  return `<${tag} class="chat-event"${attrs}><time>${escapeHtml(item.date || "")}</time><strong>${escapeHtml(item.title || "")}</strong><p>${escapeHtml(item.summary || "")}</p></${tag}>`;
}

function eventItemUrl(item, evidenceItems) {
  if (item?.url) return item.url;
  const sourceIds = new Set((item?.source_article_ids || []).filter(Boolean));
  if (sourceIds.size) {
    const match = evidenceItems.find((evidence) => evidence.article_id && sourceIds.has(evidence.article_id) && evidence.url);
    if (match) return match.url;
  }
  const title = String(item?.title || "").trim();
  if (!title) return "";
  const match = evidenceItems.find((evidence) => String(evidence.title || "").trim() === title && evidence.url);
  return match?.url || "";
}

function renderChatMindMapPlaceholder(map) {
  const branches = (map && map.branches) || [];
  if (!branches.length) return "";
  const id = `mind_map_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
  window.__pnaMindMapPayloads = window.__pnaMindMapPayloads || {};
  window.__pnaMindMapPayloads[id] = map;
  return `<div class="related-mind-map-host" data-related-map-id="${escapeAttr(id)}">${renderMindMapFallback(map)}</div>`;
}

function mountRelatedMindMaps(scope = document) {
  if (!window.React || !window.ReactDOM) return;
  const hosts = scope.querySelectorAll("[data-related-map-id]:not([data-react-mounted='true'])");
  hosts.forEach((host) => {
    const map = (window.__pnaMindMapPayloads || {})[host.dataset.relatedMapId];
    if (!map) return;
    host.dataset.reactMounted = "true";
    try {
      const root = ReactDOM.createRoot(host);
      root.render(React.createElement(RelatedMindMapExplorer, { map }));
    } catch (error) {
      host.innerHTML = renderMindMapFallback(map);
    }
  });
}

function RelatedMindMapExplorer({ map }) {
  const branches = React.useMemo(() => normalizeMindMapBranches(map), [map]);
  const [activeIndex, setActiveIndex] = React.useState(0);
  const activeBranch = branches[activeIndex] || branches[0];
  const layout = React.useMemo(() => buildExplorerLayout(branches), [branches]);
  const activePoints = (activeBranch?.points || []).slice(0, 5);

  return React.createElement(
    "section",
    { className: "related-explorer", "aria-label": "相关联想探索图" },
    React.createElement(
      "header",
      { className: "related-explorer-head" },
      React.createElement(
        "div",
        null,
        React.createElement("span", null, "Related Map"),
        React.createElement("strong", null, map.topic || "当前主题"),
        React.createElement("p", null, "先看从哪些关系出发，再看每条关系召回了哪些证据。")
      ),
      React.createElement(
        "div",
        { className: "related-explorer-stats" },
        React.createElement("span", null, `${branches.length} 个方向`),
        React.createElement("span", null, `${map.evidence_count || 0} 条证据`)
      )
    ),
    React.createElement(
      "div",
      { className: "related-explorer-body" },
      React.createElement(
        "div",
        { className: "related-explorer-map", style: { height: layout.height } },
        React.createElement(ExplorerLinks, { branches, layout, activeIndex }),
        React.createElement("div", { className: "explorer-stage-size", style: { width: layout.width, height: layout.height } }),
        React.createElement(ExplorerNode, {
          kind: "center",
          x: layout.center.x,
          y: layout.center.y,
          width: layout.center.width,
          height: layout.center.height,
          eyebrow: "中心主题",
          title: map.topic || "当前主题",
          detail: "联想搜索起点",
        }),
        branches.map((branch, index) =>
          React.createElement(ExplorerNode, {
            key: `branch_${index}`,
            kind: "branch",
            relationType: branch.relationType,
            active: index === activeIndex,
            x: layout.branches[index].x,
            y: layout.branches[index].y,
            width: layout.branches[index].width,
            height: layout.branches[index].height,
            eyebrow: "联想依据",
            detail: branch.edgeReason,
            onClick: () => setActiveIndex(index),
          })
        )
      ),
      React.createElement(
        "aside",
        { className: "related-explorer-detail", style: { "--relation-color": relationColor(activeBranch?.relationType) } },
        React.createElement("span", null, activeBranch?.relationLabel || "相关方向"),
        React.createElement("strong", null, activeBranch?.title || "相关方向"),
        React.createElement("p", null, activeBranch?.edgeReason || "从这个方向补充关联信息。"),
        React.createElement(
          "div",
          { className: "detail-evidence-list" },
          activePoints.length
            ? activePoints.map((point, index) =>
                React.createElement(
                  "a",
                  { key: `${point.title}_${index}`, href: point.url || "#", target: "_blank", rel: "noreferrer" },
                  React.createElement("span", null, point.evidence_index ? `#${point.evidence_index}` : `0${index + 1}`),
                  React.createElement("strong", null, evidenceTitle(point.title || "相关证据")),
                  React.createElement("small", null, evidenceSnippet(point.summary || point.connection_reason || "暂无摘要"))
                )
              )
            : React.createElement("p", { className: "detail-empty" }, "这个方向暂时没有召回证据。")
        )
      )
    )
  );
}

function ExplorerLinks({ branches, layout, activeIndex }) {
  return React.createElement(
    "svg",
    { className: "explorer-link-layer", viewBox: `0 0 ${layout.width} ${layout.height}`, "aria-hidden": "true" },
    React.createElement(
      "defs",
      null,
      React.createElement(
        "filter",
        { id: "explorerGlow", x: "-20%", y: "-20%", width: "140%", height: "140%" },
        React.createElement("feGaussianBlur", { stdDeviation: "3", result: "blur" }),
        React.createElement("feMerge", null, React.createElement("feMergeNode", { in: "blur" }), React.createElement("feMergeNode", { in: "SourceGraphic" }))
      )
    ),
    branches.map((branch, index) => {
      const branchPosition = layout.branches[index];
      const active = index === activeIndex;
      const color = relationColor(branch.relationType);
      const startX = layout.center.x + layout.center.width;
      const startY = layout.center.y + layout.center.height / 2;
      const endX = branchPosition.x;
      const endY = branchPosition.y + branchPosition.height / 2;
      return React.createElement(
        React.Fragment,
        { key: `links_${index}` },
        React.createElement("path", {
          className: "explorer-link-rail",
          d: curvePath(startX, startY, endX, endY),
        }),
        React.createElement("path", {
          className: `explorer-link ${active ? "active" : ""}`,
          d: curvePath(startX, startY, endX, endY),
          style: { "--relation-color": color },
          filter: active ? "url(#explorerGlow)" : "",
        })
      );
    })
  );
}

function ExplorerNode({ kind, relationType = "other", active = false, x, y, width, height, eyebrow, title, detail, onClick }) {
  const props = {
    role: onClick ? "button" : undefined,
    tabIndex: onClick ? 0 : undefined,
    className: `explorer-node ${kind} ${active ? "active" : ""}`,
    style: { left: x, top: y, width, minHeight: height, "--relation-color": relationColor(relationType) },
    onClick,
    onKeyDown: onClick
      ? (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onClick();
          }
        }
      : undefined,
  };
  const content = [];
  if (eyebrow) {
    content.push(React.createElement("span", { key: "eyebrow" }, eyebrow));
  }
  if (title) {
    content.push(React.createElement("strong", { key: "title" }, truncateText(title, 38)));
  }
  if (detail) {
    content.push(React.createElement("small", { key: "detail" }, truncateText(detail, 56)));
  }
  return React.createElement("div", props, content);
}

function normalizeMindMapBranches(map) {
  return ((map && map.branches) || []).map((branch) => ({
    ...branch,
    relationType: flowRelationType(branch.relation_type),
    relationLabel: branch.relation_label || "语义相关",
    edgeReason: branch.edge_reason || branch.reason || "从这个方向补充关联信息。",
    points: branch.points || [],
  }));
}

function buildExplorerLayout(branches) {
  const width = 720;
  const height = Math.max(360, branches.length * 88 + 52);
  const center = { x: 36, y: 0, width: 220, height: 130 };
  const branchWidth = 210;
  const branchHeight = 86;
  const branchX = 402;
  const branchYs = branchYPositions(branches.length, height, branchHeight);
  center.y = height / 2 - center.height / 2;
  const branchesLayout = [];
  branches.forEach((_, branchIndex) => {
    const xOffset = branchIndex % 2 === 0 ? 18 : 78;
    branchesLayout.push({ x: branchX + xOffset, y: branchYs[branchIndex], width: branchWidth, height: branchHeight });
  });
  return { width, height, center, branches: branchesLayout };
}

function branchYPositions(count, height, branchHeight) {
  if (count <= 1) return [height / 2 - branchHeight / 2];
  const top = 26;
  const bottom = height - branchHeight - 26;
  return Array.from({ length: count }, (_, index) => top + ((bottom - top) * index) / Math.max(1, count - 1));
}

function curvePath(startX, startY, endX, endY) {
  const distance = Math.max(80, Math.abs(endX - startX) * 0.48);
  return `M ${startX} ${startY} C ${startX + distance} ${startY}, ${endX - distance} ${endY}, ${endX} ${endY}`;
}

function relationColor(type) {
  const colors = {
    latest: "#2563eb",
    background: "#7c3aed",
    actor: "#0891b2",
    impact: "#c2410c",
    follow_up: "#15803d",
    center: "#111827",
    other: "#475467",
  };
  return colors[type] || colors.other;
}

function flowRelationType(value) {
  const type = String(value || "other").replace(/[^a-z_]/gi, "").toLowerCase();
  return ["latest", "background", "actor", "impact", "follow_up", "other"].includes(type) ? type : "other";
}

function renderMindMapFallback(map) {
  const branches = (map && map.branches) || [];
  return `<section class="related-explorer-fallback">
    <strong>${escapeHtml(map.topic || "相关联想图")}</strong>
      <p>${escapeHtml(branches.length)} 个方向 · ${escapeHtml(map.evidence_count || 0)} 条证据</p>
      <div>${branches
      .slice(0, 8)
      .map((branch) => `<span>${escapeHtml(branch.relation_label || "相关")}：${escapeHtml(truncateText(branch.title || "", 24))}</span>`)
      .join("")}</div>
  </section>`;
}

function cleanEvidenceText(value) {
  return String(value || "")
    .replace(/!\[[^\]]*](?:\([^)]*\))?/g, "")
    .replace(/\[([^\]]+)](?:\([^)]*\))?/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/#+\s*/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function evidenceTitle(value) {
  return truncateText(cleanEvidenceText(value), 34);
}

function evidenceSnippet(value) {
  return truncateText(
    cleanEvidenceText(value),
    66
  );
}

function stripRelatedGeneratedMarkdown(markdown, stripEvidenceIndex = false) {
  const lines = String(markdown || "").split(/\r?\n/);
  const output = [];
  let skippingSection = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (skippingSection && trimmed.startsWith("## ")) {
      skippingSection = false;
    }
    if (trimmed === "## 相关思维导图" || (stripEvidenceIndex && trimmed === "## 证据索引")) {
      skippingSection = true;
      continue;
    }
    if (!skippingSection) output.push(line);
  }
  return output.join("\n").trim();
}

function stripFactcheckEvidenceMarkdown(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const output = [];
  let skippingSection = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (skippingSection && trimmed.startsWith("### ")) {
      skippingSection = false;
    }
    if (trimmed.startsWith("### 检索到的全部证据")) {
      skippingSection = true;
      continue;
    }
    if (!skippingSection) output.push(line);
  }
  return output.join("\n").trim();
}

function renderFactcheckEvidenceCards(items) {
  if (!items.length) return "";
  return `<div class="chat-event-line factcheck-evidence-cards">${items
    .slice(0, 12)
    .map((item, index) => {
      const title = item.title || "未命名证据";
      const url = String(item.url || "").trim();
      const tag = url.startsWith("http") ? "a" : "div";
      const attrs = url.startsWith("http") ? ` href="${escapeAttr(url)}" target="_blank" rel="noreferrer"` : "";
      const date = item.published_at || "发布时间未知";
      const source = item.source_id || "来源未知";
      const summary = truncateText(cleanEvidenceText(item.summary || item.content_excerpt || ""), 180);
      return `<${tag} class="chat-event"${attrs}>
        <time>${escapeHtml(date)}</time>
        <strong>${escapeHtml(title)}</strong>
        <p>${escapeHtml(source)}${summary ? ` — ${escapeHtml(summary)}` : ""}${url ? " Read more" : ""}</p>
      </${tag}>`;
    })
    .join("")}</div>`;
}

function renderChatEvidenceIndex(items) {
  if (!items.length) return "";
  return `<div class="assistant-markdown related-evidence-index"><h3>证据索引</h3><ul>${items
    .map((item, index) => {
      const number = item.index || index + 1;
      const title = _markdownEvidenceLabel(`证据 ${number}｜${item.title || "未命名证据"}`);
      const source = item.source_id || "来源未知";
      const date = item.published_at || item.date || "发布时间未知";
      const summary = truncateText(cleanEvidenceText(item.summary || ""), 150);
      const url = String(item.url || "").trim();
      const titleHtml = url.startsWith("http")
        ? `<a href="${escapeAttr(url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>`
        : escapeHtml(title);
      return `<li>${titleHtml}（${escapeHtml(source)}，${escapeHtml(date)}）${summary ? `：${escapeHtml(summary)}` : ""}</li>`;
    })
    .join("")}</ul></div>`;
}

function _markdownEvidenceLabel(value) {
  return String(value || "").replace("[", "【").replace("]", "】").replace(/\s+/g, " ").trim();
}

function truncateText(value, maxLength) {
  const text = String(value || "").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  let html = "";
  let inList = false;
  const closeList = () => {
    if (inList) {
      html += "</ul>";
      inList = false;
    }
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      continue;
    }
    if (isMarkdownTableStart(lines, index)) {
      closeList();
      const table = renderMarkdownTable(lines, index);
      html += table.html;
      index = table.endIndex;
      continue;
    }
    if (trimmed.startsWith("### ")) {
      closeList();
      html += `<h4>${renderInlineMarkdown(trimmed.slice(4))}</h4>`;
    } else if (trimmed.startsWith("## ")) {
      closeList();
      html += `<h3>${renderInlineMarkdown(trimmed.slice(3))}</h3>`;
    } else if (trimmed.startsWith("# ")) {
      closeList();
      html += `<h3>${renderInlineMarkdown(trimmed.slice(2))}</h3>`;
    } else if (trimmed.startsWith("- ")) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${renderInlineMarkdown(trimmed.slice(2))}</li>`;
    } else if (trimmed.startsWith("> ")) {
      closeList();
      html += `<blockquote>${renderInlineMarkdown(trimmed.slice(2))}</blockquote>`;
    } else {
      closeList();
      html += `<p>${renderInlineMarkdown(trimmed)}</p>`;
    }
  }
  closeList();
  return html;
}

function isMarkdownTableStart(lines, index) {
  return splitMarkdownTableRow(lines[index]).length > 1 && isMarkdownTableSeparator(lines[index + 1]);
}

function isMarkdownTableSeparator(line) {
  const cells = splitMarkdownTableRow(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitMarkdownTableRow(line) {
  const trimmed = String(line || "").trim();
  if (!trimmed.includes("|")) return [];
  return trimmed.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function renderMarkdownTable(lines, startIndex) {
  const headers = splitMarkdownTableRow(lines[startIndex]);
  const rows = [];
  let index = startIndex + 2;
  while (index < lines.length) {
    const cells = splitMarkdownTableRow(lines[index]);
    if (!cells.length) break;
    rows.push(cells);
    index += 1;
  }
  const headerHtml = headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("");
  const bodyHtml = rows
    .map((row) => `<tr>${headers.map((_, columnIndex) => `<td>${renderInlineMarkdown(row[columnIndex] || "")}</td>`).join("")}</tr>`)
    .join("");
  return {
    html: `<div class="markdown-table-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`,
    endIndex: index - 1,
  };
}

function renderInlineMarkdown(value) {
  const text = String(value || "");
  const linkPattern = /\[([^\]]+)]\((https?:\/\/[^)\s]+)\)/g;
  let html = "";
  let lastIndex = 0;
  let match;
  while ((match = linkPattern.exec(text))) {
    html += renderInlinePlain(text.slice(lastIndex, match.index));
    html += `<a href="${escapeAttr(match[2])}" target="_blank" rel="noreferrer">${renderInlinePlain(match[1])}</a>`;
    lastIndex = linkPattern.lastIndex;
  }
  html += renderInlinePlain(text.slice(lastIndex));
  return html;
}

function renderInlinePlain(value) {
  return linkifyPlainUrls(escapeHtml(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>"));
}

function linkifyPlainUrls(value) {
  return String(value || "").replace(/https?:\/\/[^\s<]+/g, (url) => {
    const trailing = url.match(/[，。；、,.!?）)]$/)?.[0] || "";
    const cleanUrl = trailing ? url.slice(0, -trailing.length) : url;
    return `<a href="${escapeAttr(cleanUrl)}" target="_blank" rel="noreferrer">${cleanUrl}</a>${trailing}`;
  });
}

function bindAskButtons() {
  document.addEventListener("click", async (event) => {
    const commandButton = event.target.closest("[data-command]");
    if (commandButton) {
      event.preventDefault();
      const command = commandButton.dataset.command || "";
      const handler = window.handleAssistantInput;
      if (typeof handler === "function") {
        await handler(command);
        return;
      }
      await sendChat(command);
      return;
    }
    const button = event.target.closest("[data-ask]");
    if (!button) return;
    event.preventDefault();
    const ask = button.dataset.ask || button.textContent || "";
    const handler = window.handleAssistantInput;
    if (typeof handler === "function") {
      await handler(ask);
      return;
    }
    await sendChat(ask);
  });
}

const assistantSlashCommands = [
  {
    name: "factcheck",
    label: "事实核查",
    description: "核查关键说法，并区分事实、争议与未知",
    icon: "✓",
  },
  {
    name: "report",
    label: "生成专题报告",
    description: "汇总当前主题的进展、证据与关键结论",
    icon: "▤",
  },
  {
    name: "brief",
    label: "生成新闻简报",
    description: "把当前关注内容整理成一份快速简报",
    icon: "☀",
  },
  {
    name: "related",
    label: "查找相关新闻",
    description: "不输入内容则用当前主题；输入内容则查 /related 后面的内容",
    icon: "⌁",
  },
];

function bindSlashCommandMenu(inputSelector = "#message") {
  const input = document.querySelector(inputSelector);
  const form = input?.closest("form");
  if (!input || !form || form.querySelector(".slash-command-menu")) return;

  const menu = document.createElement("div");
  menu.className = "slash-command-menu";
  menu.hidden = true;
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-label", "新闻技能");
  form.appendChild(menu);

  let visibleCommands = [];
  let activeIndex = 0;

  const closeMenu = () => {
    menu.hidden = true;
    input.removeAttribute("aria-activedescendant");
  };

  const selectCommand = (command) => {
    input.value = `/${command.name} `;
    closeMenu();
    input.focus();
  };

  const renderMenu = () => {
    const value = input.value;
    if (!value.startsWith("/") || value.slice(1).includes(" ")) {
      closeMenu();
      return;
    }
    const query = value.slice(1).toLowerCase();
    visibleCommands = assistantSlashCommands.filter((command) =>
      `${command.name} ${command.label} ${command.description}`.toLowerCase().includes(query),
    );
    if (!visibleCommands.length) {
      closeMenu();
      return;
    }
    activeIndex = Math.min(activeIndex, visibleCommands.length - 1);
    menu.innerHTML = `
      <div class="slash-command-heading">技能</div>
      ${visibleCommands.map((command, index) => `
        <button type="button" id="slash-command-${command.name}" class="slash-command-item${index === activeIndex ? " active" : ""}" role="option" aria-selected="${index === activeIndex}" data-slash-command="${command.name}">
          <span class="slash-command-icon" aria-hidden="true">${command.icon}</span>
          <span class="slash-command-copy"><strong>/${command.name}</strong><span>${command.label}</span><small>${command.description}</small></span>
        </button>
      `).join("")}
    `;
    menu.hidden = false;
    input.setAttribute("aria-activedescendant", `slash-command-${visibleCommands[activeIndex].name}`);
  };

  input.addEventListener("input", () => {
    activeIndex = 0;
    renderMenu();
  });
  input.addEventListener("keydown", (event) => {
    if (menu.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      activeIndex = (activeIndex + direction + visibleCommands.length) % visibleCommands.length;
      renderMenu();
    } else if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      selectCommand(visibleCommands[activeIndex]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
    }
  });
  menu.addEventListener("mousedown", (event) => event.preventDefault());
  menu.addEventListener("click", (event) => {
    const button = event.target.closest("[data-slash-command]");
    const command = assistantSlashCommands.find((item) => item.name === button?.dataset.slashCommand);
    if (command) selectCommand(command);
  });
  input.addEventListener("blur", () => window.setTimeout(closeMenu, 120));
}

function parseAssistantCommand(message) {
  const value = String(message || "").trim();
  if (!value.startsWith("/")) return null;
  const tokens = shellLikeTokens(value.slice(1));
  const name = (tokens.shift() || "").toLowerCase();
  if (!name) return null;
  const args = { _: [] };
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token.startsWith("--")) {
      const inline = token.indexOf("=");
      if (inline > 2) {
        args[token.slice(2, inline)] = token.slice(inline + 1);
      } else {
        const key = token.slice(2);
        const next = tokens[index + 1];
        if (next && !next.startsWith("--")) {
          args[key] = next;
          index += 1;
        } else {
          args[key] = true;
        }
      }
    } else {
      args._.push(token);
    }
  }
  return { name, args, raw: value };
}

function shellLikeTokens(value) {
  const matches = String(value || "").match(/"[^"]*"|'[^']*'|\S+/g) || [];
  return matches.map((token) => {
    if ((token.startsWith('"') && token.endsWith('"')) || (token.startsWith("'") && token.endsWith("'"))) {
      return token.slice(1, -1);
    }
    return token;
  });
}

function commandText(command) {
  return (command?.args?._ || []).join(" ").trim();
}

function commandArg(command, ...keys) {
  for (const key of keys) {
    const value = command?.args?.[key];
    if (value !== undefined && value !== true) return String(value);
  }
  return "";
}

function commandScope(command, fallback = []) {
  const value = commandArg(command, "cat", "category", "categories", "scope");
  if (!value) return fallback;
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

async function runDueTaskUpdates(limit = 5) {
  if (!activeUserId || activeUserId === "default") return { ran_count: 0, items: [], notifications: [] };
  return request("/api/tasks/due/run", {
    method: "POST",
    body: JSON.stringify({ user_id: activeUserId, limit }),
  });
}

async function loadTaskNotifications(targetSelector = "[data-notifications]", options = {}) {
  if (!activeUserId || activeUserId === "default") return [];
  const unreadOnly = options.unreadOnly ? "true" : "false";
  const limit = options.limit || 10;
  const data = await request(`/api/notifications?user_id=${encodeURIComponent(activeUserId)}&unread_only=${unreadOnly}&limit=${limit}`);
  renderTaskNotifications(targetSelector, data.items || []);
  announceBrowserNotifications(data.items || []);
  return data.items || [];
}

function renderTaskNotifications(targetSelector, items) {
  const target = document.querySelector(targetSelector);
  if (!target) return;
  if (!items.length) {
    target.innerHTML = `<div class="empty-state compact-empty">暂无更新</div>`;
    return;
  }
  target.innerHTML = items
    .map(
      (item) => `<article class="${item.read_at ? "read" : "unread"}">
        <div>
          <strong>${escapeHtml(item.title)}</strong>
          <p>${escapeHtml(item.body || "")}</p>
          <span>${escapeHtml(notificationTimeLabel(item.created_at))}</span>
        </div>
        ${item.read_at ? "" : `<button type="button" data-notification-read="${escapeAttr(item.id)}" title="点此标为已读">未读</button>`}
      </article>`
    )
    .join("");
}

function bindNotificationReads(targetSelector = "[data-notifications]") {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-notification-read]");
    if (!button) return;
    event.preventDefault();
    await request(`/api/notifications/${encodeURIComponent(button.dataset.notificationRead)}/read`, {
      method: "POST",
      body: JSON.stringify({ user_id: activeUserId }),
    });
    await loadTaskNotifications(targetSelector);
  });
}

function startTaskPushPolling(targetSelector = "[data-notifications]", intervalMs = 60000) {
  const tick = async () => {
    if (!activeUserId || activeUserId === "default") return;
    try {
      await runDueTaskUpdates(5);
      await loadTaskNotifications(targetSelector, { limit: 8 });
    } catch (error) {
      // Keep polling quiet; visible panels still show explicit action errors.
    }
  };
  tick();
  return window.setInterval(tick, intervalMs);
}

async function enableBrowserNotifications(statusSelector = "[data-notification-status]") {
  const status = document.querySelector(statusSelector);
  if (!("Notification" in window)) {
    if (status) status.textContent = "当前浏览器不支持系统提醒。";
    return false;
  }
  const permission = await Notification.requestPermission();
  if (status) status.textContent = permission === "granted" ? "浏览器提醒已开启。" : "浏览器提醒未开启。";
  return permission === "granted";
}

function announceBrowserNotifications(items) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const key = `pna_notified_${activeUserId}`;
  const seen = new Set(JSON.parse(localStorage.getItem(key) || "[]"));
  const nextSeen = new Set(seen);
  items
    .filter((item) => !item.read_at && !seen.has(item.id))
    .slice(0, 3)
    .forEach((item) => {
      new Notification(item.title, { body: item.body || "" });
      nextSeen.add(item.id);
    });
  localStorage.setItem(key, JSON.stringify(Array.from(nextSeen).slice(-100)));
}

function notificationTimeLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

renderUser();
