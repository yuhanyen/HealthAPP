// Node 24 的 fs.rmSync 改以 C++（std::filesystem）實作，在本專案路徑（OneDrive 下含全形字的資料夾）
// 會讓整個行程直接崩潰（0xC0000409）。pnpm 安裝與 Vite 清快取都會呼叫它。
// 以 NODE_OPTIONS=--require 預先載入本檔，改用 JS 版遞迴刪除（lstat/readdir/unlink/rmdir 在此路徑正常）。
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { fileURLToPath } = require("node:url");

function toPath(target) {
  if (target instanceof URL) return fileURLToPath(target);
  return Buffer.isBuffer(target) ? target.toString() : String(target);
}

function attempt(fn, retries) {
  for (let i = 0; ; i++) {
    try {
      return fn();
    } catch (err) {
      if (err.code === "EPERM" || err.code === "EACCES") {
        // Windows 唯讀檔：先解除唯讀再試
        try {
          fs.chmodSync(err.path || "", 0o666);
        } catch {
          /* 忽略 */
        }
      }
      const retriable = ["EBUSY", "EPERM", "EACCES", "ENOTEMPTY", "EMFILE", "ENFILE"];
      if (i >= retries || !retriable.includes(err.code)) throw err;
    }
  }
}

function rmSync(target, options = {}) {
  const file = toPath(target);
  const retries = options.maxRetries ?? 2;
  let stat;
  try {
    stat = fs.lstatSync(file);
  } catch (err) {
    if (err.code === "ENOENT" && options.force) return;
    throw err;
  }
  if (stat.isDirectory()) {
    if (!options.recursive) {
      const err = new Error(`Path is a directory: rm returned EISDIR (is a directory) ${file}`);
      err.code = "ERR_FS_EISDIR";
      throw err;
    }
    for (const name of fs.readdirSync(file)) {
      rmSync(path.join(file, name), { ...options, force: true });
    }
    attempt(() => fs.rmdirSync(file), retries);
  } else {
    attempt(() => fs.unlinkSync(file), retries);
  }
}

fs.rmSync = rmSync;
