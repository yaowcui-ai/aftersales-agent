import { expect, test, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

test("Streamlit saved conversations survive reload, relogin and account switching", async ({ page, request }) => {
  test.skip(process.env.E2E_STREAMLIT_HISTORY !== "1", "Dedicated local Streamlit verification only");
  test.setTimeout(180000);
  const address = process.env.E2E_BASE_URL!;
  expect(new URL(address).hostname).toMatch(/^(127\.0\.0\.1|localhost)$/);
  const api = "http://127.0.0.1:8090";
  async function login(name = "customer1") {
    await expect(page.getByRole("heading", { name: "登录", exact: true })
      .or(page.getByRole("heading", { name: "我的售后对话", exact: true }))).toBeVisible();
    if (await page.getByRole("heading", { name: "登录", exact: true }).isVisible()) {
      await page.getByLabel("用户名", { exact: true }).fill(name);
      await page.getByLabel("密码", { exact: true }).fill(process.env[name === "customer1" ? "E2E_CUSTOMER_PASSWORD" : "E2E_OTHER_PASSWORD"]!);
      await page.getByRole("button", { name: "登录", exact: true }).click();
    }
    await expect(page.getByRole("heading", { name: "我的售后对话", exact: true })).toBeVisible();
  }
  const auth = await request.post(`${api}/auth/login`, {
    data: { username: "customer1", password: process.env.E2E_CUSTOMER_PASSWORD },
  });
  expect(auth.status()).toBe(200);
  const headers = { Authorization: `Bearer ${(await auth.json()).token}` };
  await page.goto(address);
  await login();
  const marker = `HISTORY_REFRESH_${Date.now()}`;
  const beforeList = await request.get(`${api}/conversations`, { headers });
  expect(beforeList.status()).toBe(200);
  if (process.env.E2E_STREAMLIT_WRITE === "1") {
    await page.getByRole("button", { name: "新建对话", exact: true }).click();
    const input = page.getByPlaceholder("描述您的售后问题");
    await input.fill(`这是只读测试，请只回复 ${marker}，不要调用工具。`);
    await input.press("Enter");
  }
  await expect.poll(() => new URL(page.url()).searchParams.get("conversation")).toBeTruthy();
  const thread = new URL(page.url()).searchParams.get("conversation")!;
  // The initial checkpoint already contains the human turn. Wait for the saved
  // model reply, rather than mistaking that human bubble for a completed run.
  if (process.env.E2E_STREAMLIT_WRITE === "1") {
    await expect.poll(async () => {
      const result = await request.get(`${api}/tasks/${thread}`, { headers });
      return result.status() === 200 ? (await result.json()).final_answer || "" : "";
    }, { timeout: 90000 }).toContain(marker);
    await expect(page.getByTestId("stChatMessage")).toHaveCount(2);
  }
  const task = await (await request.get(`${api}/tasks/${thread}`, { headers })).json();
  if (process.env.E2E_STREAMLIT_WRITE === "1") {
    expect(task.metrics.model_calls).toBe(1);
    expect(task.proposal).toBeFalsy();
  }
  await page.reload();
  await login();
  await expect.poll(() => new URL(page.url()).searchParams.get("conversation")).toBe(thread);
  const savedMessage = task.messages.find((m: { type: string }) => m.type === "human").content;
  await expect(page.getByTestId("stChatMessage").first()).toContainText(savedMessage);
  const restored = await (await request.get(`${api}/tasks/${thread}`, { headers })).json();
  expect(restored.messages).toEqual(task.messages);
  expect(restored.metrics.model_calls).toBe(task.metrics.model_calls);
  const history = await (await request.get(`${api}/conversations`, { headers })).json();
  expect(history.filter((row: { thread_id: string }) => row.thread_id === thread)).toHaveLength(1);
  await mkdir("test-results/streamlit-history", { recursive: true });
  await page.screenshot({ path: "test-results/streamlit-history/restored.png", fullPage: true });
  const older = history.find((row: { thread_id: string }) => row.thread_id !== thread);
  if (older) {
    await page.goto(`${address}?conversation=${encodeURIComponent(older.thread_id)}`);
    await login();
    await expect(page.getByTestId("stChatMessage").first()).toContainText(older.title);
    await page.reload();
    await login();
    await expect(page.getByTestId("stChatMessage").first()).toContainText(older.title);
  }
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("conversation")).toBeNull();
  await login("customer2");
  await expect(page.getByTestId("stSidebar")).not.toContainText(marker);
  const other = await request.post(`${api}/auth/login`, {
    data: { username: "customer2", password: process.env.E2E_OTHER_PASSWORD },
  });
  const otherHistory = await request.get(`${api}/conversations?customer_id=learner-001`, {
    headers: { Authorization: `Bearer ${(await other.json()).token}` },
  });
  expect((await otherHistory.json()).some((row: { thread_id: string }) => row.thread_id === thread)).toBeFalsy();
  await writeFile("test-results/streamlit-history/report.json", JSON.stringify({
    success: true, thread_id: thread, history_count: history.length,
    messages_before: task.messages.length, messages_after: restored.messages.length,
    model_calls_before: task.metrics.model_calls, model_calls_after: restored.metrics.model_calls,
    real_model_calls_this_test: process.env.E2E_STREAMLIT_WRITE === "1" ? 1 : 0,
    total_tokens_this_test: process.env.E2E_STREAMLIT_WRITE === "1" ? task.metrics.total_tokens : 0,
    older_conversation_restored: !!older, cross_customer_blocked: true,
  }, null, 2));
});
