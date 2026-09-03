import { Draggable } from "./Draggable";

/** Hidden-in-the-software creator signature. Draggable, glitchy. */
export function Signature() {
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50">
      <div className="pointer-events-auto">
        <Draggable label="MADE BY JARVIS signature" tiltFactor={0.6}>
          <div className="border border-acid-dim bg-background/90 px-2 py-1">
            <span
              data-text="MADE BY JARVIS"
              className="glitch-text block font-mono text-[10px] font-bold tracking-[0.3em] text-acid"
            >
              MADE BY JARVIS
            </span>
            <span className="caret block font-mono text-[8px] tracking-[0.3em] text-muted-foreground">
              ./sig --verify
            </span>
          </div>
        </Draggable>
      </div>
    </div>
  );
}
