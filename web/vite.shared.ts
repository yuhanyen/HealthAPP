// 四個前端共用的 Vite 設定：掛在 /app/<名稱>/、開發時 /api 代理到 :8000、
// PWA manifest 名稱由 build 時從 i18n/zh-TW.json 產生（前端不寫死使用者可見文字）。
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin, type UserConfig } from "vite";

const I18N = resolve(__dirname, "../i18n/zh-TW.json");

function lookup(catalog: Record<string, unknown>, key: string): string {
  let node: unknown = catalog;
  for (const part of key.split(".")) node = (node as Record<string, unknown>)[part];
  return String(node);
}

function i18nManifest(base: string, titleKey: string, theme: string): Plugin {
  const render = () => {
    const title = lookup(JSON.parse(readFileSync(I18N, "utf-8")) as Record<string, unknown>, titleKey);
    return JSON.stringify({
      name: title,
      short_name: title,
      lang: "zh-Hant-TW",
      start_url: base,
      scope: base,
      display: "standalone",
      background_color: "#f4f6f8",
      theme_color: theme,
      icons: [{ src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
    });
  };
  return {
    name: "i18n-manifest",
    configureServer(server) {
      server.middlewares.use(`${base}manifest.webmanifest`, (_req, res) => {
        res.setHeader("Content-Type", "application/manifest+json");
        res.end(render());
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "manifest.webmanifest", source: render() });
    },
  };
}

export function appConfig(name: string, options: { port: number; titleKey: string; theme?: string }): UserConfig {
  const base = `/app/${name}/`;
  return defineConfig({
    base,
    plugins: [react(), i18nManifest(base, options.titleKey, options.theme ?? "#0b5fb8")],
    server: {
      port: options.port,
      strictPort: true,
      proxy: { "/api": "http://127.0.0.1:8000" },
    },
    // 清空 dist 改由 scripts/clean.mjs（Node 24 rmSync 在全形路徑會當掉）
    build: { outDir: "dist", emptyOutDir: false },
  });
}
