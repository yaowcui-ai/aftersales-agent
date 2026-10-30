import { expect, test } from "@playwright/test";

test("public demo identifies React screens and keeps model baseline historical", async ({ page }) => {
  test.skip(process.env.E2E_PUBLIC_DEMO !== "1", "Public evidence page check only");
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const address = process.env.E2E_PUBLIC_URL || "http://127.0.0.1:18745/demo/";
  await expect.poll(async () => {
    try { return (await page.request.get(address)).status(); }
    catch { return 0; }
  }, { timeout: 15000 }).toBe(200);
  await page.goto(address);
  await expect(page.locator(".stack-strip")).toContainText("React");
  await expect(page.locator(".stack-strip")).toContainText("TypeScript");
  await expect(page.locator("#screenshots")).toContainText("React 客户工作台");
  await expect(page.locator("#screenshots")).toContainText("React 管理员工作台");
  for (const image of await page.locator("#screenshots img").all()) {
    await image.scrollIntoViewIfNeeded();
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBeTruthy();
  }
  await expect(page.locator("#evaluation-detail")).toContainText("历史");
  await expect(page.locator("#evaluation-detail")).toContainText("不代表新版");
  await expect(page.locator("#tests-value")).toContainText("153");
  await expect(page.locator("#recordings")).toContainText("Streamlit 历史版本");
  await expect(page.locator(".hero-note")).toContainText("不提供在线对话");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});
