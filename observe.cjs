const fs = require('node:fs');
const path = require('node:path');
const entry = process.argv[1] || '';
const bin = path.basename(entry);
if (entry === process.env.C8_488_BIN || ['mocha', 'mocha.js', '_mocha', 'child.cjs'].includes(bin)) {
  fs.appendFileSync(process.env.C8_488_TRACE, JSON.stringify({bin: entry === process.env.C8_488_BIN ? 'c8' : bin, argv: process.argv.slice(2)}) + '\n');
}
