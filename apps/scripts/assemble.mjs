// Put the three builds where CloudFront serves them: landing at /, the bots at /broker/ and /shopper/.
// The result (apps/site/) is what scripts/aws/deploy-web.sh syncs to the web bucket.
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const site = join(root, "site");
rmSync(site, { recursive: true, force: true });
for (const [app, to] of [["landing", ""], ["broker", "broker"], ["shopper", "shopper"]]) {
  const dist = join(root, app, "dist");
  if (!existsSync(dist)) throw new Error(`${app} isn't built: run npm run build`);
  cpSync(dist, join(site, to), { recursive: true });
}
console.log(`assembled ${site}`);
