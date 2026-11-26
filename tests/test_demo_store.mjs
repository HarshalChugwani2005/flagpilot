// Unit tests for demo-store/variant.js. Run with: node --test tests/test_demo_store.mjs

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  VISITOR_KEY,
  getOrCreateVisitorId,
  parseButtonConfig,
  resetVisitorId,
} from "../demo-store/variant.js";

/** Minimal in-memory stand-in for window.localStorage. */
function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  };
}

test("creates a visitor id once and reuses it", () => {
  const storage = memoryStorage();
  const first = getOrCreateVisitorId(storage, () => "id-1");
  const second = getOrCreateVisitorId(storage, () => "id-2");
  assert.equal(first, "id-1");
  assert.equal(second, "id-1");
  assert.equal(storage.getItem(VISITOR_KEY), "id-1");
});

test("reset gives the next visit a new id", () => {
  const storage = memoryStorage();
  getOrCreateVisitorId(storage, () => "id-1");
  resetVisitorId(storage);
  assert.equal(getOrCreateVisitorId(storage, () => "id-2"), "id-2");
});

test("default ids are random and distinct", () => {
  const a = getOrCreateVisitorId(memoryStorage());
  const b = getOrCreateVisitorId(memoryStorage());
  assert.notEqual(a, b);
});

test("parses a JSON string variant", () => {
  assert.deepEqual(parseButtonConfig('{"color":"#16a34a","label":"Buy now"}'), {
    ok: true,
    color: "#16a34a",
    label: "Buy now",
  });
});

test("accepts an already-parsed object", () => {
  assert.equal(parseButtonConfig({ color: "#fff", label: "Go" }).ok, true);
});

for (const [name, raw] of [
  ["missing flag", null],
  ["invalid JSON", "{nope"],
  ["array", "[]"],
  ["bad colour", '{"color":"red; position:fixed","label":"x"}'],
  ["empty label", '{"color":"#16a34a","label":"  "}'],
]) {
  test(`rejects ${name} with a reason`, () => {
    const result = parseButtonConfig(raw);
    assert.equal(result.ok, false);
    assert.ok(result.reason.length > 0);
  });
}
