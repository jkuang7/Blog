export function resolveIp(headerValue: string | null): string {
  if (!headerValue) {
    return "unknown";
  }

  const first = headerValue.split(",")[0];
  return first?.trim() || "unknown";
}
