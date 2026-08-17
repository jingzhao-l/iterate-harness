// ConvergenceChart — findings-per-round curve as inline SVG
// (aligns with the report's own convergence chart, design §17.5).

interface ConvergenceChartProps {
  values: number[];
  width?: number;
  height?: number;
}

function buildPoints(values: number[], width: number, height: number): string {
  if (values.length === 0) return "";
  const maxValue = Math.max(...values, 1);
  const padX = 28;
  const padY = 18;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const stepX = values.length > 1 ? innerW / (values.length - 1) : 0;
  return values
    .map((value, index) => {
      const x = padX + (values.length > 1 ? index * stepX : innerW / 2);
      const y = padY + innerH - (value / maxValue) * innerH;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function buildArea(points: string, height: number): string {
  if (!points) return "";
  const first = points.split(" ")[0];
  const last = points.split(" ")[points.split(" ").length - 1];
  const firstX = first.split(",")[0];
  const lastX = last.split(",")[0];
  const baseY = height - 18;
  return `${firstX},${baseY} ${points} ${lastX},${baseY}`;
}

export function ConvergenceChart({
  values,
  width = 620,
  height = 200,
}: ConvergenceChartProps): React.JSX.Element {
  const points = buildPoints(values, width, height);
  const area = buildArea(points, height);

  if (values.length === 0) {
    return <div className="empty">尚无收敛数据（先运行一次 iterate review / run）</div>;
  }

  const maxValue = Math.max(...values, 1);

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="findings per round convergence curve"
    >
      {area && <polygon points={area} fill="#2563eb22" />}
      {points && (
        <polyline
          points={points}
          fill="none"
          stroke="#2563eb"
          strokeWidth="2.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
      {values.map((value, index) => {
        const parts = points.split(" ")[index];
        if (!parts) return null;
        const [x, y] = parts.split(",").map(Number);
        return (
          <g key={index}>
            <circle cx={x} cy={y} r="4" fill="#fff" stroke="#2563eb" strokeWidth="2" />
            <text x={x} y={y - 10} textAnchor="middle" fontSize="11" fill="#64748b">
              {value}
            </text>
            <text x={x} y={height - 4} textAnchor="middle" fontSize="11" fill="#94a3b8">
              R{index + 1}
            </text>
          </g>
        );
      })}
      <text x={width - 4} y={height - 4} textAnchor="end" fontSize="11" fill="#94a3b8">
        peak {maxValue}
      </text>
    </svg>
  );
}
