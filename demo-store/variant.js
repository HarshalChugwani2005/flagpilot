// Pure helpers for the demo store (no DOM, no network), unit-tested with `node --test`.

export const VISITOR_KEY = "flagpilot_visitor_id";

const HEX_COLOR = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

/**
 * Return this browser's visitor id, creating and saving a random one on the first visit.
 * @param {Storage} storage Usually `window.localStorage`.
 * @param {() => string} makeId Id generator (random UUID by default).
 * @returns {string}
 */
export function getOrCreateVisitorId(storage, makeId = () => crypto.randomUUID()) {
  const existing = storage.getItem(VISITOR_KEY);
  if (existing) {
    return existing;
  }
  const id = makeId();
  storage.setItem(VISITOR_KEY, id);
  return id;
}

/**
 * Forget the visitor id so the next page load behaves like a brand-new visitor.
 * @param {Storage} storage
 */
export function resetVisitorId(storage) {
  storage.removeItem(VISITOR_KEY);
}

/**
 * Validate a `checkout_button` flag value such as `{"color":"#16a34a","label":"Buy now"}`.
 * @param {unknown} raw The value returned by `flagsmith.getValue` (JSON string or object).
 * @returns {{ok: true, color: string, label: string} | {ok: false, reason: string}}
 */
export function parseButtonConfig(raw) {
  if (raw === null || raw === undefined || raw === "") {
    return { ok: false, reason: "the flag was not found or has no value" };
  }
  let value = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw);
    } catch {
      return { ok: false, reason: "the flag value is not valid JSON" };
    }
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { ok: false, reason: "the flag value must be a JSON object" };
  }
  const { color, label } = value;
  if (typeof color !== "string" || !HEX_COLOR.test(color)) {
    return { ok: false, reason: '"color" must be a hex colour such as #16a34a' };
  }
  if (typeof label !== "string" || label.trim() === "") {
    return { ok: false, reason: '"label" must be a non-empty string' };
  }
  return { ok: true, color, label: label.trim() };
}
