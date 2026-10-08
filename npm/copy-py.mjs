// Bundles the video watcher (reel.py + its helpers) so `npx reel-agent watch` works without cloning the repo.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FILES = ["reel.py", "common.py", "analyze.py", "extract.py", "llm.py", "ocr.py"];

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, "..", ".claude", "skills", "reel-watch", "scripts");
fs.mkdirSync(path.join(here, "py"), { recursive: true });
for (const f of FILES) fs.copyFileSync(path.join(src, f), path.join(here, "py", f));
console.log(`bundled ${FILES.map((f) => `py/${f}`).join(", ")}`);
