import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/types";
import { apiGet } from "@/api/client";

describe("apiGet", () => {
  it("returns parsed JSON on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ a: 1 }) }));
    await expect(apiGet("/x")).resolves.toEqual({ a: 1 });
    vi.unstubAllGlobals();
  });

  it("maps 404 to not_found", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: "nf", json: async () => ({ detail: "nope" }) }));
    await expect(apiGet("/x")).rejects.toMatchObject({ kind: "not_found", status: 404, message: "nope" });
    vi.unstubAllGlobals();
  });

  it("maps 503 to unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: "svc", json: async () => ({}) }));
    await expect(apiGet("/x")).rejects.toBeInstanceOf(ApiError);
    vi.unstubAllGlobals();
  });

  it("maps network failure to network", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
    await expect(apiGet("/x")).rejects.toMatchObject({ kind: "network" });
    vi.unstubAllGlobals();
  });

  it("maps abort to timeout", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(Object.assign(new Error("aborted"), { name: "AbortError" })));
    await expect(apiGet("/x")).rejects.toMatchObject({ kind: "timeout" });
    vi.unstubAllGlobals();
  });
});