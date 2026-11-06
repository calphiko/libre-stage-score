import adapter from "@sveltejs/adapter-node";
import staticAdapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

const config = {
   extensions: ['.svelte'],
  preprocess: vitePreprocess(),
  kit: {
    adapter: process.env.LIBRE_STAGE_DESKTOP === "1"
      ? staticAdapter({ fallback: "index.html" })
      : adapter(),
  },
};

export default config;
