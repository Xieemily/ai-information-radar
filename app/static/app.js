document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector("[data-nav-toggle]");
  const sidebar = document.querySelector(".sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", () => {
      const open = sidebar.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "关闭" : "菜单";
    });
  }

  const savedTheme = localStorage.getItem("radar-theme");
  const preferredTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = savedTheme || preferredTheme;

  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("radar-theme", next);
    });
  });

  document.querySelectorAll("time[data-relative-time]").forEach((element) => {
    const parsed = new Date(element.textContent.trim().replace(" ", "T") + "Z");
    if (Number.isNaN(parsed.getTime())) return;
    const minutes = Math.round((Date.now() - parsed.getTime()) / 60000);
    let label;
    if (minutes < 1) label = "刚刚";
    else if (minutes < 60) label = `${minutes} 分钟前`;
    else if (minutes < 1440) label = `${Math.floor(minutes / 60)} 小时前`;
    else if (minutes < 10080) label = `${Math.floor(minutes / 1440)} 天前`;
    else label = new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(parsed);
    element.title = element.textContent.trim();
    element.textContent = label;
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
    const search = document.querySelector("input[type='search']");
    if (search) {
      event.preventDefault();
      search.focus();
    } else {
      window.location.href = "/items";
    }
  });
});

document.addEventListener("htmx:beforeRequest", (event) => {
  const button = event.target.querySelector?.("button[type='submit']");
  if (button) button.dataset.originalText = button.textContent;
  if (button) button.textContent = "处理中…";
});

document.addEventListener("htmx:afterRequest", (event) => {
  const button = event.target.querySelector?.("button[type='submit']");
  if (button?.dataset.originalText) button.textContent = button.dataset.originalText;
});

document.addEventListener("radarCounts", (event) => {
  Object.entries(event.detail || {}).forEach(([state, count]) => {
    const element = document.querySelector(`[data-state-count="${state}"]`);
    if (element) element.textContent = count;
  });
});
