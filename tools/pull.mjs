/*
 * Pull BoardWeaver /src files onto disk, byte-exactly, out of the Claude Code
 * session transcript.
 *
 * The MCP read_file tool returns a file's whole text. Retyping that text to
 * write it to disk is both expensive and a chance to introduce a typo, so this
 * pairs each read_file tool_use (which carries the path) with its tool_result
 * (which carries the content) and writes the bytes straight out.
 *
 *   node tools/pull.mjs <transcript.jsonl> <destRoot> [path ...]
 *
 * With no paths, every /src file seen in the transcript is written. A later
 * read of the same path wins, so re-reading a file refreshes it.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";

const [, , transcript, destRoot, ...only] = process.argv;
if (!transcript || !destRoot) {
  console.error("usage: node tools/pull.mjs <transcript.jsonl> <destRoot> [path ...]");
  process.exit(1);
}

const pathById = new Map();
const textById = new Map();

for (const line of readFileSync(transcript, "utf8").split("\n")) {
  if (!line) continue;
  let entry;
  try {
    entry = JSON.parse(line);
  } catch {
    continue;
  }
  const blocks = entry?.message?.content;
  if (!Array.isArray(blocks)) continue;
  for (const b of blocks) {
    if (b.type === "tool_use" && String(b.name ?? "").endsWith("read_file") && b.input?.path) {
      pathById.set(b.id, b.input.path);
    }
    if (b.type === "tool_result" && b.tool_use_id) {
      const c = b.content;
      const text = typeof c === "string"
        ? c
        : Array.isArray(c)
          ? c.map((p) => (typeof p === "string" ? p : p.text ?? "")).join("")
          : "";
      if (text) textById.set(b.tool_use_id, text);
    }
  }
}

// A persisted result is a pointer to a file rather than the content itself.
const PERSISTED = /Full output saved to:\s*(\S+)/;

const resolved = new Map();
for (const [id, srcPath] of pathById) {
  let text = textById.get(id);
  if (!text) continue;
  const persisted = text.match(PERSISTED);
  if (persisted) {
    try {
      const outer = JSON.parse(readFileSync(persisted[1], "utf8"));
      const blocks = Array.isArray(outer) ? outer : [outer];
      text = blocks.map((b) => (typeof b === "string" ? b : b.text ?? "")).join("");
    } catch {
      continue;
    }
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    continue; // an error result, not a file
  }
  if (typeof payload?.content !== "string") continue;
  resolved.set(srcPath, payload.content); // last read wins
}

const wanted = only.length > 0 ? only : [...resolved.keys()];
let written = 0;
for (const srcPath of wanted) {
  const content = resolved.get(srcPath);
  if (content === undefined) {
    console.error(`  MISS ${srcPath} — not in this transcript`);
    continue;
  }
  const dest = join(destRoot, srcPath.replace(/^\/src\//, "").replace(/^\//, ""));
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, content, "utf8");
  console.log(`  ${dest}  (${content.split("\n").length} lines)`);
  written++;
}
console.log(`${written}/${wanted.length} written`);
