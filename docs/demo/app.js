"use strict";

// This page reads public evidence only; it has no connection to a business API.
const setText = (id, value) => {
  if (value !== undefined && value !== null) document.getElementById(id).textContent = String(value);
};

function setMetric(id, count, suffix) {
  if (!Number.isFinite(count) || count < 0) return;
  const node = document.getElementById(id);
  const unit = document.createElement("span");
  unit.textContent = suffix;
  node.replaceChildren(document.createTextNode(String(count) + " "), unit);
  node.classList.remove("pending");
}

function evidenceUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value, window.location.href);
    const repository = "https://github.com/yaowcui-ai/aftersales-agent";
    if (url.origin === window.location.origin || url.href === repository || url.href.startsWith(repository + "/")) return url.href;
  } catch (_) { /* Invalid evidence URLs are omitted. */ }
  return null;
}

function renderMetrics(data) {
  const evaluation = data.evaluation || {};
  const complete = data.status === "complete" || evaluation.status === "complete";
  const passed = evaluation.passed;
  const total = evaluation.total;
  const reportUrl = evidenceUrl(evaluation.report_url);
  if (reportUrl) {
    const reportLink = document.getElementById("evaluation-report");
    reportLink.href = reportUrl;
    reportLink.hidden = false;
  }
  // Publish a rate only for a verified, complete 48 × 3 evaluation.
  if (complete && Number.isInteger(passed) && total === 144 && passed >= 0 && passed <= total) {
    setText("evaluation-value", `${passed} / ${total}`);
    document.getElementById("evaluation-value").classList.remove("pending");
    setText("evaluation-detail", `真实任务通过率 ${(100 * passed / total).toFixed(1)}% · 固定 48 案例 × 3 次`);
    setText("evaluation-tag", "完整评测 · 已记录失败");
  } else {
    setText("evaluation-value", complete ? "待核验指标" : "评测进行中");
    setText("evaluation-detail", data.detail || "新一轮真实评测进行中");
    setText("evaluation-tag", "结果待验证");
  }
  if (data.tests) {
    setMetric("tests-value", data.tests.passed, "项通过");
    setText("tests-detail", data.tests.detail);
  }
  if (data.browser) {
    setMetric("browser-value", data.browser.steps ?? data.browser.passed, "步骤通过");
    setText("browser-detail", data.browser.detail);
  }
  setText("metrics-status", data.updated_at ? `指标记录更新：${data.updated_at}。请结合下方原始验证记录核查。` : (data.detail || "公开指标已加载。"));
  if (Array.isArray(data.evidence)) {
    const container = document.getElementById("extra-evidence");
    for (const item of data.evidence) {
      const url = evidenceUrl(item.url);
      if (!url || typeof item.label !== "string") continue;
      const link = document.createElement("a");
      link.href = url;
      link.textContent = item.label + " ↗";
      container.append(link);
    }
    container.hidden = !container.childElementCount;
  }
}

fetch("data/metrics.json", { cache: "no-cache" })
  .then(response => {
    if (!response.ok) throw new Error("Metrics unavailable");
    return response.json();
  })
  .then(renderMetrics)
  .catch(() => setText("metrics-status", "本轮指标暂未加载。页面保留待验证状态，请通过 GitHub 查看验证记录。"));
