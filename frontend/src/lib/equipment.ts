import type { EquipmentFilters } from "../types";

export const EMPTY_EQUIPMENT_FILTERS: EquipmentFilters = { difficulty: "", stats: "", socket: "", tertiary: "" };
export const DIFFICULTY_OPTIONS = [
  { value: "", label: "全部难度" }, { value: "4", label: "随机团队" },
  { value: "3", label: "普通" }, { value: "5", label: "英雄" }, { value: "6", label: "史诗" },
];
const SECONDARY = [["32", "暴击"], ["36", "急速"], ["40", "全能"], ["49", "精通"]] as const;
export const STAT_OPTIONS = [{ value: "", label: "全部组合" }, ...SECONDARY.flatMap(([a, an]) =>
  SECONDARY.filter(([b]) => b !== a).map(([b, bn]) => ({ value: `${a}-${b}`, label: `${an} > ${bn}` })))];
export const SOCKET_OPTIONS = [
  { value: "", label: "不限插槽" }, { value: "yes", label: "有棱彩插槽" }, { value: "no", label: "无棱彩插槽" },
];
export const TERTIARY_LABELS: Record<string, string> = { avoidance: "闪避", leech: "吸血", speed: "加速", indestructible: "永不磨损" };
export const TERTIARY_OPTIONS = [
  { value: "", label: "不限额外属性" }, { value: "any", label: "有额外属性" }, { value: "none", label: "无额外属性" },
  ...Object.entries(TERTIARY_LABELS).map(([value, label]) => ({ value, label })),
];

export function readEquipmentFilters(params: URLSearchParams): EquipmentFilters {
  const valid = (key: string, options: Array<{ value: string }>) => options.some(o => o.value === params.get(key)) ? params.get(key)! : "";
  return { difficulty: valid("difficulty", DIFFICULTY_OPTIONS), stats: valid("stats", STAT_OPTIONS),
    socket: valid("socket", SOCKET_OPTIONS), tertiary: valid("tertiary", TERTIARY_OPTIONS) };
}

export function equipmentFilterLabels(filters?: Partial<EquipmentFilters>): string[] {
  if (!filters) return [];
  const groups: Array<[keyof EquipmentFilters, Array<{ value: string; label: string }>]> = [
    ["difficulty", DIFFICULTY_OPTIONS], ["stats", STAT_OPTIONS], ["socket", SOCKET_OPTIONS], ["tertiary", TERTIARY_OPTIONS],
  ];
  return groups.flatMap(([key, options]) => {
    const value = filters[key];
    const option = options.find(o => o.value === value);
    return value && option ? [option.label] : [];
  });
}
