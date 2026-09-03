import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { SystemBar } from "@/components/SystemBar";
import { LiveFeed } from "@/components/LiveFeed";
import { Signature } from "@/components/Signature";
import { DraggableLabel, DraggableTitle } from "@/components/DraggableTitle";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Rug Pull Detector — Live ERC-20 Rug Pull Scanner" },
      {
        name: "description",
        content:
          "Real-time surveillance feed of newly deployed ERC-20 tokens, scored for rug pull risk with deployer, liquidity and honeypot heuristics.",
      },
      { property: "og:title", content: "Rug Pull Detector — Live Token Feed" },
      {
        property: "og:description",
        content:
          "Underground blockchain intelligence: newly deployed ERC-20 tokens analyzed live for rug pull risk.",
      },
    ],
  }),
  component: Index,
});

function Index() {
  const [count, setCount] = useState(0);

  return (
    <div className="crt-scanlines crt-vignette relative min-h-screen bg-background">
      <div
        className="noise-layer pointer-events-none fixed inset-0 z-[58]"
        aria-hidden="true"
      />
      <div
        className="grid-floor pointer-events-none fixed inset-0 opacity-40"
        aria-hidden="true"
      />

      <SystemBar scanned={count} />

      <main className="relative z-10 mx-auto max-w-[1400px] px-3 pb-28 pt-8 sm:px-5">
        <section className="relative">
          <DraggableTitle />

          <div className="mt-4 grid gap-4 border-y border-border py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
            <p className="max-w-xl font-mono text-[11px] leading-relaxed tracking-[0.08em] text-muted-foreground">
              PASSIVE OBSERVER ATTACHED TO CHAIN HEAD. EVERY NEW ERC-20
              DEPLOYMENT IS PULLED, DECOMPILED AND SCORED. WE ASSUME MALICE
              UNTIL THE BYTECODE PROVES OTHERWISE.
              <span className="text-acid"> NOT FINANCIAL ADVICE.</span>
            </p>
            <div className="flex flex-wrap gap-2">
              <DraggableLabel text="unaudited" tone="critical" rotate={-3} />
              <DraggableLabel text="drag me" rotate={2} />
              <DraggableLabel text="chain: base" tone="muted" rotate={-1} />
            </div>
          </div>
        </section>

        <div className="mt-8">
          <LiveFeed onCountChange={setCount} />
        </div>

        <section className="mt-6 grid gap-3 sm:grid-cols-3">
          {[
            ["CRITICAL", "risk >= 70 // assume exit scam", "text-risk-critical", "border-risk-critical"],
            ["SUSPICIOUS", "risk 40-69 // mutable controls", "text-risk-suspicious", "border-risk-suspicious"],
            ["LOW", "risk < 40 // no obvious traps", "text-risk-low", "border-risk-low"],
          ].map(([label, desc, text, border]) => (
            <div key={label} className={`border-l-2 ${border} bg-card/30 px-3 py-2`}>
              <p className={`font-mono text-[10px] font-bold tracking-[0.28em] ${text}`}>
                {label}
              </p>
              <p className="mt-1 font-mono text-[10px] tracking-[0.12em] text-muted-foreground">
                {desc}
              </p>
            </div>
          ))}
        </section>

        <footer className="mt-10 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3 font-mono text-[10px] tracking-[0.2em] text-muted-foreground">
          <span>RUG_PULL_DETECTOR // NODE 07 // READ-ONLY</span>
          <span className="caret text-acid-dim">awaiting next block</span>
        </footer>
      </main>

      <Signature />
    </div>
  );
}
