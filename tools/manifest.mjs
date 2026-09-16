/*
 * Record what the BoardWeaver worktree looked like at the moment we pulled it.
 *
 * `list_files` returns a blobHash per file. Storing those hashes is what turns
 * "is sim/ measuring the game people actually play?" from a question nobody can
 * answer into one command. We never need to reproduce the hash locally — only
 * to compare a stored listing against a fresh one.
 *
 *   node tools/manifest.mjs write <transcript.jsonl>   # from the latest list_files
 *   node tools/manifest.mjs check <transcript.jsonl>   # diff stored vs latest
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const OUT = "src/SYNC.json";
const [, , mode, transcript] = process.argv;
if (!["write", "check"].includes(mode ?? "") || !transcript) {
  console.error("usage: node tools/manifest.mjs <write|check> <transcript.jsonl>");
  process.exit(1);
}

/** The most recent list_files result in the transcript. */
const latest = () => {
  const ids = new Set();
  let found = null;
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
      if (b.type === "tool_use" && String(b.name ?? "").endsWith("list_files")) ids.add(b.id);
      if (b.type === "tool_result" && ids.has(b.tool_use_id)) {
        const c = b.content;
        const text = typeof c === "string"
          ? c
          : Array.isArray(c) ? c.map((p) => (typeof p === "string" ? p : p.text ?? "")).join("") : "";
        try {
          const payload = JSON.parse(text);
          if (Array.isArray(payload?.files)) found = payload.files;
        } catch { /* not a listing */ }
      }
    }
  }
  return found;
};

const files = latest();
if (!files) {
  console.error("no list_files result in this transcript — run list_files first");
  process.exit(1);
}

const byKey = Object.fromEntries(
  files.map((f) => [f.storageKey, { kind: f.kind, blobHash: f.blobHash }]),
);

if (mode === "write") {
  const doc = { gameDefinitionId: 86, pulledAt: new Date().toISOString().slice(0, 10), files: byKey };
  writeFileSync(OUT, `${JSON.stringify(doc, null, 2)}\n`);
  console.log(`${OUT}: ${Object.keys(byKey).length} files recorded`);
  process.exit(0);
}

if (!existsSync(OUT)) {
  console.error(`${OUT} missing — run 'write' first`);
  process.exit(1);
}
const stored = JSON.parse(readFileSync(OUT, "utf8")).files;
let drift = 0;
for (const key of new Set([...Object.keys(stored), ...Object.keys(byKey)])) {
  const a = stored[key]?.blobHash;
  const b = byKey[key]?.blobHash;
  if (a === b) continue;
  drift++;
  console.log(!a ? `  NEW      ${key}` : !b ? `  DELETED  ${key}` : `  CHANGED  ${key}`);
}
console.log(drift === 0 ? "engine in sync" : `${drift} file(s) drifted — re-pull before trusting any numbers`);
process.exit(drift === 0 ? 0 : 1);
