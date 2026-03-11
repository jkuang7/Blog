import fs from "node:fs";

import { planActions } from "../planner/planner.js";
import { toCanonicalPlannerContext } from "../boundary/normalize.js";

function readStdin(): string {
  return fs.readFileSync(0, "utf8");
}

const raw = readStdin().trim();
if (!raw) {
  throw new Error("plan-callback: expected planner context JSON via stdin");
}

const parsed = JSON.parse(raw);
const context = toCanonicalPlannerContext(parsed);
const actions = planActions(context);

process.stdout.write(
  `${JSON.stringify(
    {
      actions
    },
    null,
    2
  )}\n`
);
