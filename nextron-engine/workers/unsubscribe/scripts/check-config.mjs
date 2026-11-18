// Refuses to deploy (or set a secret for) an environment whose wrangler.jsonc isn't ready.
//
//   node scripts/check-config.mjs <dev|test|prod>
//
// Exits 0 and prints "ok" only when checkConfig (scripts/config-lib.mjs) finds nothing;
// otherwise it prints every problem and exits 1. It always runs: it is only ever a CLI.

import { checkConfigFile } from "./config-lib.mjs";

const envName = process.argv[2];
const problems = checkConfigFile(envName);
if (problems.length > 0) {
  for (const problem of problems) {
    process.stderr.write(`check-config: ${problem}\n`);
  }
  process.stderr.write("check-config: refused; nothing was deployed or set.\n");
  process.exit(1);
}
process.stdout.write(`check-config: env.${envName} ok\n`);
