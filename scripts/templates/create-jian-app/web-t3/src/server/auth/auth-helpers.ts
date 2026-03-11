export function extractBearerToken(value: string | null) {
  if (!value) {
    return null;
  }

  const normalized = value.trim();
  if (!normalized.toLowerCase().startsWith("bearer ")) {
    return null;
  }

  const token = normalized.slice(7).trim();
  return token.length > 0 ? token : null;
}

export function roleFromEmail(email: string, adminEmails: string | undefined) {
  const normalizedEmail = email.trim().toLowerCase();
  if (!normalizedEmail) {
    return "USER";
  }

  const configuredAdmins = (adminEmails ?? "")
    .split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean);

  return configuredAdmins.includes(normalizedEmail) ? "ADMIN" : "USER";
}

export function safeNextPath(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/dashboard";
  }

  return value;
}
