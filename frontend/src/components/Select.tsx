import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";
import "./select.css";

type SelectOption<Value extends string | number> = { value: Value; label: string };

type SelectProps<Value extends string | number> = {
  label: string;
  prefix: string;
  value: Value;
  options: readonly SelectOption<Value>[];
  icon?: ReactNode;
  appearance?: "field" | "filter";
  onChange: (value: Value) => void;
};

/** Shared, theme-aware select. The portal keeps menus outside table clipping. */
export function Select<Value extends string | number>({
  label, prefix, value, options, icon, appearance = "field", onChange,
}: SelectProps<Value>) {
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [position, setPosition] = useState<CSSProperties>({ position: "fixed", visibility: "hidden" });
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
  const selected = options[selectedIndex];

  function openMenu(index = selectedIndex) {
    setActiveIndex(index);
    setOpen(true);
  }

  function choose(index: number) {
    const option = options[index];
    if (!option) return;
    onChange(option.value);
    setOpen(false);
    triggerRef.current?.focus({ preventScroll: true });
  }

  useLayoutEffect(() => {
    if (!open) return;
    function placeMenu() {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const viewport = window.visualViewport;
      const viewportTop = viewport?.offsetTop ?? 0;
      const viewportBottom = viewportTop + (viewport?.height ?? window.innerHeight);
      if (rect.bottom < viewportTop || rect.top > viewportBottom) { setOpen(false); return; }
      const width = Math.min(Math.max(rect.width, 208), window.innerWidth - 24);
      const idealHeight = options.length * 44 + 50;
      const below = viewportBottom - rect.bottom - 20;
      const above = rect.top - viewportTop - 20;
      const aboveTrigger = below < idealHeight && above > below;
      const maxHeight = Math.max(44, Math.min(idealHeight, aboveTrigger ? above : below));
      setPosition({
        position: "fixed",
        width,
        left: Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12)),
        top: aboveTrigger ? rect.top - maxHeight - 8 : rect.bottom + 8,
        maxHeight,
      });
    }
    placeMenu();
    window.addEventListener("resize", placeMenu);
    window.addEventListener("scroll", placeMenu, true);
    window.visualViewport?.addEventListener("resize", placeMenu);
    return () => {
      window.removeEventListener("resize", placeMenu);
      window.removeEventListener("scroll", placeMenu, true);
      window.visualViewport?.removeEventListener("resize", placeMenu);
    };
  }, [open, options.length]);

  useEffect(() => {
    if (!open) return;
    function dismiss(event: PointerEvent | FocusEvent) {
      const target = event.target as Node | null;
      if (!triggerRef.current?.contains(target) && !popupRef.current?.contains(target)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", dismiss, true);
    document.addEventListener("focusin", dismiss);
    return () => {
      document.removeEventListener("pointerdown", dismiss, true);
      document.removeEventListener("focusin", dismiss);
    };
  }, [open]);

  useEffect(() => {
    const popup = popupRef.current;
    const option = document.getElementById(`${id}-option-${activeIndex}`);
    if (!open || !popup || !option) return;
    // Scroll only the menu; scrollIntoView can move the entire market page.
    const top = option.offsetTop;
    const bottom = top + option.offsetHeight;
    if (top < popup.scrollTop) popup.scrollTop = top;
    else if (bottom > popup.scrollTop + popup.clientHeight) popup.scrollTop = bottom - popup.clientHeight;
  }, [open, activeIndex, id]);

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    if (event.key === "Escape") {
      if (open) { event.preventDefault(); event.stopPropagation(); setOpen(false); }
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      if (!open) openMenu();
      else setActiveIndex((current) => (current + direction + options.length) % options.length);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      openMenu(event.key === "Home" ? 0 : options.length - 1);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) choose(activeIndex);
      else openMenu();
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const index = options.findIndex((option) => option.label.toLocaleLowerCase().startsWith(event.key.toLocaleLowerCase()));
      if (index !== -1) { event.preventDefault(); openMenu(index); }
    }
  }

  return (
    <div className={`ui-select ${appearance === "filter" ? "ui-select-filter" : ""}`} data-active={appearance === "filter" && value !== "" ? "true" : undefined}>
      <button
        ref={triggerRef}
        className="ui-select-trigger"
        type="button"
        role="combobox"
        aria-label={`${label}：${selected?.label ?? ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-listbox` : undefined}
        aria-activedescendant={open ? `${id}-option-${activeIndex}` : undefined}
        onClick={() => open ? setOpen(false) : openMenu()}
        onKeyDown={handleKeyDown}
      >
        {icon && <span className="ui-select-icon" aria-hidden="true">{icon}</span>}
        <span className="ui-select-prefix">{prefix}</span>
        <span className="ui-select-value">{appearance === "filter" && selected?.value === "" ? "不限" : selected?.label}</span>
        <ChevronDown className="ui-select-chevron" size={16} aria-hidden="true" />
      </button>
      {open && createPortal(
        <div ref={popupRef} className="ui-select-popover" style={position}>
          <div className="ui-select-caption" aria-hidden="true">{label}</div>
          <div id={`${id}-listbox`} role="listbox" aria-label={label}>
            {options.map((option, index) => (
              <div
                key={option.value}
                id={`${id}-option-${index}`}
                role="option"
                aria-selected={option.value === value}
                className={`ui-select-option${index === activeIndex ? " is-active" : ""}`}
                onPointerMove={() => setActiveIndex(index)}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => choose(index)}
              >
                <span>{option.label}</span>
                {option.value === value && <Check size={16} strokeWidth={2.5} aria-hidden="true" />}
              </div>
            ))}
          </div>
        </div>, document.body,
      )}
    </div>
  );
}
