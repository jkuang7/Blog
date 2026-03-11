export type Role = "USER" | "ADMIN";

export type SessionIdentity = {
  id: string;
  email: string;
};

export type RequestAuthContext = SessionIdentity & {
  role: Role;
};
