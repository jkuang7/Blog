import { createNextConfig } from "./eslint/next.mjs";

export default createNextConfig({
  profile: "hardened",
  maintainabilitySeverity: "warn",
});
