import { normalizePath } from "./lib.mjs";

const BANNED_LINE_PATTERNS = [
  {
    code: "obsolete-marker",
    regex: /\b(?:obsolete|dead\s+code|remove\s+me|temporary\s+workaround)\b/i,
    message: "Remove obsolete/dead-code markers by deleting or rewriting the code.",
  },
];

function extractCommentText(addedText) {
  const trimmed = addedText.trimStart();

  if (trimmed.startsWith("//")) {
    return trimmed.slice(2).trim();
  }

  if (trimmed.startsWith("/*")) {
    return trimmed.slice(2).replace(/\*\/\s*$/, "").trim();
  }

  if (trimmed.startsWith("*")) {
    return trimmed.slice(1).replace(/\*\/\s*$/, "").trim();
  }

  return null;
}

export function parseAddedLineViolations(diffText) {
  const violations = [];
  let currentFile = "";
  let currentLine = 0;

  const lines = diffText.split("\n");
  for (const line of lines) {
    if (line.startsWith("+++ b/")) {
      currentFile = normalizePath(line.slice("+++ b/".length));
      continue;
    }

    if (line.startsWith("@@")) {
      const headerMatch = line.match(/\+(\d+)(?:,(\d+))?/);
      if (headerMatch) {
        currentLine = Number.parseInt(headerMatch[1], 10);
      }
      continue;
    }

    if (line.startsWith("+") && !line.startsWith("+++")) {
      const addedText = line.slice(1);
      const commentText = extractCommentText(addedText);

      if (commentText) {
        for (const pattern of BANNED_LINE_PATTERNS) {
          if (!pattern.regex.test(commentText)) continue;

          violations.push({
            file: currentFile,
            line: currentLine,
            code: pattern.code,
            message: pattern.message,
            text: addedText.trim(),
          });
        }
      }

      currentLine += 1;
      continue;
    }

    if (line.startsWith(" ")) {
      currentLine += 1;
    }
  }

  return violations;
}
