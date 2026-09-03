import { useCallback, useRef, useState, type ReactNode } from "react";

interface DraggableProps {
  children: ReactNode;
  /** Initial offset in px relative to layout position. */
  initial?: { x: number; y: number };
  /** Degrees of tilt applied per px of horizontal drag velocity. */
  tiltFactor?: number;
  className?: string;
  label?: string;
}

/**
 * Pointer-event based drag wrapper. No dependencies, no layout thrash:
 * the element keeps its document flow position and is only translated.
 */
export function Draggable({
  children,
  initial = { x: 0, y: 0 },
  tiltFactor = 0.35,
  className = "",
  label = "draggable element",
}: DraggableProps) {
  const [pos, setPos] = useState(initial);
  const [tilt, setTilt] = useState(0);
  const [dragging, setDragging] = useState(false);
  const origin = useRef({ px: 0, py: 0, x: 0, y: 0, lastX: 0 });

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
      origin.current = {
        px: e.clientX,
        py: e.clientY,
        x: pos.x,
        y: pos.y,
        lastX: e.clientX,
      };
      setDragging(true);
    },
    [pos],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      const o = origin.current;
      const dx = e.clientX - o.px;
      const dy = e.clientY - o.py;
      const vx = e.clientX - o.lastX;
      o.lastX = e.clientX;
      setPos({ x: o.x + dx, y: o.y + dy });
      setTilt(Math.max(-12, Math.min(12, vx * tiltFactor)));
    },
    [dragging, tiltFactor],
  );

  const end = useCallback(() => {
    setDragging(false);
    setTilt(0);
  }, []);

  const nudge = useCallback((dx: number, dy: number) => {
    setPos((p) => ({ x: p.x + dx, y: p.y + dy }));
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 24 : 8;
    if (e.key === "ArrowLeft") nudge(-step, 0);
    else if (e.key === "ArrowRight") nudge(step, 0);
    else if (e.key === "ArrowUp") nudge(0, -step);
    else if (e.key === "ArrowDown") nudge(0, step);
    else if (e.key === "Escape") setPos(initial);
    else return;
    e.preventDefault();
  };

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${label} — drag with pointer or move with arrow keys`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={onKeyDown}
      className={`touch-none select-none outline-none focus-visible:ring-1 focus-visible:ring-acid ${
        dragging ? "cursor-grabbing z-50" : "cursor-grab"
      } ${className}`}
      style={{
        transform: `translate3d(${pos.x}px, ${pos.y}px, 0) rotate(${tilt}deg)`,
        transition: dragging
          ? "none"
          : "transform 420ms cubic-bezier(0.16, 1, 0.3, 1)",
        willChange: "transform",
      }}
    >
      {children}
    </div>
  );
}
