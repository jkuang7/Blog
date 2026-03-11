import { env } from "~/env";
import { getIdentityFromServerSession } from "~/lib/auth";
import { roleFromEmail } from "~/server/auth/auth-helpers";

export async function getSessionUser() {
  const identity = await getIdentityFromServerSession();
  if (!identity) {
    return null;
  }

  return {
    id: identity.id,
    email: identity.email,
    role: roleFromEmail(identity.email, env.ADMIN_EMAILS),
  };
}
