// Sets one Worker secret, typed at wrangler's own prompt (never an argument or a file):
//
//   npm run secret:<env> -- <NAME>
//
// NAME is K_EDGE_<1-255> (the k_edge of that kid, from the Credentials page's escrow),
// PENDING_BEARER or PENDING_BEARER_NEXT (the engine's unsub/bearer, during rotation).
// It refuses before wrangler starts when check-config refuses the env (secretPlan in
// scripts/config-lib.mjs). It always runs: it is only ever a CLI.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { secretPlan } from "./config-lib.mjs";

const WRANGLER = fileURLToPath(new URL("../node_modules/wrangler/bin/wrangler.js", import.meta.url));

const plan = secretPlan(process.argv.slice(2));
if (plan.refusals !== undefined) {
  for (const refusal of plan.refusals) {
    process.stderr.write(`${refusal}\n`);
  }
  process.stderr.write("secret: refused; nothing was set.\n");
  process.exit(1);
}
if (!existsSync(WRANGLER)) {
  process.stderr.write(`secret: wrangler is not installed at ${WRANGLER}; run npm ci first.\n`);
  process.exit(1);
}
const result = spawnSync(process.execPath, [WRANGLER, ...plan.args], { stdio: "inherit" });
if (result.error !== undefined) {
  process.stderr.write(`secret: could not start wrangler (${result.error.message}); run npm ci first.\n`);
  process.exit(1);
}
process.exit(result.status ?? 1);
