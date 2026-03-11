import { collectEslintReports, flattenMessages, printEslintSummary, writeEslintReport } from "./eslint-report.mjs";

const report = collectEslintReports();
const reportPath = writeEslintReport(report);
const messages = flattenMessages(report);

printEslintSummary(messages);
console.log(`Wrote report: ${reportPath}`);
