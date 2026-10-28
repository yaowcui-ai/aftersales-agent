import { expect, test, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import type { Task, Ticket } from "../src/types";

test('saved Agent history restores after refresh and scrolls to its final answer', async ({ page }) => {
  test.skip(!process.env.E2E_RESTORE_THREAD || !process.env.E2E_CUSTOMER_PASSWORD, 'Requires existing local sandbox task. No model calls.');
  await login(page, 'customer');
  await page.getByRole('button', { name: '智能售后', exact: true }).click();
  await page.getByLabel('按原任务编号恢复').fill(process.env.E2E_RESTORE_THREAD!);
  await page.getByRole('button', { name: '读取任务', exact: true }).click();
  const messages = page.locator('.messages');
  await expect(messages).toContainText(process.env.E2E_RESTORE_CASE!);
  await expect.poll(() => messages.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(5);
  await page.reload();
  await expect(page.getByRole('heading', { name: '智能售后', exact: true })).toBeVisible();
  await expect(messages).toContainText(process.env.E2E_RESTORE_CASE!);
  await expect.poll(() => messages.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(5);
  await page.screenshot({ path: 'test-results/customer-history-final-answer.png', fullPage: true });
});

test("login is accessible and remains usable at mobile widths", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "登录服务工作台" }),
  ).toBeVisible();
  await expect(page.getByLabel("用户名")).toBeVisible();
  await expect(page.getByLabel("密码", { exact: true })).toHaveAttribute(
    "type",
    "password",
  );
  await page.getByRole("button", { name: "管理员审批", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "管理员审批", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({
    path: "test-results/login-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "登录工作台" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/login-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

async function login(page: Page, role: "customer" | "approver") {
  await page.goto("/");
  if (role === "approver")
    await page.getByRole("button", { name: "管理员审批", exact: true }).click();
  await page
    .getByLabel("用户名")
    .fill(
      process.env[
        role === "customer" ? "E2E_CUSTOMER_USERNAME" : "E2E_ADMIN_USERNAME"
      ]!,
    );
  await page
    .getByLabel("密码", { exact: true })
    .fill(
      process.env[
        role === "customer" ? "E2E_CUSTOMER_PASSWORD" : "E2E_ADMIN_PASSWORD"
      ]!,
    );
  await page.getByRole("button", { name: "登录工作台" }).click();
}

test("customer uses real HTTP for orders, shipments and saved tasks", async ({
  page,
}) => {
  test.skip(
    !process.env.E2E_CUSTOMER_USERNAME || !process.env.E2E_CUSTOMER_PASSWORD,
    "Set dedicated sandbox account environment variables for real API verification.",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page, "customer");
  await expect(page.getByRole("heading", { name: "我的订单" })).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: "订单 / 商品" }),
  ).toBeVisible();
  const details = page.getByRole("button", { name: "物流详情", exact: true });
  if (await details.count()) {
    await details.first().click();
    await expect(page.getByRole("heading", { name: "物流记录" })).toBeVisible();
  }
  await page.screenshot({
    path: "test-results/customer-orders.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "智能售后", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "智能售后", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "历史对话" })).toBeVisible();
  await page.getByRole("button", { name: "申请与工单", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "售后申请", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => localStorage.getItem("aftersales.session")),
  ).toBeNull();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/customer-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(
    page.getByRole("heading", { name: "登录服务工作台" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("administrator sees API overview and approval reasons", async ({
  page,
}) => {
  test.skip(
    !process.env.E2E_ADMIN_USERNAME || !process.env.E2E_ADMIN_PASSWORD,
    "Set dedicated sandbox admin environment variables for real API verification.",
  );
  await login(page, "approver");
  await expect(
    page.getByRole("heading", { name: "服务运营总览" }),
  ).toBeVisible();
  await expect(page.locator(".stat-card")).toHaveCount(4);
  await page.screenshot({
    path: "test-results/admin-overview.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "申请审批", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "申请审批", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toHaveCount(0);
  const requests = page.locator(".approval-item");
  if (await requests.count()) {
    await requests.first().click();
    await expect(page.getByLabel("审批理由（必填）")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "批准并继续处理" }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "读取原任务与审计" }).click();
    await expect(
      page.getByRole("heading", { name: "业务审计轨迹" }),
    ).toBeVisible();
  }
});

test("sandbox form approvals and return receiving preserve the original action", async ({
  browser,
}) => {
  test.setTimeout(240000);
  test.skip(
    process.env.E2E_WRITE_FLOW !== "1",
    "Enable explicitly for isolated sandbox writes; up to two model reply calls.",
  );
  test.skip(
    !process.env.E2E_CUSTOMER_PASSWORD || !process.env.E2E_ADMIN_PASSWORD,
    "Dedicated sandbox credentials required.",
  );
  const customerContext = await browser.newContext();
  const adminContext = await browser.newContext();
  const customer = await customerContext.newPage();
  const admin = await adminContext.newPage();
  const summary: Record<string, unknown>[] = [];
  await login(customer, "customer");
  await login(admin, "approver");
  await expect(
    customer.getByRole("heading", { name: "我的订单" }),
  ).toBeVisible();
  await expect(admin.locator(".stat-card")).toHaveCount(4);
  for (const [kind, order] of [
    ["refund", "UP-PAID"],
    ["return_refund", "UP-SHIPPED"],
  ]) {
    const existing = await admin.evaluate(async (wantedKind) => {
      const session = JSON.parse(sessionStorage.getItem("aftersales.session")!);
      const response = await fetch("/api/approvals", {
        headers: { Authorization: `Bearer ${session.token}` },
      });
      const records = (await response.json()) as Ticket[];
      return records.find(
        (item) =>
          item.description.startsWith(`Edge-${wantedKind}-`) &&
          ["pending", "waiting_approval"].includes(item.status),
      );
    }, kind);
    const marker =
      existing?.description.split("：")[0] || `Edge-${kind}-${Date.now()}`;
    if (existing)
      await customer.evaluate(
        ({ thread }) => {
          sessionStorage.setItem("aftersales.request.customer1", thread);
          sessionStorage.setItem("aftersales.request.customer1.thread", thread);
        },
        { thread: existing.thread_id },
      );
    await customer
      .getByRole("button", { name: "提交申请", exact: true })
      .click();
    let created: Task;
    if (existing) {
      const restore = customer.waitForResponse((response) =>
        response.url().endsWith(`/api/tasks/${existing.thread_id}`),
      );
      await customer
        .getByRole("button", { name: "读取已保存状态", exact: true })
        .click();
      created = (await (await restore).json()) as Task;
    } else {
      const another = customer.getByRole("button", {
        name: "另建申请",
        exact: true,
      });
      if (await another.count()) await another.click();
      await customer.getByLabel("订单编号", { exact: true }).fill(order);
      await customer.getByLabel("服务类型").selectOption(kind);
      await customer.getByLabel("申请退款金额（元）").fill("1.00");
      await customer
        .getByLabel("问题描述")
        .fill(`${marker}：独立沙箱浏览器验收，仅模拟退款1元。`);
      const submitResponse = customer.waitForResponse(
        (response) =>
          response.url().endsWith("/api/requests") &&
          response.request().method() === "POST",
      );
      await customer
        .getByRole("button", { name: "提交申请 →", exact: true })
        .click();
      const submitted = await submitResponse;
      expect(submitted.status()).toBe(200);
      created = (await submitted.json()) as Task;
    }
    await mkdir("test-results", { recursive: true });
    await writeFile(
      `test-results/${kind}-submitted.json`,
      JSON.stringify(created, null, 2),
    );
    expect(created.proposal?.action_id).toBeTruthy();
    expect(["pending", "waiting_approval"]).toContain(created.proposal?.status);
    await expect(
      customer.getByRole("heading", { name: "已保存任务" }),
    ).toBeVisible();
    await customer.screenshot({
      path: `test-results/${kind}-customer-pending.png`,
      fullPage: true,
    });
    console.log(
      `Inspecting original ${kind} sandbox request ${created.thread_id}`,
    );
    await admin.getByRole("button", { name: "申请审批", exact: true }).click();
    console.log("Approval workspace opened");
    await admin
      .getByRole("button", { name: "读取已保存状态", exact: true })
      .click();
    console.log("Approval list refreshed");
    await admin.locator(".approval-item").filter({ hasText: marker }).click();
    console.log("Original request selected");
    await admin
      .getByLabel("审批理由（必填）")
      .fill("已核对订单归属、冻结金额与沙箱验收范围，批准模拟1元退款。");
    const approvalResponse = admin.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/api/approvals/${created.proposal!.action_id}/decision`) &&
        response.request().method() === "POST",
      { timeout: 180000 },
    );
    await admin.getByRole("button", { name: "批准并继续处理" }).click();
    const decided = await approvalResponse;
    if (kind === "return_refund") {
      expect(decided.status()).toBe(200);
      await expect(
        admin.getByRole("button", { name: "确认退货已收到并模拟退款" }),
      ).toBeVisible();
      await admin.screenshot({
        path: "test-results/return-admin-awaiting-receipt.png",
        fullPage: true,
      });
      const receiveResponse = admin.waitForResponse(
        (response) =>
          response
            .url()
            .includes(`/api/returns/${created.proposal!.action_id}/receive`) &&
          response.request().method() === "POST",
        { timeout: 180000 },
      );
      await admin
        .getByRole("button", { name: "确认退货已收到并模拟退款" })
        .click();
      const received = await receiveResponse;
      summary.push({
        phase: "receive_http",
        kind,
        status: received.status(),
        response: await received.json(),
      });
    } else
      summary.push({
        phase: "approve_http",
        kind,
        status: decided.status(),
        response: await decided.json(),
      });
    await expect(admin.getByRole("status")).toHaveCount(0, { timeout: 180000 });
    await admin.getByRole("button", { name: "读取原任务与审计" }).click();
    await expect(
      admin.getByRole("heading", { name: "业务审计轨迹" }),
    ).toBeVisible();
    const savedResponse = customer.waitForResponse((response) =>
      response.url().endsWith(`/api/tasks/${created.thread_id}`),
    );
    await customer
      .getByRole("button", { name: "读取已保存状态", exact: true })
      .click();
    const saved = (await (await savedResponse).json()) as Task;
    expect(saved.proposal?.action_id).toBe(created.proposal?.action_id);
    expect(saved.proposal?.status).toBe("completed");
    expect(saved.proposal?.result?.amount).toBe("1.00");
    expect(saved.proposal?.result?.refund_id).toBeTruthy();
    summary.push({
      phase: "customer_saved",
      kind,
      request_id: saved.thread_id,
      status: saved.status,
      action: saved.proposal,
    });
    await customer.screenshot({
      path: `test-results/${kind}-customer-completed.png`,
      fullPage: true,
    });
    await admin.screenshot({
      path: `test-results/${kind}-admin-audit.png`,
      fullPage: true,
    });
  }
  await mkdir("test-results", { recursive: true });
  await writeFile(
    "test-results/real-write-flow.json",
    JSON.stringify(
      { at: new Date().toISOString(), business: "simulated", summary },
      null,
      2,
    ),
  );
  await customerContext.close();
  await adminContext.close();
});
