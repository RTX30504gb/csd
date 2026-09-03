import { Draggable } from "./Draggable";

/** The main RUG PULL DETECTOR object — draggable, tilting, glitching. */
export function DraggableTitle() {
  return (
    <Draggable label="RUG PULL DETECTOR title" tiltFactor={0.4}>
      <h1 className="display-type relative inline-block text-[clamp(2.6rem,10vw,7.5rem)]">
        <span
          data-text="RUG PULL"
          className="glitch-text block acid-glow"
          aria-hidden="true"
        >
          RUG PULL
        </span>
        <span className="sr-only">RUG PULL DETECTOR</span>
        <span
          className="block text-foreground"
          style={{
            WebkitTextStroke: "2px var(--color-acid)",
            color: "transparent",
          }}
          aria-hidden="true"
        >
          DETECTOR
        </span>
        <span className="absolute -right-2 top-0 rotate-90 border border-acid-dim px-1 py-0.5 font-mono text-[9px] tracking-[0.3em] text-acid-dim">
          v0.9.4-BETA
        </span>
      </h1>
    </Draggable>
  );
}

/** Small satellite label, positioned absolutely by the caller. */
export function DraggableLabel({
  text,
  className = "",
  tone = "acid",
  rotate = 0,
}: {
  text: string;
  className?: string;
  tone?: "acid" | "muted" | "critical";
  rotate?: number;
}) {
  const toneClass =
    tone === "critical"
      ? "border-risk-critical text-risk-critical"
      : tone === "muted"
        ? "border-border text-muted-foreground"
        : "border-acid-dim text-acid";
  return (
    <div className={className} style={{ transform: `rotate(${rotate}deg)` }}>
      <Draggable label={`${text} label`} tiltFactor={0.5}>
        <span
          className={`distort-hover inline-block border bg-background/85 px-2 py-1 font-mono text-[10px] tracking-[0.22em] uppercase ${toneClass}`}
        >
          {text}
        </span>
      </Draggable>
    </div>
  );
}
