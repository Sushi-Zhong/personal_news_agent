(function () {
  const assetVersion = new URLSearchParams(window.location.search).get("v") || "20260812-newsroom-8";
  const mobileQuery = window.matchMedia("(max-width: 1024px)");
  const mode = mobileQuery.matches ? "mobile" : "web";
  const template = document.querySelector(`#${mode}Template`);

  if (!window.React || !window.ReactDOM) {
    document.body.textContent = "React 运行时加载失败。";
    return;
  }

  if (!template) {
    document.body.textContent = "页面模板加载失败。";
    return;
  }

  document.body.className = template.dataset.bodyClass || "";
  document.documentElement.classList.toggle("mobile-root", mode === "mobile");

  const themePreferenceKey = "pna.console.theme";

  function applyTheme(theme) {
    const isLight = theme === "light";
    document.body.classList.toggle("console-light", isLight);
    document.body.classList.toggle("console-dark", !isLight);
    const button = document.querySelector("#themeToggle");
    if (button) {
      button.textContent = isLight ? "深色" : "浅色";
      button.setAttribute("aria-pressed", String(isLight));
      button.setAttribute("aria-label", isLight ? "切换深色背景" : "切换浅色背景");
    }
  }

  function savedTheme() {
    try {
      const storedTheme = localStorage.getItem(themePreferenceKey);
      return storedTheme === "dark" ? "dark" : "light";
    } catch (error) {
      return "light";
    }
  }

  applyTheme(savedTheme());

  const rootNode = document.createElement("div");
  rootNode.id = "react-root";
  document.body.appendChild(rootNode);

  function loadPageScripts() {
    const sharedScript = document.createElement("script");
    sharedScript.src = `static/shared.js?v=${encodeURIComponent(assetVersion)}`;
    sharedScript.onload = () => {
      const pageScript = document.createElement("script");
      pageScript.src = `static/${mode}.js?v=${encodeURIComponent(assetVersion)}`;
      document.body.appendChild(pageScript);
    };
    document.body.appendChild(sharedScript);
  }

  function HomeShell() {
    React.useEffect(() => {
      applyTheme(savedTheme());
      const button = document.querySelector("#themeToggle");
      button?.addEventListener("click", () => {
        const nextTheme = document.body.classList.contains("console-light") ? "dark" : "light";
        try {
          localStorage.setItem(themePreferenceKey, nextTheme);
        } catch (error) {
          // Storage can be unavailable in private or embedded browser contexts.
        }
        applyTheme(nextTheme);
      });
      loadPageScripts();
    }, []);

    return React.createElement("div", {
      className: `react-home-shell react-home-shell-${mode}`,
      dangerouslySetInnerHTML: { __html: template.innerHTML },
    });
  }

  ReactDOM.createRoot(rootNode).render(React.createElement(HomeShell));

  mobileQuery.addEventListener("change", () => {
    window.location.reload();
  });
})();
