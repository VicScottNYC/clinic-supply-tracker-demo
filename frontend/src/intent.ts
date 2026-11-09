// Submission-key discipline. One intended action -> one Idempotency-Key. Retries of the unchanged request
// reuse it. While an outcome is unknown, a different request is refused until the original is retried.
import { ApiError } from "./api";

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export class ActionGate {
  private pending: { fingerprint: string; key: string; uncertain: boolean } | null = null;

  get isUncertain(): boolean {
    return this.pending?.uncertain ?? false;
  }

  prepare(fingerprint: string): string {
    if (this.pending) {
      if (this.pending.fingerprint === fingerprint) return this.pending.key;
      if (this.pending.uncertain) {
        throw new Error("The previous action's outcome is unknown. Retry that same request before changing it.");
      }
    }
    this.pending = { fingerprint, key: newIdempotencyKey(), uncertain: false };
    return this.pending.key;
  }

  markUncertain(): void {
    if (this.pending) this.pending.uncertain = true;
  }

  settle(): void {
    this.pending = null;
  }
}

export async function submitAction<T>(
  gate: ActionGate,
  fingerprint: string,
  send: (key: string) => Promise<T>,
): Promise<T> {
  const key = gate.prepare(fingerprint);
  try {
    const out = await send(key);
    gate.settle();
    return out;
  } catch (e) {
    if (e instanceof ApiError && e.uncertain) gate.markUncertain();
    else gate.settle();
    throw e;
  }
}

export function fingerprintOf(parts: unknown): string {
  return JSON.stringify(parts);
}
