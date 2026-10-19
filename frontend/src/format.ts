// Display helpers only. No business rule lives here.
const CLINIC_TZ = "America/New_York";

export function localTime(utcIso: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: CLINIC_TZ, dateStyle: "medium", timeStyle: "short" })
    .format(new Date(utcIso)) + " ET";
}

export function qty(n: number): string {
  return n.toLocaleString("en-US");
}

export function signed(n: number): string {
  return (n > 0 ? "+" : "") + n.toLocaleString("en-US");
}

export const CATEGORY_LABEL: Record<string, string> = {
  expired: "Expired",
  expiring_soon: "Expiring soon",
  none: "",
};

export const STATE_LABEL: Record<string, string> = {
  pending: "Not counted yet",
  within: "Matched",
  out: "Out of tolerance: recount",
  resolved_recount: "Resolved on recount",
  adjusted: "Adjusted",
};

export const OPERATION_LABEL: Record<string, string> = {
  opening: "Opening balance",
  receipt: "Receipt",
  issue: "Issue",
  count_completion: "Count completion",
};

export const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  completed: "Completed",
  cancelled: "Cancelled",
};
