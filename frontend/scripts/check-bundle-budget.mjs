import { gzipSync } from "node:zlib";
import { readFileSync, readdirSync } from "node:fs";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = new URL("../dist/", import.meta.url);
const ASSETS = fileURLToPath(new URL("../dist/assets/", import.meta.url));
const KIB = 1024;
const BUDGETS = {
  initialJavaScriptGzip: 120 * KIB,
  initialCssGzip: 25 * KIB,
  deferredJavaScriptChunkGzip: 425 * KIB,
};

const gzipSize = path => gzipSync(readFileSync(path)).byteLength;
const format = bytes => `${(bytes / KIB).toFixed(2)} KiB gzip`;
const indexHtml = readFileSync(new URL("index.html", DIST), "utf8");
const initialJavaScript = Array.from(indexHtml.matchAll(/(?:src|href)="\/assets\/([^"]+\.js)"/g), match => match[1]);
const initialCss = Array.from(indexHtml.matchAll(/href="\/assets\/([^"]+\.css)"/g), match => match[1]);
const javascriptChunks = readdirSync(ASSETS).filter(file => file.endsWith(".js"));

const initialJavaScriptBytes = initialJavaScript.reduce((total, file) => total + gzipSize(join(ASSETS, file)), 0);
const initialCssBytes = initialCss.reduce((total, file) => total + gzipSize(join(ASSETS, file)), 0);
const oversizedChunks = javascriptChunks
  .map(file => ({ file, bytes: gzipSize(join(ASSETS, file)) }))
  .filter(chunk => chunk.bytes > BUDGETS.deferredJavaScriptChunkGzip)
  .sort((left, right) => right.bytes - left.bytes);

const failures = [];
if (!initialJavaScript.length) failures.push("No initial JavaScript entry was found in dist/index.html.");
if (initialJavaScriptBytes > BUDGETS.initialJavaScriptGzip) {
  failures.push(`Initial JavaScript is ${format(initialJavaScriptBytes)}; budget is ${format(BUDGETS.initialJavaScriptGzip)}.`);
}
if (initialCssBytes > BUDGETS.initialCssGzip) {
  failures.push(`Initial CSS is ${format(initialCssBytes)}; budget is ${format(BUDGETS.initialCssGzip)}.`);
}
for (const chunk of oversizedChunks) {
  failures.push(`${basename(chunk.file)} is ${format(chunk.bytes)}; deferred JavaScript chunk budget is ${format(BUDGETS.deferredJavaScriptChunkGzip)}.`);
}

console.log(`Initial JavaScript: ${format(initialJavaScriptBytes)} / ${format(BUDGETS.initialJavaScriptGzip)}`);
console.log(`Initial CSS: ${format(initialCssBytes)} / ${format(BUDGETS.initialCssGzip)}`);
console.log(`Largest deferred JavaScript chunk: ${format(Math.max(0, ...javascriptChunks.map(file => gzipSize(join(ASSETS, file)))))} / ${format(BUDGETS.deferredJavaScriptChunkGzip)}`);

if (failures.length) {
  for (const failure of failures) console.error(`Bundle budget failure: ${failure}`);
  process.exitCode = 1;
} else {
  console.log("Bundle budget passed.");
}
