import { test, expect, type Page } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

test('real Agent clarifies, prepares, then admin approves and customer restores result', async ({ browser }) => {
  test.skip(process.env.E2E_AGENT_WRITE !== '1', 'Explicit bounded real-model sandbox run only.');
  test.setTimeout(240000);
  const customerContext = await browser.newContext();
  const adminContext = await browser.newContext();
  const customer = await customerContext.newPage();
  const admin = await adminContext.newPage();
  const report: Record<string, unknown> = { success: false, model: 'real configured provider', steps: [] };
  const steps: unknown[] = [];
  await mkdir('test-results/agent-chat', { recursive: true });
  async function login(page: Page, role: 'customer' | 'admin') {
    await page.goto('/');
    if (role === 'admin') await page.getByRole('button', { name: '管理员审批', exact: true }).click();
    await page.getByLabel('用户名').fill(process.env[role === 'admin' ? 'E2E_ADMIN_USERNAME' : 'E2E_CUSTOMER_USERNAME']!);
    await page.getByLabel('密码', { exact: true }).fill(process.env[role === 'admin' ? 'E2E_ADMIN_PASSWORD' : 'E2E_CUSTOMER_PASSWORD']!);
    await page.getByRole('button', { name: '登录工作台 →', exact: true }).click();
    await expect(page.getByRole('heading', { name: role === 'admin' ? '服务运营总览' : '我的订单', exact: true })).toBeVisible();
  }
  async function send(message: string) {
    await customer.getByLabel('售后消息', { exact: true }).fill(message);
    const response = customer.waitForResponse(r => r.url().endsWith('/messages') && r.request().method() === 'POST', { timeout: 150000 });
    await customer.getByRole('button', { name: '发送消息 ↑', exact: true }).click();
    const result = await response;
    expect(result.status()).toBe(200);
    return result.json();
  }
  try {
    await login(customer, 'customer');
    await customer.getByRole('button', { name: '智能售后', exact: true }).click();
    const clarified = await send('我想申请退款，请帮我处理。');
    expect(clarified.proposal).toBeFalsy();
    expect(clarified.final_answer).toMatch(/订单|编号/);
    steps.push({ phase: 'missing_order_clarification', task: clarified });
    const proposal = await send('订单号UP-PAID，我要申请沙箱模拟退款0.20元，原因是不需要商品了。请先核对订单，再提出申请，等待系统人工审批。');
    expect(proposal.status).toBe('waiting_approval');
    expect(proposal.proposal.order_id).toBe('UP-PAID');
    expect(proposal.proposal.kind).toBe('refund');
    expect(proposal.proposal.amount).toBe('0.20');
    const calls = proposal.messages.flatMap((m: { tool_calls?: unknown[] }) => m.tool_calls || []);
    expect(calls.some((c: { name: string }) => c.name === 'get_order')).toBeTruthy();
    expect(calls.some((c: { name: string }) => c.name === 'prepare_action')).toBeTruthy();
    steps.push({ phase: 'real_model_proposal', task: proposal });
    await customer.screenshot({ path: 'test-results/agent-chat/customer-pending.png', fullPage: true });
    await login(admin, 'admin');
    await admin.getByRole('button', { name: '申请审批', exact: true }).click();
    await admin.locator('.approval-item').filter({ hasText: proposal.proposal.description }).first().click();
    await admin.getByLabel('审批理由（必填）').fill('核对真实Agent申请、订单归属及冻结0.20元金额，同意沙箱执行。');
    const decision = admin.waitForResponse(r => r.url().includes(`/approvals/${proposal.proposal.action_id}/decision`) && r.request().method() === 'POST', { timeout: 150000 });
    await admin.getByRole('button', { name: '批准并继续处理', exact: true }).click();
    const response = await decision;
    expect(response.status()).toBe(200);
    const completed = await response.json();
    expect(completed.status).toBe('completed');
    expect(completed.proposal.result.refund_id).toBeTruthy();
    expect(completed.proposal.result.amount).toBe('0.20');
    steps.push({ phase: 'approved_business_result', task: completed });
    await admin.screenshot({ path: 'test-results/agent-chat/admin-result.png', fullPage: true });
    await customer.getByRole('button', { name: '读取已保存状态', exact: true }).click();
    await expect(customer.getByText(completed.proposal.result.case_id, { exact: false }).first()).toBeVisible();
    await customer.reload();
    await expect(customer.getByText(completed.proposal.result.case_id, { exact: false }).first()).toBeVisible();
    await customer.screenshot({ path: 'test-results/agent-chat/customer-restored.png', fullPage: true });
    report.success = true;
    report.thread_id = completed.thread_id;
    report.action_id = completed.proposal.action_id;
  } finally {
    report.steps = steps;
    await writeFile('test-results/agent-chat/report.json', JSON.stringify(report, null, 2));
    await customerContext.close();
    await adminContext.close();
  }
});
