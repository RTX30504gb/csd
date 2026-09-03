import { useEffect, useMemo, useRef, useState } from "react";
import { TokenRow } from "./TokenRow";
import { DraggableLabel } from "./DraggableTitle";
import {
  riskBucket,
  type DetectedToken,
} from "@/lib/tokens";
import { fetchRecentTokens } from "@/lib/api";

type Filter = "all" | "critical" | "suspicious" | "low";

const FILTERS: Filter[] = ["all", "critical", "suspicious", "low"];

export function LiveFeed({
  onCountChange,
}: {
  onCountChange?: (n: number) => void;
}) {
  // Swap this state for API/websocket data — same DetectedToken shape.
  const [tokens, setTokens] = useState<DetectedToken[]>([]);

  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const newestRef = useRef<string | null>(null);

  useEffect(() => {
    if (paused) return;

    const loadTokens = async () => {
      try {
        const data = await fetchRecentTokens(40);
        if (data.length > 0) {
          newestRef.current = data[0].contract_address;
        }
        setTokens(data);
      } catch (e) {
        console.error("LiveFeed polling error:", e);
      }
    };

    loadTokens();
    const id = setInterval(loadTokens, 10000);
    return () => clearInterval(id);
  }, [paused]);



  useEffect(() => {
    onCountChange?.(tokens.length);
  }, [tokens.length, onCountChange]);

  const visible = useMemo(
    () =>
      filter === "all"
        ? tokens
        : tokens.filter((t) => riskBucket(t.risk) === filter),
    [tokens, filter],
  );

  return (
    <section className="relative border border-border bg-card/30">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-acid-dim px-3 py-2 sm:px-4">
        <h2 className="caret min-w-0 truncate font-mono text-[11px] font-bold tracking-[0.3em] text-acid">
          LIVE_TOKEN_FEED
        </h2>
        <div className="flex shrink-0 items-center gap-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`border px-2 py-1 font-mono text-[9px] tracking-[0.2em] uppercase transition-colors ${
                filter === f
                  ? "border-acid bg-accent text-acid"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {f}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setPaused((p) => !p)}
            className="ml-1 border border-border px-2 py-1 font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground hover:border-acid hover:text-acid"
          >
            {paused ? "resume" : "hold"}
          </button>
        </div>
      </div>

      <DraggableLabel
        text="incoming events"
        className="absolute -left-3 top-32 z-20 hidden lg:block"
        rotate={-90}
        tone="muted"
      />

      <ul
        aria-live="polite"
        className="max-h-[62vh] overflow-y-auto overflow-x-hidden"
      >
        {visible.map((t, i) => (
          <TokenRow
            key={t.contract_address + t.detected_at}
            token={t}
            index={visible.length - i}
            isNew={t.contract_address === newestRef.current}
          />
        ))}
        {visible.length === 0 && (
          <li className="px-4 py-10 text-center font-mono text-[11px] tracking-[0.24em] text-muted-foreground">
            NO RECORDS MATCH FILTER
          </li>
        )}
      </ul>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 font-mono text-[10px] tracking-[0.2em] text-muted-foreground sm:px-4">
        <span>
          {paused ? "STREAM HELD" : "STREAM OPEN"} // {visible.length} SHOWN
        </span>
        <span className="text-acid-dim">
          BUFFER {tokens.length}/40 — CLICK ROW TO INSPECT
        </span>
      </div>
    </section>
  );
}
