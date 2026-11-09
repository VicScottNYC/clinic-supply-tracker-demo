import { describe, expect, it } from "vitest";
import { ActionGate, submitAction } from "./intent";
import { ApiError } from "./api";

const ok = async (key: string) => ({ key });
const network = () => {
  throw new ApiError({ status: 0, code: "NETWORK", message: "dropped", uncertain: true });
};
const rejected = () => {
  throw new ApiError({ status: 409, code: "COUNT_OPEN", message: "locked" });
};

describe("submission keys", () => {
  it("retries of the unchanged request reuse the key after an unknown outcome", async () => {
    const gate = new ActionGate();
    await expect(submitAction(gate, "A", network)).rejects.toThrow("dropped");
    expect(gate.isUncertain).toBe(true);
    const keys: string[] = [];
    const out = await submitAction(gate, "A", async (k) => (keys.push(k), await ok(k)));
    expect(keys[0]).toBe(out.key);
    expect(gate.isUncertain).toBe(false);
  });

  it("refuses a different request while an outcome is unknown", async () => {
    const gate = new ActionGate();
    await expect(submitAction(gate, "A", network)).rejects.toThrow();
    await expect(submitAction(gate, "B", ok)).rejects.toThrow(/Retry that same request/);
  });

  it("uses a new key for the next identical action after success", async () => {
    const gate = new ActionGate();
    const first = await submitAction(gate, "A", ok);
    const second = await submitAction(gate, "A", ok);
    expect(second.key).not.toBe(first.key);
  });

  it("a definitive rejection clears the pending action so the user can correct and resubmit", async () => {
    const gate = new ActionGate();
    await expect(submitAction(gate, "A", rejected)).rejects.toThrow("locked");
    expect(gate.isUncertain).toBe(false);
    await expect(submitAction(gate, "B", ok)).resolves.toBeDefined();
  });
});
