import type { ReactNode } from "react";

interface SegmentedProps<T extends string | number> {
  options: readonly T[];
  value: T;
  onChange: (value: T) => void;
  /** Render a label for an option (defaults to the value itself). */
  label?: (value: T) => ReactNode;
}

/** A row of mutually-exclusive segmented buttons (the Export tempo/nudge/octave
 * controls). Reuses the `.tempo-toggle` / `.seg` styling. */
export function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  label,
}: SegmentedProps<T>) {
  return (
    <div className="tempo-toggle">
      {options.map((opt) => (
        <button
          key={String(opt)}
          className={`seg ${value === opt ? "on" : ""}`}
          onClick={() => onChange(opt)}
        >
          {label ? label(opt) : String(opt)}
        </button>
      ))}
    </div>
  );
}
