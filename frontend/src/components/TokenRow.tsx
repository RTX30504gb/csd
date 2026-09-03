import { useState } from "react";
import { RiskScore, RiskTag, riskTone } from "./RiskScore";
import {
  clockStamp,
  shortAddress,
  timeAgo,
  type DetectedToken,
} from "@/lib/tokens";

export function TokenRow({
  token,
  index,
  isNew,
}: {
  token: DetectedToken;
  index: number;
  isNew?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const tone = riskTone(token.risk);

  return (
    <li
      className={`group relative border-b border-border bg-card/40 transition-colors hover:bg-accent/40 ${
        isNew ? "feed-enter" : ""
      }`}
    >
      <span
        className={`absolute left-0 top-0 h-full w-[3px] ${tone.bar} opacity-60 group-hover:opacity-100`}
        aria-hidden="true"
      />
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-3 text-left sm:grid-cols-[3rem_minmax(0,1fr)_11rem] sm:gap-5 sm:px-4"
      >
        <span className="hidden font-mono text-[10px] text-muted-foreground sm:block">
          {String(index).padStart(3, "0")}
        </span>

        <span className="flex min-w-0 flex-col gap-1">
          <span className="flex min-w-0 items-center gap-2">
            <RiskTag risk={token.risk} />
            <span className="distort-hover truncate font-mono text-sm font-bold tracking-[0.1em] text-foreground">
              {token.symbol}
            </span>
            <span className="truncate font-mono text-[11px] text-muted-foreground">
              {token.name}
            </span>
          </span>
          <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] text-muted-foreground">
            <span className="text-acid-dim">
              {shortAddress(token.contract_address)}
            </span>
            <span>BLK {token.creation_block}</span>
            <span>{clockStamp(token.detected_at)}</span>
            <span>{timeAgo(token.detected_at)}</span>
          </span>
        </span>

        <RiskScore risk={token.risk} />
      </button>

      {open && (
        <div className="border-t border-border bg-background/70 px-3 py-3 font-mono text-[11px] sm:px-4">
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
            <Field label="CONTRACT" value={token.contract_address} />
            <Field label="DEPLOYER" value={token.deployer} />
            <Field label="SUPPLY" value={token.total_supply} />
            <Field label="DECIMALS" value={String(token.decimals)} />
          </dl>
          <p className="mt-3 text-[10px] tracking-[0.24em] text-muted-foreground">
            FLAGS //
          </p>
          <ul className="mt-1 space-y-0.5">
            {(token.risk?.reasons ?? ["NO ANALYSIS AVAILABLE"]).map((r) => (
              <li key={r} className={`flex gap-2 ${tone.text}`}>
                <span aria-hidden="true">{">"}</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="shrink-0 text-[10px] tracking-[0.2em] text-muted-foreground">
        {label}
      </dt>
      <dd className="truncate text-acid-dim">{value}</dd>
    </div>
  );
}
