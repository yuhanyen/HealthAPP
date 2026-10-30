// 清空 dist：Node 24 的 fs.rmSync 在全形路徑（本專案位於「【重要優先】…」資料夾）會當掉，
// 所以 Vite 的 emptyOutDir 關閉，改用逐檔 unlink／rmdir。
import { existsSync, readdirSync, rmdirSync, statSync, unlinkSync } from "node:fs";
import { join, resolve } from "node:path";

function clear(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      clear(path);
      rmdirSync(path);
    } else {
      unlinkSync(path);
    }
  }
}

const target = resolve(process.argv[2] ?? "dist");
if (existsSync(target)) clear(target);
