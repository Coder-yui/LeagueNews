"use client";

import { Check, ChevronDown } from "lucide-react";
import {
  Children,
  isValidElement,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type OptionHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";

type PublicSelectProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "multiple" | "onChange" | "value"> & {
  children: ReactNode;
  label: string;
};

type PublicSelectOption = {
  disabled: boolean;
  label: string;
  value: string;
};

export function PublicSelect({ children, defaultValue, disabled, id, label, name, required }: PublicSelectProps) {
  const generatedId = useId();
  const controlId = id ?? `public-select-${generatedId}`;
  const labelId = `${controlId}-label`;
  const listboxId = `${controlId}-listbox`;
  const rootRef = useRef<HTMLDivElement>(null);
  const typeaheadRef = useRef("");
  const typeaheadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const options = useMemo<PublicSelectOption[]>(() => Children.toArray(children).flatMap((child) => {
    if (!isValidElement<OptionHTMLAttributes<HTMLOptionElement>>(child) || child.type !== "option") return [];
    return [{
      disabled: Boolean(child.props.disabled),
      label: String(child.props.children ?? ""),
      value: String(child.props.value ?? ""),
    }];
  }), [children]);
  const initialValue = Array.isArray(defaultValue) ? String(defaultValue[0] ?? "") : String(defaultValue ?? "");
  const [selectedValue, setSelectedValue] = useState(initialValue);
  const [activeIndex, setActiveIndex] = useState(() => Math.max(0, options.findIndex((option) => option.value === initialValue)));
  const [open, setOpen] = useState(false);
  const selectedOption = options.find((option) => option.value === selectedValue) ?? options[0];

  useEffect(() => {
    const nextIndex = options.findIndex((option) => option.value === initialValue);
    setSelectedValue(nextIndex >= 0 ? initialValue : (options[0]?.value ?? ""));
    setActiveIndex(Math.max(0, nextIndex));
    setOpen(false);
  }, [initialValue, options]);

  useEffect(() => {
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, []);

  useEffect(() => () => {
    if (typeaheadTimerRef.current) clearTimeout(typeaheadTimerRef.current);
  }, []);

  useEffect(() => {
    if (open) document.getElementById(`${listboxId}-option-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, listboxId, open]);

  const moveActive = (direction: 1 | -1) => {
    if (!options.length) return;
    let next = activeIndex;
    do {
      next = (next + direction + options.length) % options.length;
    } while (options[next]?.disabled && next !== activeIndex);
    setActiveIndex(next);
  };

  const selectOption = (index: number) => {
    const option = options[index];
    if (!option || option.disabled) return;
    setSelectedValue(option.value);
    setActiveIndex(index);
    setOpen(false);
  };

  const openAtSelection = () => {
    const selectedIndex = options.findIndex((option) => option.value === selectedValue);
    setActiveIndex(Math.max(0, selectedIndex));
    setOpen(true);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) openAtSelection();
      else moveActive(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      if (!open) setOpen(true);
      const candidates = options.map((option, index) => ({ option, index })).filter(({ option }) => !option.disabled);
      const target = event.key === "Home" ? candidates[0] : candidates.at(-1);
      if (target) setActiveIndex(target.index);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) selectOption(activeIndex);
      else openAtSelection();
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      typeaheadRef.current += event.key.toLocaleLowerCase();
      if (typeaheadTimerRef.current) clearTimeout(typeaheadTimerRef.current);
      typeaheadTimerRef.current = setTimeout(() => { typeaheadRef.current = ""; }, 500);
      const match = options.findIndex((option) => !option.disabled && option.label.toLocaleLowerCase().startsWith(typeaheadRef.current));
      if (match >= 0) {
        event.preventDefault();
        setActiveIndex(match);
        if (!open) selectOption(match);
      }
    }
  };

  return (
    <div className="public-select-field">
      <span id={labelId}>{label}</span>
      <div className={`public-select-control${open ? " open" : ""}`} ref={rootRef}>
        {name && <input type="hidden" name={name} value={selectedValue} />}
        <button
          id={controlId}
          className="public-select-trigger"
          type="button"
          role="combobox"
          aria-activedescendant={open ? `${listboxId}-option-${activeIndex}` : undefined}
          aria-controls={listboxId}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-labelledby={labelId}
          disabled={disabled}
          onClick={() => open ? setOpen(false) : openAtSelection()}
          onKeyDown={handleKeyDown}
        >
          <span>{selectedOption?.label ?? "请选择"}</span>
          <ChevronDown aria-hidden="true" size={15} strokeWidth={1.8} />
        </button>
        {required && !selectedValue && <input className="public-select-required" tabIndex={-1} required aria-hidden="true" />}
        {open && (
          <div className="public-select-menu" id={listboxId} role="listbox" aria-labelledby={labelId}>
            {options.map((option, index) => (
              <button
                id={`${listboxId}-option-${index}`}
                className={`public-select-option${activeIndex === index ? " active" : ""}`}
                type="button"
                role="option"
                aria-selected={option.value === selectedValue}
                disabled={option.disabled}
                key={`${option.value}-${index}`}
                onClick={() => selectOption(index)}
                onPointerMove={() => setActiveIndex(index)}
              >
                <span>{option.label}</span>
                {option.value === selectedValue && <Check aria-hidden="true" size={14} strokeWidth={1.8} />}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
