import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

/** Smoothly tweens numbers and flashes green/red on change. */
export function AnimatedNumber({
  value,
  format,
  className,
  flash = true,
  duration = 600,
}: {
  value: number;
  format: (v: number) => string;
  className?: string;
  flash?: boolean;
  duration?: number;
}) {
  const [shown, setShown] = useState(value);
  const [dir, setDir] = useState<"" | "up" | "down">("");
  const from = useRef(value);
  const raf = useRef(0);
  const flashKey = useRef(0);

  useEffect(() => {
    const start = performance.now();
    const a = from.current;
    const b = value;
    if (Math.abs(b - a) < 1e-9) return;
    if (flash && Math.abs(b - a) / Math.max(Math.abs(a), 1) > 1e-6) {
      setDir(b > a ? "up" : "down");
      flashKey.current++;
    }
    cancelAnimationFrame(raf.current);
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / duration);
      const e = 1 - Math.pow(1 - k, 3);
      const v = a + (b - a) * e;
      setShown(v);
      from.current = v;
      if (k < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [value, duration, flash]);

  return (
    <span
      key={flashKey.current}
      className={clsx("num rounded-md", dir === "up" && "flash-up", dir === "down" && "flash-down", className)}
    >
      {format(shown)}
    </span>
  );
}
