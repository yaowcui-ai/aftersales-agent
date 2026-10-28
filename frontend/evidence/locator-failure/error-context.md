# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: platform.spec.ts >> sandbox form approvals and return receiving preserve the original action
- Location: e2e\platform.spec.ts:149:1

# Error details

```
TimeoutError: locator.click: Timeout 15000ms exceeded.
Call log:
  - waiting for getByRole('button', { name: '读取已保存状态', exact: true })

```

# Page snapshot

```yaml
- generic [ref=e3]:
  - complementary [ref=e4]:
    - link "循 循服 AFTERCARE" [ref=e5] [cursor=pointer]:
      - /url: "#"
      - generic [ref=e6]: 循
      - text: 循服
      - generic [ref=e7]: AFTERCARE
    - paragraph [ref=e8]: WORKSPACE / 客户服务
    - navigation "客户导航" [ref=e9]:
      - button "我的订单" [ref=e10] [cursor=pointer]:
        - generic [aria-hidden] [ref=e11]: ▦
        - text: 我的订单
      - button "智能售后" [ref=e12] [cursor=pointer]:
        - generic [aria-hidden] [ref=e13]: ◌
        - text: 智能售后
      - button "提交申请" [ref=e14] [cursor=pointer]:
        - generic [aria-hidden] [ref=e15]: ＋
        - text: 提交申请
      - button "申请与工单" [ref=e17] [cursor=pointer]:
        - generic [aria-hidden] [ref=e18]: ▤
        - text: 申请与工单
    - generic [ref=e19]:
      - generic [ref=e20]:
        - text: 本地沙箱环境
        - paragraph [ref=e22]: 退款与补发均为模拟执行
      - generic [ref=e23]:
        - generic [ref=e24]: C
        - generic [ref=e25]:
          - strong [ref=e26]: customer1
          - generic [ref=e27]: learner-001
        - button "退出登录" [ref=e28] [cursor=pointer]: ↗
  - generic [ref=e29]:
    - banner [ref=e30]:
      - generic [ref=e31]:
        - text: 客户服务/
        - strong [ref=e32]: 提交申请
      - generic [ref=e33]: ● 沙箱模拟业务
    - main [ref=e35]:
      - generic [ref=e37]:
        - generic [ref=e38]: NEW SERVICE REQUEST
        - heading "提交售后申请" [level=1] [ref=e39]
        - paragraph [ref=e40]: 选择服务类型并填写真实诉求；提交后会保存申请并等待人工审核。
      - generic [ref=e41]:
        - generic [ref=e42]:
          - heading "申请信息" [level=2] [ref=e44]
          - group [ref=e46]:
            - generic [ref=e47]:
              - text: 订单编号
              - textbox "订单编号" [disabled] [ref=e48]:
                - /placeholder: 填写您的订单编号
            - generic [ref=e49]:
              - text: 服务类型
              - combobox "服务类型" [disabled] [ref=e50]:
                - option "仅退款" [disabled] [selected]
                - option "退货退款" [disabled]
                - option "申请补发" [disabled]
                - option "物流问题" [disabled]
            - generic [ref=e51]:
              - text: 申请退款金额（元）
              - spinbutton "申请退款金额（元） 实际金额以服务端可退余额与冻结审批参数为准。" [disabled] [ref=e52]
              - generic [ref=e53]: 实际金额以服务端可退余额与冻结审批参数为准。
            - generic [ref=e54]:
              - text: 问题描述
              - textbox "问题描述" [disabled] [ref=e55]:
                - /placeholder: 请说明问题、期望的处理方式及必要的信息。
            - button "提交申请 →" [disabled] [ref=e56]
          - generic [ref=e57]:
            - strong [ref=e58]: 本次申请编号已保留
            - paragraph [ref=e59]: form-71ce56c6591a7188afafbd9667021ff52be578a4
            - generic [ref=e60]:
              - button "读取已保存状态" [ref=e61] [cursor=pointer]
              - button "另建申请" [ref=e62] [cursor=pointer]
            - generic [ref=e63]: 另建前请先核对原任务与工单，避免对同一问题重复申请。
        - generic [ref=e64]:
          - generic [ref=e65]:
            - heading "处理流程" [level=2] [ref=e67]
            - list [ref=e68]:
              - listitem [ref=e69]:
                - generic [ref=e70]: "01"
                - generic [ref=e71]:
                  - strong [ref=e72]: 保存申请与规则校验
                  - paragraph [ref=e73]: 校验订单归属、时限、金额、数量和库存。
              - listitem [ref=e74]:
                - generic [ref=e75]: "02"
                - generic [ref=e76]:
                  - strong [ref=e77]: 人工审核
                  - paragraph [ref=e78]: 管理员核对冻结参数和风险提示，记录审批理由。
              - listitem [ref=e79]:
                - generic [ref=e80]: "03"
                - generic [ref=e81]:
                  - strong [ref=e82]: 沙箱模拟执行
                  - paragraph [ref=e83]: 仅在批准后执行；退货退款须先确认收货。
          - generic [ref=e84]:
            - heading "已保存任务" [level=2] [ref=e86]
            - generic [ref=e87]:
              - generic [ref=e88]: 任务进度
              - generic [ref=e89]: 等待审批
            - paragraph [ref=e90]: form-71ce56c6591a7188afafbd9667021ff52be578a4
            - list [ref=e91]:
              - listitem [ref=e92]: 任务已保存
              - listitem [ref=e93]: 规则与参数校验
              - listitem [ref=e94]: 人工审批
              - listitem [ref=e95]: 模拟执行
            - generic [ref=e96]: 申请已保存，正在等待人工审批；退款、补发尚未执行。
            - generic [ref=e97]:
              - generic [ref=e98]:
                - heading "仅退款" [level=3] [ref=e99]
                - generic [ref=e100]: 等待审批
              - generic [ref=e101]:
                - generic [ref=e102]:
                  - term [ref=e103]: 订单编号
                  - definition [ref=e104]: UP-PAID
                - generic [ref=e105]:
                  - term [ref=e106]: 申请编号
                  - definition [ref=e107]: 71ced0f7-c2d8-435c-a88f-4569003173b3
                - generic [ref=e108]:
                  - term [ref=e109]: 客户
                  - definition [ref=e110]: learner-001
                - generic [ref=e111]:
                  - term [ref=e112]: 审批版本
                  - definition [ref=e113]: v1
                - generic [ref=e114]:
                  - term [ref=e115]: 申请退款
                  - definition [ref=e116]: ¥1.00
                - generic [ref=e117]:
                  - term [ref=e118]: 风险级别
                  - definition [ref=e119]: low
              - paragraph [ref=e120]: Edge-refund-1793155523693：独立沙箱浏览器验收，仅模拟退款1元。
            - group [ref=e121]:
              - generic "工具轨迹与运行指标" [ref=e122] [cursor=pointer]
    - contentinfo [ref=e123]:
      - text: 循服 AFTERCARE
      - generic [ref=e124]: 每项业务写入均经人工审批 · 服务端记录为准
```

# Test source

```ts
  155 |     "Enable explicitly for isolated sandbox writes; up to two model reply calls.",
  156 |   );
  157 |   test.skip(
  158 |     !process.env.E2E_CUSTOMER_PASSWORD || !process.env.E2E_ADMIN_PASSWORD,
  159 |     "Dedicated sandbox credentials required.",
  160 |   );
  161 |   const customerContext = await browser.newContext();
  162 |   const adminContext = await browser.newContext();
  163 |   const customer = await customerContext.newPage();
  164 |   const admin = await adminContext.newPage();
  165 |   const summary: Record<string, unknown>[] = [];
  166 |   await login(customer, "customer");
  167 |   await login(admin, "approver");
  168 |   await expect(
  169 |     customer.getByRole("heading", { name: "我的订单" }),
  170 |   ).toBeVisible();
  171 |   await expect(admin.locator(".stat-card")).toHaveCount(4);
  172 |   for (const [kind, order] of [
  173 |     ["refund", "UP-PAID"],
  174 |     ["return_refund", "UP-SHIPPED"],
  175 |   ]) {
  176 |     const existing = await admin.evaluate(async (wantedKind) => {
  177 |       const session = JSON.parse(sessionStorage.getItem("aftersales.session")!);
  178 |       const response = await fetch("/api/approvals", {
  179 |         headers: { Authorization: `Bearer ${session.token}` },
  180 |       });
  181 |       const records = (await response.json()) as Ticket[];
  182 |       return records.find(
  183 |         (item) =>
  184 |           item.description.startsWith(`Edge-${wantedKind}-`) &&
  185 |           ["pending", "waiting_approval"].includes(item.status),
  186 |       );
  187 |     }, kind);
  188 |     const marker =
  189 |       existing?.description.split("：")[0] || `Edge-${kind}-${Date.now()}`;
  190 |     if (existing)
  191 |       await customer.evaluate(
  192 |         ({ thread }) => {
  193 |           sessionStorage.setItem("aftersales.request.customer1", thread);
  194 |           sessionStorage.setItem("aftersales.request.customer1.thread", thread);
  195 |         },
  196 |         { thread: existing.thread_id },
  197 |       );
  198 |     await customer
  199 |       .getByRole("button", { name: "提交申请", exact: true })
  200 |       .click();
  201 |     let created: Task;
  202 |     if (existing) {
  203 |       const restore = customer.waitForResponse((response) =>
  204 |         response.url().endsWith(`/api/tasks/${existing.thread_id}`),
  205 |       );
  206 |       await customer
  207 |         .getByRole("button", { name: "读取已保存状态", exact: true })
  208 |         .click();
  209 |       created = (await (await restore).json()) as Task;
  210 |     } else {
  211 |       const another = customer.getByRole("button", {
  212 |         name: "另建申请",
  213 |         exact: true,
  214 |       });
  215 |       if (await another.count()) await another.click();
  216 |       await customer.getByLabel("订单编号", { exact: true }).fill(order);
  217 |       await customer.getByLabel("服务类型").selectOption(kind);
  218 |       await customer.getByLabel("申请退款金额（元）").fill("1.00");
  219 |       await customer
  220 |         .getByLabel("问题描述")
  221 |         .fill(`${marker}：独立沙箱浏览器验收，仅模拟退款1元。`);
  222 |       const submitResponse = customer.waitForResponse(
  223 |         (response) =>
  224 |           response.url().endsWith("/api/requests") &&
  225 |           response.request().method() === "POST",
  226 |       );
  227 |       await customer
  228 |         .getByRole("button", { name: "提交申请 →", exact: true })
  229 |         .click();
  230 |       const submitted = await submitResponse;
  231 |       expect(submitted.status()).toBe(200);
  232 |       created = (await submitted.json()) as Task;
  233 |     }
  234 |     await mkdir("test-results", { recursive: true });
  235 |     await writeFile(
  236 |       `test-results/${kind}-submitted.json`,
  237 |       JSON.stringify(created, null, 2),
  238 |     );
  239 |     expect(created.proposal?.action_id).toBeTruthy();
  240 |     expect(["pending", "waiting_approval"]).toContain(created.proposal?.status);
  241 |     await expect(
  242 |       customer.getByRole("heading", { name: "已保存任务" }),
  243 |     ).toBeVisible();
  244 |     await customer.screenshot({
  245 |       path: `test-results/${kind}-customer-pending.png`,
  246 |       fullPage: true,
  247 |     });
  248 |     console.log(
  249 |       `Inspecting original ${kind} sandbox request ${created.thread_id}`,
  250 |     );
  251 |     await admin.getByRole("button", { name: "申请审批", exact: true }).click();
  252 |     console.log("Approval workspace opened");
  253 |     await admin
  254 |       .getByRole("button", { name: "读取已保存状态", exact: true })
> 255 |       .click();
      |        ^ TimeoutError: locator.click: Timeout 15000ms exceeded.
  256 |     console.log("Approval list refreshed");
  257 |     await admin.locator(".approval-item").filter({ hasText: marker }).click();
  258 |     console.log("Original request selected");
  259 |     await admin
  260 |       .getByLabel("审批理由（必填）")
  261 |       .fill("已核对订单归属、冻结金额与沙箱验收范围，批准模拟1元退款。");
  262 |     const approvalResponse = admin.waitForResponse(
  263 |       (response) =>
  264 |         response
  265 |           .url()
  266 |           .includes(`/api/approvals/${created.proposal!.action_id}/decision`) &&
  267 |         response.request().method() === "POST",
  268 |       { timeout: 180000 },
  269 |     );
  270 |     await admin.getByRole("button", { name: "批准并继续处理" }).click();
  271 |     const decided = await approvalResponse;
  272 |     if (kind === "return_refund") {
  273 |       expect(decided.status()).toBe(200);
  274 |       await expect(
  275 |         admin.getByRole("button", { name: "确认退货已收到并模拟退款" }),
  276 |       ).toBeVisible();
  277 |       await admin.screenshot({
  278 |         path: "test-results/return-admin-awaiting-receipt.png",
  279 |         fullPage: true,
  280 |       });
  281 |       const receiveResponse = admin.waitForResponse(
  282 |         (response) =>
  283 |           response
  284 |             .url()
  285 |             .includes(`/api/returns/${created.proposal!.action_id}/receive`) &&
  286 |           response.request().method() === "POST",
  287 |         { timeout: 180000 },
  288 |       );
  289 |       await admin
  290 |         .getByRole("button", { name: "确认退货已收到并模拟退款" })
  291 |         .click();
  292 |       const received = await receiveResponse;
  293 |       summary.push({
  294 |         phase: "receive_http",
  295 |         kind,
  296 |         status: received.status(),
  297 |         response: await received.json(),
  298 |       });
  299 |     } else
  300 |       summary.push({
  301 |         phase: "approve_http",
  302 |         kind,
  303 |         status: decided.status(),
  304 |         response: await decided.json(),
  305 |       });
  306 |     await expect(admin.getByRole("status")).toHaveCount(0, { timeout: 180000 });
  307 |     await admin.getByRole("button", { name: "读取原任务与审计" }).click();
  308 |     await expect(
  309 |       admin.getByRole("heading", { name: "业务审计轨迹" }),
  310 |     ).toBeVisible();
  311 |     const savedResponse = customer.waitForResponse((response) =>
  312 |       response.url().endsWith(`/api/tasks/${created.thread_id}`),
  313 |     );
  314 |     await customer
  315 |       .getByRole("button", { name: "读取已保存状态", exact: true })
  316 |       .click();
  317 |     const saved = (await (await savedResponse).json()) as Task;
  318 |     expect(saved.proposal?.action_id).toBe(created.proposal?.action_id);
  319 |     expect(saved.proposal?.status).toBe("completed");
  320 |     expect(saved.proposal?.result?.amount).toBe("1.00");
  321 |     expect(saved.proposal?.result?.refund_id).toBeTruthy();
  322 |     summary.push({
  323 |       phase: "customer_saved",
  324 |       kind,
  325 |       request_id: saved.thread_id,
  326 |       status: saved.status,
  327 |       action: saved.proposal,
  328 |     });
  329 |     await customer.screenshot({
  330 |       path: `test-results/${kind}-customer-completed.png`,
  331 |       fullPage: true,
  332 |     });
  333 |     await admin.screenshot({
  334 |       path: `test-results/${kind}-admin-audit.png`,
  335 |       fullPage: true,
  336 |     });
  337 |   }
  338 |   await mkdir("test-results", { recursive: true });
  339 |   await writeFile(
  340 |     "test-results/real-write-flow.json",
  341 |     JSON.stringify(
  342 |       { at: new Date().toISOString(), business: "simulated", summary },
  343 |       null,
  344 |       2,
  345 |     ),
  346 |   );
  347 |   await customerContext.close();
  348 |   await adminContext.close();
  349 | });
  350 | 
```