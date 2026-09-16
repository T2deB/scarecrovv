/*
 * Pull the `content` string out of an oversized MCP read_file result that the
 * harness spilled to a file, and write it verbatim to disk.
 *
 * Two spill formats exist: a JSON array of text blocks, and the raw JSON
 * payload as plain text. Both wrap the same {content, kind, status} object.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const [, , inPath, outPath] = process.argv;
if (!inPath || !outPath) {
  console.error("usage: node tools/extract.mjs <spilled-result> <dest>");
  process.exit(1);
}

const raw = readFileSync(inPath, "utf8");
let payload;
try {
  payload = JSON.parse(raw);
} catch {
  throw new Error("spill file is not JSON");
}
if (Array.isArray(payload)) {
  const text = payload.map((b) => (typeof b === "string" ? b : b.text ?? "")).join("");
  payload = JSON.parse(text);
}
if (typeof payload.content !== "string") throw new Error("no content field");

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, payload.content, "utf8");
console.log(`${outPath}: ${payload.content.split("\n").length} lines, kind=${payload.kind}, status=${payload.status}`);
