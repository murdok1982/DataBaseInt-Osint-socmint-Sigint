/**
 * readlink-eisdir-preload.cjs
 *
 * Workaround de Windows para `next build`.
 *
 * En Node moderno (v22+), `fs.readlink()` sobre un fichero normal en Windows
 * devuelve `EISDIR` en lugar de `EINVAL`. Webpack solo contempla `EINVAL` como
 * "no es un symlink", por lo que aborta el build con:
 *
 *   Error: EISDIR: illegal operation on a directory, readlink '...\_app.js'
 *
 * Este preload convierte `EISDIR` en `EINVAL` para `fs.readlink`,
 * `fs.readlinkSync` y `fs.promises.readlink`. Es inocuo en Linux/macOS.
 *
 * Uso:
 *   node -r ./scripts/readlink-eisdir-preload.cjs ./node_modules/next/dist/bin/next build
 */
"use strict";

const fs = require("fs");

function toEinvl(err, path) {
  const out = new Error(`EINVAL: invalid argument, readlink '${path}'`);
  out.code = "EINVAL";
  out.errno = -22;
  out.syscall = "readlink";
  out.path = path;
  out.cause = err;
  return out;
}

function patch(target) {
  if (!target || typeof target.readlink !== "function") return;

  const readlinkSync = target.readlinkSync;
  if (typeof readlinkSync === "function") {
    target.readlinkSync = function patchedReadlinkSync(path, options) {
      try {
        return readlinkSync.call(target, path, options);
      } catch (err) {
        if (err && err.code === "EISDIR") throw toEinvl(err, path);
        throw err;
      }
    };
  }

  const readlink = target.readlink;
  target.readlink = function patchedReadlink(path, options, callback) {
    let opts = options;
    let cb = callback;
    if (typeof opts === "function") {
      cb = opts;
      opts = undefined;
    }
    return readlink.call(target, path, opts, (err, link) => {
      if (err && err.code === "EISDIR") return cb(toEinvl(err, path));
      return cb(err, link);
    });
  };

  if (target.promises && typeof target.promises.readlink === "function") {
    const readlinkPromise = target.promises.readlink;
    target.promises.readlink = async function patchedReadlinkPromise(path, options) {
      try {
        return await readlinkPromise.call(target.promises, path, options);
      } catch (err) {
        if (err && err.code === "EISDIR") throw toEinvl(err, path);
        throw err;
      }
    };
  }
}

// `graceful-fs` parchea `fs` al cargarse; parcheamos después para quedar encima.
let graceful;
try {
  // eslint-disable-next-line global-require
  graceful = require("graceful-fs");
} catch (_) {
  graceful = null;
}

patch(fs);
patch(graceful);
