const integerFormatter = new Intl.NumberFormat("zh-CN");
const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function formatInteger(value?: number | null): string {
  return value == null || !Number.isFinite(value) ? "—" : integerFormatter.format(value);
}

export function formatDate(value?: string | number | null): string {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "未提供" : dateFormatter.format(date);
}

export function formatShortDate(value?: string | number | null): string {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatRelativeTime(value?: string | number | null): string {
  if (!value) return "等待下一份快照";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "时间未知";
  const seconds = Math.round((Date.now() - timestamp) / 1000);
  if (seconds < 60) return "刚刚更新";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前更新`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3_600)} 小时前更新`;
  return `${Math.floor(seconds / 86_400)} 天前更新`;
}

export interface CoinParts {
  gold: number;
  silver: number;
  copper: number;
}

export function splitCopper(value?: number | null): CoinParts | null {
  if (value == null || !Number.isFinite(value) || value <= 0) return null;
  const amount = Math.floor(value);
  return {
    gold: Math.floor(amount / 10_000),
    silver: Math.floor((amount % 10_000) / 100),
    copper: amount % 100,
  };
}

export function qualityClass(quality?: number | null): string {
  const normalized = quality != null && quality >= 0 && quality <= 8 ? quality : 1;
  return `quality-${normalized}`;
}
