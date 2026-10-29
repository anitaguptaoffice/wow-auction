const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(path.join(__dirname, "../frontend/src/lib/wowhead.ts"), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const scripts = new Map();
const context = {
  exports: {}, window: {},
  document: {
    getElementById: (id) => scripts.get(id),
    createElement: () => ({ dataset: {} }),
    head: { appendChild: (script) => scripts.set(script.id, script) },
  },
};
vm.runInNewContext(compiled, context);
const { wowheadLink, enableWowheadTooltips } = context.exports;

assert.equal(wowheadLink({ itemID: 19019 }).href, "https://www.wowhead.com/cn/item=19019");
assert.equal(wowheadLink({ itemID: 82800, petSpeciesID: 159 }).href, "https://www.wowhead.com/cn/battle-pet/159");
assert.equal(wowheadLink({ itemID: 82800, battlePetCreatureID: 1234 }).href, "https://www.wowhead.com/cn/npc=1234");
const fields = ["123", "456", "789", "", "", "", "", "", "80", "", "", "6", "2", "450", "565"];
const itemLink = `|cffa335ee|Hitem:${fields.join(":")}|h[Example]|h|r`;
const variant = wowheadLink({ itemID: 123, itemLink });
const params = new URL(variant.href).searchParams;
assert.equal(params.get("bonus"), "450:565");
assert.equal(params.get("ench"), "456");
assert.equal(params.get("gems"), "789:0:0:0");
assert.equal(variant.tooltip, "item=123&bonus=450:565&ench=456&gems=789:0:0:0");
assert.equal(new URL(wowheadLink({ itemID: 124, itemLink }).href).search, "");
fields[12] = "3";
assert.equal(new URL(wowheadLink({ itemID: 123, itemLink: `item:${fields.join(":")}` }).href).searchParams.has("bonus"), false);
fields[12] = "2";
fields[13] = "450&domain=evil";
assert.equal(new URL(wowheadLink({ itemID: 123, itemLink: `item:${fields.join(":")}` }).href).searchParams.has("domain"), false);
assert.equal(new URL(wowheadLink({ itemID: NaN }).href).origin, "https://www.wowhead.com");

enableWowheadTooltips();
enableWowheadTooltips();
assert.equal(scripts.size, 1);
const script = scripts.get("wowhead-tooltip-script");
assert.equal(script.src, "https://wow.zamimg.com/js/tooltips.js");
assert.equal(script.async, true);
assert.equal(script.referrerPolicy, "no-referrer");
assert.equal(context.window.whTooltips.renameLinks, false);
assert.equal(context.window.whTooltips.iconizeLinks, false);
assert.equal(context.window.whTooltips.colorLinks, false);
script.onerror();
assert.equal(script.dataset.unavailable, "true");
console.log("PASS: external item/pet links, exact listing bonuses, malformed input, idempotent optional script and silent load failure.");
