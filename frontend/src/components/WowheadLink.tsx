import type { ReactNode } from "react";
import { wowheadLink } from "../lib/wowhead";

export function WowheadLink({
  item, children, className,
}: {
  item: Parameters<typeof wowheadLink>[0] & { name: string };
  children: ReactNode;
  className?: string;
}) {
  const { href, tooltip } = wowheadLink(item);
  return (
    <a
      className={`wowhead-item-link${className ? ` ${className}` : ""}`}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
      aria-label={`在 Wowhead 查看${item.name}（新窗口）`}
      data-wowhead={tooltip || undefined}
      data-disable-wowhead-touch-tooltip="true"
    >
      {children}
    </a>
  );
}
