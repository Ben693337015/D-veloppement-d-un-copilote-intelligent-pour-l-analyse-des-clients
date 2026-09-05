const SEGMENT_TONES = {
  Fidèle: "bg-success-bg text-success",
  "À risque": "bg-danger-bg text-danger",
  Occasionnel: "bg-accent-light text-accent-dark",
  Nouveau: "bg-brand-light text-brand",
  "Atypique (B2B/grossiste)": "bg-purple-100 text-purple-700",
};

const NIVEAU_TONES = {
  critique: "bg-danger-bg text-danger",
  attention: "bg-warning-bg text-accent-dark",
  ok: "bg-success-bg text-success",
};

export function SegmentBadge({ segment }) {
  const classes = SEGMENT_TONES[segment] || "bg-ink/5 text-muted";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${classes}`}>
      {segment}
    </span>
  );
}

export function NiveauBadge({ niveau }) {
  const classes = NIVEAU_TONES[niveau] || "bg-ink/5 text-muted";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${classes}`}>
      {niveau}
    </span>
  );
}
