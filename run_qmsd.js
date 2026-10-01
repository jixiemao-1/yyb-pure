const fs = require("fs");
const path = require("path");
const vm = require("vm");

// Use vm loader to avoid Node module-loader crash on some Unicode filenames in this environment.
const targetFile = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.resolve(__dirname, "全面时代code版.js");

const code = fs.readFileSync(targetFile, "utf8");
const context = {
  require,
  module: { exports: {} },
  exports: {},
  __filename: targetFile,
  __dirname: path.dirname(targetFile),
  process,
  console,
  Buffer,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  setImmediate,
  clearImmediate,
};
context.global = context;

vm.runInNewContext(code, context, {
  filename: targetFile,
  displayErrors: true,
});
