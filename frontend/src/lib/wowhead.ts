type TooltipItem = {
  itemID: number;
  itemLink?: string | null;
  petSpeciesID?: number | null;
  battlePetCreatureID?: number | null;
};

type WowheadWindow = Window & {
  whTooltips?: { colorLinks: boolean; iconizeLinks: boolean; renameLinks: boolean };
};

/** Build external links only. No data requests or server-side forwarding. */
export function wowheadLink(item: TooltipItem): { href: string; tooltip: string } {
  if (item.petSpeciesID && Number.isSafeInteger(item.petSpeciesID) && item.petSpeciesID > 0) {
    return { href: `https://www.wowhead.com/cn/battle-pet/${item.petSpeciesID}`, tooltip: "" };
  }
  if (item.battlePetCreatureID && Number.isSafeInteger(item.battlePetCreatureID) && item.battlePetCreatureID > 0) {
    return { href: `https://www.wowhead.com/cn/npc=${item.battlePetCreatureID}`, tooltip: "" };
  }
  const id = Number.isSafeInteger(item.itemID) && item.itemID > 0 ? item.itemID : 0;
  const params: string[] = [];
  const fields = item.itemLink?.match(/(?:Hitem:|^item:)([^|]+)/)?.[1].split(":");
  // Include bonuses only from a full, matching listing link; never infer them.
  if (fields && Number(fields[0]) === id) {
    const count = Number(fields[12]);
    const bonuses = fields.slice(13, 13 + count);
    if (Number.isInteger(count) && count > 0 && count <= 64 && bonuses.length === count && bonuses.every((bonus) => /^\d+$/.test(bonus))) {
      params.push(`bonus=${bonuses.join(":")}`);
    }
    if (/^[1-9]\d*$/.test(fields[1] ?? "")) params.push(`ench=${fields[1]}`);
    const gems = fields.slice(2, 6);
    if (gems.some((gem) => /^[1-9]\d*$/.test(gem)) && gems.every((gem) => gem === "" || /^\d+$/.test(gem))) {
      params.push(`gems=${gems.map((gem) => gem || "0").join(":")}`);
    }
  }
  return {
    href: `https://www.wowhead.com/cn/item=${id}${params.length ? `?${params.join("&")}` : ""}`,
    tooltip: `item=${id}${params.length ? `&${params.join("&")}` : ""}`,
  };
}

/** Optional official widget, loaded by the visitor's browser without blocking React. */
export function enableWowheadTooltips(): void {
  if (document.getElementById("wowhead-tooltip-script")) return;
  (window as WowheadWindow).whTooltips = {
    colorLinks: false,
    iconizeLinks: false,
    renameLinks: false,
  };
  const script = document.createElement("script");
  script.id = "wowhead-tooltip-script";
  script.src = "https://wow.zamimg.com/js/tooltips.js";
  script.async = true;
  script.referrerPolicy = "no-referrer";
  // A blocked script leaves ordinary links and the local market UI intact.
  script.onerror = () => { script.dataset.unavailable = "true"; };
  document.head.appendChild(script);
}
