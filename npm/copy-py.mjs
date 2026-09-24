// Bundles the video watcher (reel.py + its helpers) so `npx reel-agent watch` works without cloning the repo.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, "..", ".claude", "skills", "reel-watch", "scripts");
fs.mkdirSync(path.join(here, "py"), { recursive: true });
for (const f of ["reel.py", "common.py"]) fs.copyFileSync(path.join(src, f), path.join(here, "py", f));
console.log("bundled py/reel.py and py/common.py");
