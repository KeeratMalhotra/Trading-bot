import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

/** Number that briefly tints green/red when it changes. No motion, broker-style. */
export function Num({ value, format, className }: { value: number; format: (v: number) => string; className?: string }) {
  const prev = useRef(value);
  const [cls, setCls] = useState("");
  const [k, setK] = useState(0);
  useEffect(() => {
    if (Math.abs(value - prev.current) > Math.abs(prev.current) * 1e-7 + 1e-9) {
      setCls(value > prev.current ? "tick-up" : "tick-down");
      setK((x) => x + 1);
    }
    prev.current = value;
  }, [value]);
  return (
    <span key={k} className={clsx("num", cls, className)}>
      {format(value)}
    </span>
  );
}
