import type { ReactNode } from "react";
import { ApiError } from "./api";

export function Loading() {
  return <p className="muted">Loading…</p>;
}

export function Banner({ kind, children }: { kind: "info" | "warn" | "error" | "ok"; children: ReactNode }) {
  return <div className={`banner banner-${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}

export function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <span className="field-error" role="alert">{message}</span>;
}

// Shows a failed action. Unknown outcomes are called out explicitly, with the retry rule.
export function ActionError({ error, uncertain }: { error: unknown; uncertain: boolean }) {
  if (!error) return null;
  if (uncertain) {
    return (
      <Banner kind="warn">
        <strong>Outcome unknown.</strong> {(error as Error).message} Retry the same request. Nothing else can be submitted until the outcome is confirmed.
      </Banner>
    );
  }
  const code = error instanceof ApiError ? error.code : "";
  return (
    <Banner kind="error">
      {code && <strong>{code}: </strong>}
      {(error as Error).message}
    </Banner>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warn" | "ok" | "danger" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
