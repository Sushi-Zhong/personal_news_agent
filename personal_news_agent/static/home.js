(function () {
  const assetVersion = new URLSearchParams(window.location.search).get("v") || "20260707-16";
  const mobileQuery = window.matchMedia("(max-width: 760px)");
  const mode = mobileQuery.matches ? "mobile" : "web";
  const template = document.querySelector(`#${mode}Template`);

  if (!template) {
    document.body.textContent = "页面模板加载失败。";
    return;
  }

  document.body.className = template.dataset.bodyClass || "";
  document.documentElement.classList.toggle("mobile-root", mode === "mobile");
  document.body.appendChild(template.content.cloneNode(true));

  const sharedScript = document.createElement("script");
  sharedScript.src = `/static/shared.js?v=${encodeURIComponent(assetVersion)}`;
  sharedScript.onload = () => {
    const pageScript = document.createElement("script");
    pageScript.src = `/static/${mode}.js?v=${encodeURIComponent(assetVersion)}`;
    document.body.appendChild(pageScript);
  };
  document.body.appendChild(sharedScript);

  mobileQuery.addEventListener("change", () => {
    window.location.reload();
  });
})();
