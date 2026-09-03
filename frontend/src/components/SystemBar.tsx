import { useEffect, useState } from "react";

function Stat({
  k,
  v,
  tone = "acid",
}: {
  k: string;
  v: string;
  tone?: "acid" | "muted";
}) {
  return (
    <span className="flex shrink-0 items-center gap-1.5 font-mono text-[10px] tracking-[0.18em] uppercase">
      <span className="text-muted-foreground">{k}:</span>
      <span className={tone === "acid" ? "text-acid" : "text-foreground"}>
        {v}
      </span>
    </span>
  );
}

export function SystemBar({ scanned }: { scanned: number }) {
  const [clock, setClock] = useState("--:--:--");

  useEffect(() => {
    const tick = () => setClock(new Date().toISOString().slice(11, 19));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="sticky top-0 z-40 border-b border-acid-dim bg-background/95 backdrop-blur-[2px]">
      <div className="mx-auto grid max-w-[1400px] grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 sm:px-5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
          <span className="flex shrink-0 items-center gap-2">
            <span
              className="live-dot inline-block h-2 w-2 bg-acid"
              aria-hidden="true"
            />
            <span className="font-mono text-[10px] font-bold tracking-[0.28em] text-acid">
              LIVE
            </span>
          </span>
          <Stat k="system" v="online" />
          <Stat k="network" v="base" />
          <Stat k="scanner" v="active" />
          <Stat k="records" v={String(scanned).padStart(4, "0")} tone="muted" />
        </div>
        <span className="font-mono text-[10px] tracking-[0.2em] text-muted-foreground">
          UTC {clock}
        </span>
      </div>
      <div className="overflow-hidden border-t border-border py-1">
        <div className="ticker-track flex w-max gap-8 font-mono text-[10px] tracking-[0.24em] text-acid-dim">
          {Array.from({ length: 2 }).map((_, i) => (
            <span key={i} className="flex gap-8 whitespace-nowrap">
              <span>MEMPOOL WATCH ENGAGED</span>
              <span>ERC-20 BYTECODE HEURISTICS v4</span>
              <span>DEPLOYER GRAPH CROSS-REFERENCE</span>
              <span>LIQUIDITY LOCK PROBE</span>
              <span>HONEYPOT SIMULATION</span>
              <span>DO NOT TRUST. VERIFY.</span>
            </span>
          ))}
        </div>
      </div>
    </header>
  );
}
