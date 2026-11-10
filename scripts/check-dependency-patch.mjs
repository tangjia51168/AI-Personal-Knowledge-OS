import { createRequire } from "node:module";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const estreeRequire = createRequire(require.resolve("@typescript-eslint/typescript-estree"));
const installed = estreeRequire("minimatch/package.json").version;
assert.equal(installed, "11.0.1", "Security override must affect the actual parser dependency");
assert.equal(estreeRequire("minimatch").minimatch("example.ts", "**/*.ts"), true);
console.log(`PASS: typescript-estree resolves patched minimatch ${installed}`);
