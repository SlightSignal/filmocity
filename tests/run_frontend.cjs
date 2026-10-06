// Run with `node tests/run_frontend.cjs` on Windows, macOS, or Linux.
// Use Node's own executable and argument arrays so paths with spaces work.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
function run(args) {
  const result = spawnSync(process.execPath, args, { cwd: root, stdio: 'inherit' });
  if (result.error) console.error(result.error.message);
  if (result.error || result.status !== 0) process.exit(result.status || 1);
}
for (const name of fs.readdirSync(path.join(root, 'frontend')).filter(name => name.endsWith('.js')).sort()) {
  run(['--check', path.join(root, 'frontend', name)]);
}
const suites = fs.readdirSync(__dirname).filter(name => /^test_.*\.cjs$/.test(name)).sort();
if (!suites.length) throw new Error('No frontend test suites found');
for (const name of suites) run([path.join(__dirname, name)]);
console.log(`All ${suites.length} frontend suites and JavaScript syntax checks passed.`);
