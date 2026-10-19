// Typed client for the P1 API. The server decides every business rule; this file only transports requests
// and turns failures into ApiError values that say whether the outcome is known.

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly uncertain: boolean;
  readonly fields: Record<string, string>;

  constructor(init: { status: number; code: string; message: string; uncertain?: boolean; fields?: Record<string, string> }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.uncertain = init.uncertain ?? false;
    this.fields = init.fields ?? {};
  }
}

const BASE = "/api";

async function readError(r: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await r.json();
  } catch {
    body = null;
  }
  const b = body as { error?: string; message?: string; detail?: unknown } | null;
  if (r.status === 422 && b && Array.isArray(b.detail)) {
    const fields: Record<string, string> = {};
    const parts: string[] = [];
    for (const d of b.detail as { loc?: unknown[]; msg?: string }[]) {
      const path = (d.loc ?? []).filter((x) => x !== "body").join(".");
      if (path) fields[path] = d.msg ?? "Invalid value";
      parts.push(`${path || "request"}: ${d.msg ?? "invalid"}`);
    }
    return new ApiError({ status: 422, code: "INVALID_INPUT", message: parts.join("; "), fields });
  }
  // Any 5xx leaves the outcome unknown, whatever the body looks like: the server may have committed.
  const uncertain = r.status >= 500;
  if (b && typeof b.error === "string") {
    return new ApiError({ status: r.status, code: b.error, message: b.message ?? b.error, uncertain });
  }
  return new ApiError({
    status: r.status,
    code: `HTTP_${r.status}`,
    message: `The server returned an error (${r.status}).`,
    uncertain,
  });
}

export const api = {
  async get<T>(path: string): Promise<T> {
    let r: Response;
    try {
      r = await fetch(BASE + path);
    } catch {
      throw new ApiError({ status: 0, code: "NETWORK", message: "Cannot reach the API. Is it running?" });
    }
    if (!r.ok) throw await readError(r);
    return (await r.json()) as T;
  },

  // Every write carries the actor and an Idempotency-Key. A lost connection or 5xx leaves the outcome
  // unknown (uncertain = true): the caller must retry the same request with the same key.
  async post<T>(path: string, actor: string, key: string, body?: unknown): Promise<T> {
    let r: Response;
    try {
      r = await fetch(BASE + path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Actor-Id": actor,
          "Idempotency-Key": key,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError({
        status: 0,
        code: "NETWORK",
        message: "The connection dropped before a response arrived.",
        uncertain: true,
      });
    }
    if (!r.ok) throw await readError(r);
    try {
      return (await r.json()) as T;
    } catch {
      throw new ApiError({
        status: r.status, code: "BAD_RESPONSE",
        message: "The response could not be read.", uncertain: true,
      });
    }
  },
};

export function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
