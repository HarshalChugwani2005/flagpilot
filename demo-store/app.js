// Demo store: identifies the visitor, reads the checkout button flag and renders its variant.

import flagsmith from "https://cdn.jsdelivr.net/npm/flagsmith@10.0.0/index.mjs";
import config from "./config.js";
import { getOrCreateVisitorId, parseButtonConfig, resetVisitorId } from "./variant.js";

const button = document.getElementById("checkout");
const badgeText = document.getElementById("badge-text");
const badgeSwatch = document.getElementById("badge-swatch");
const newVisitor = document.getElementById("new-visitor");

/**
 * Show a problem in the debug badge and keep the checkout button disabled.
 * @param {string} message
 */
function showError(message) {
  button.disabled = true;
  badgeSwatch.hidden = true;
  badgeText.textContent = message;
  badgeText.parentElement.classList.add("error");
}

/**
 * Style the checkout button from the variant and describe it in the debug badge.
 * @param {string} visitorId
 * @param {{color: string, label: string}} variant
 */
function showVariant(visitorId, variant) {
  button.textContent = variant.label;
  button.style.backgroundColor = variant.color;
  button.disabled = false;
  badgeSwatch.style.backgroundColor = variant.color;
  badgeSwatch.hidden = false;
  badgeText.textContent = `${variant.label} · ${variant.color} · visitor ${visitorId.slice(0, 8)}`;
}

/** Connect to Flagsmith as this visitor and render the assigned variant. */
async function start() {
  if (!config.environmentID) {
    showError("FLAGSMITH_ENVIRONMENT_KEY is empty in .env");
    return;
  }
  const visitorId = getOrCreateVisitorId(localStorage);
  let failed = false;
  const onError = (error) => {
    failed = true;
    showError(`Could not load flags from Flagsmith: ${error.message ?? error}`);
  };
  try {
    await flagsmith.init({ environmentID: config.environmentID, identity: visitorId, onError });
  } catch (error) {
    onError(error);
  }
  if (failed) {
    return;
  }
  const variant = parseButtonConfig(flagsmith.getValue(config.flagName));
  if (!variant.ok) {
    showError(`Flag "${config.flagName}": ${variant.reason}`);
    return;
  }
  showVariant(visitorId, variant);
}

newVisitor.addEventListener("click", () => {
  resetVisitorId(localStorage);
  window.location.reload();
});

start();
