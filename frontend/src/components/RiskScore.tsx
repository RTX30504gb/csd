import { riskBucket, type TokenRisk } from "@/lib/tokens";

const TONE = {
  critical: {
    text: "text-risk-critical",
    border: "border-risk-critical",
    bar: "bg-risk-critical",
    tag: "RUG",
  },
  suspicious: {
    text: "text-risk-suspicious",
    border: "border-risk-suspicious",
    bar: "bg-risk-suspicious",
    tag: "SUS",
  },
  low: {
    text: "text-risk-low",
    border: "border-risk-low",
    bar: "bg-risk-low",
    tag: "OK",
  },
  unknown: {
    text: "text-muted-foreground",
    border: "border-border",
    bar: "bg-muted-foreground",
    tag: "???",
  },
} as const;

export function riskTone(risk?: TokenRisk) {
  return TONE[riskBucket(risk)];
}

export function RiskTag({ risk }: { risk?: TokenRisk | undefined }) {
  const tone = riskTone(risk);
  return (
    <span
      className={`inline-block border px-1.5 py-0.5 font-mono text-[10px] font-bold tracking-[0.18em] ${tone.text} ${tone.border}`}
    >
      [{tone.tag}]
    </span>
  );
}

export function RiskScore({ risk }: { risk?: TokenRisk | undefined }) {
  const tone = riskTone(risk);
  const score = risk?.score ?? 0;
  return (
    <div className="flex w-full items-center gap-3 sm:w-44">
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2 font-mono text-[10px] tracking-[0.2em] text-muted-foreground">
          <span>RISK //</span>
          <span className={`text-lg font-bold leading-none ${tone.text}`}>
            {risk ? String(score).padStart(2, "0") : "--"}
          </span>
        </div>
        <div className="mt-1 h-[6px] w-full border border-border bg-background">
          <div
            className={`h-full ${tone.bar}`}
            style={{
              width: `${risk ? score : 0}%`,
              transition: "width 600ms cubic-bezier(0.16,1,0.3,1)",
            }}
          />
        </div>
        <div
          className={`mt-1 truncate font-mono text-[10px] font-bold tracking-[0.24em] ${tone.text}`}
        >
          {risk?.level?.toUpperCase() ?? "UNSCORED"}
        </div>
      </div>
    </div>
  );
}
