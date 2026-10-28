import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, requestReadPath } from "./api";
import { kindName, statusName } from "./ui";

afterEach(() => vi.unstubAllGlobals());
describe("HTTP contract", () => {
  it("uses same-origin API and authenticated GET without caching", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ total: 0, orders: [] })),
      );
    vi.stubGlobal("fetch", fetcher);
    const result = await new ApiClient("session-token").get(
      "/orders?limit=20&offset=0",
    );
    expect(result).toEqual({ total: 0, orders: [] });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/orders?limit=20&offset=0",
      expect.objectContaining({
        method: "GET",
        cache: "no-store",
        headers: expect.objectContaining({
          Authorization: "Bearer session-token",
        }),
      }),
    );
  });
  it("keeps the business error code and does not retry uncertain writes", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "BUSINESS_COMPLETED_REPLY_FAILED",
            message: "业务已保存",
          },
        }),
        { status: 503 },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    await expect(
      new ApiClient("t").post("/requests", { request_id: "saved-1" }),
    ).rejects.toMatchObject({
      code: "BUSINESS_COMPLETED_REPLY_FAILED",
      message: "业务已保存",
      status: 503,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("turns a lost response into a recoverable network error without replaying", async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError("connection lost"));
    vi.stubGlobal("fetch", fetcher);
    await expect(
      new ApiClient("t").post("/requests", {}),
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("does not leak raw HTML in a proxy failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html>upstream failed</html>", { status: 502 }),
        ),
    );
    await expect(new ApiClient("t").get("/tasks")).rejects.toMatchObject({
      code: "HTTP_502",
    });
  });
});

describe("saved business state presentation", () => {
  it("recovers a request by canonical task ID or the original idempotency ID", () => {
    expect(requestReadPath("req-original", "form-canonical")).toBe(
      "/tasks/form-canonical",
    );
    expect(requestReadPath("req-original")).toBe("/requests/req-original");
  });
  it("shows return acceptance separately from a completed refund", () => {
    expect(statusName("awaiting_return")).toBe("等待退货收货");
    expect(statusName("reply_failed")).toContain("业务已保存");
    expect(kindName("return_refund")).toBe("退货退款");
  });
});
