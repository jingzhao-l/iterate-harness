// Skeleton — lightweight loading placeholders (design §17 UX).
// Renders grey shimmering blocks matching the target shape so pages feel
// snappy while async data is in flight. No layout shift vs. final content.

import type { CSSProperties } from "react";

interface SkeletonProps {
  lines?: number;
  height?: number;
  style?: CSSProperties;
}

export function SkeletonRows({ lines = 3, height = 14, style }: SkeletonProps): React.JSX.Element {
  return (
    <div className="skeleton-stack" aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <div
          key={index}
          className="skeleton"
          style={{ height, width: `${Math.max(40, 100 - index * 18)}%`, ...style }}
        />
      ))}
    </div>
  );
}

export function SkeletonCard({ height = 96 }: { height?: number }): React.JSX.Element {
  return (
    <div className="card skeleton-card" aria-hidden="true">
      <div className="skeleton" style={{ height: 16, width: "40%" }} />
      <div className="skeleton" style={{ height, width: "90%", marginTop: 12 }} />
    </div>
  );
}

export function SkeletonTable({ rows = 5 }: { rows?: number }): React.JSX.Element {
  return (
    <div className="skeleton-table" aria-hidden="true">
      <div className="skeleton skeleton-table-head" style={{ height: 12 }} />
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton" style={{ height: 12, width: "94%" }} />
      ))}
    </div>
  );
}
