import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
import { test } from "node:test";

test("repeated WS/bootstrap cache cannot keep a stopped camera online", async () => {
  const source = (await readFile(new URL("../web/js/store.js", import.meta.url), "utf8"))
    .replace('import { api } from "./api.js";', 'const api = { stations: async () => JSON.parse(JSON.stringify(globalThis.__stationFixture)) };');
  const module = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
  const original = Date.now;
  let now = 10000;
  Date.now = () => now;
  try {
    globalThis.__stationFixture = [{ meta: { id: "ST" } }];
    await module.bootstrap();
    assert.equal(module.isOffline("ST"), true);
    globalThis.__stationFixture[0].frame = 1;
    await module.bootstrap();
    assert.equal(module.isOffline("ST"), false);
    now += 6000;
    await module.bootstrap();
    assert.equal(module.isOffline("ST"), true);
    globalThis.__stationFixture[0].frame = 2;
    await module.bootstrap();
    assert.equal(module.isOffline("ST"), false);
  } finally {
    Date.now = original;
    delete globalThis.__stationFixture;
  }
});
