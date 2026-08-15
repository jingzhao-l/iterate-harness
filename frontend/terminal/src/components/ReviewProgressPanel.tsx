import React from 'react';
import {Box, Text} from 'ink';

import type {ReviewProgressSnapshot} from '../types.js';

function formatCost(costUsd: number): string {
	if (costUsd >= 1) {
		return `$${costUsd.toFixed(2)}`;
	}
	return `$${costUsd.toFixed(4)}`;
}

/**
 * Iterate review-loop convergence dashboard.
 *
 * Rendered above the status bar while an iterate loop (dry-run review or
 * normal fix loop) emits `review_progress` events. Shows the running round,
 * per-round findings trend, per-dimension counts, cumulative cost, and the
 * convergence badge.
 */
function ReviewProgressPanelInner({
	progress,
	roundTrend,
}: {
	progress: ReviewProgressSnapshot;
	roundTrend: number[];
}): React.JSX.Element {
	const dimensions = Object.entries(progress.perDimension)
		.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
		.slice(0, 6);
	const hasDimensionCost = Object.keys(progress.dimensionCostUsd).length > 0;

	return (
		<Box
			flexDirection="column"
			borderStyle="round"
			borderColor={progress.converged ? 'green' : 'magenta'}
			paddingX={1}
			marginTop={1}
		>
			<Box>
				<Text color={progress.converged ? 'green' : 'magenta'} bold>
					{progress.converged ? '◉ converged ' : '◎ iterating '}
				</Text>
				<Text bold>Iterate Review </Text>
				<Text dimColor>[{progress.mode}]</Text>
				<Text dimColor> round {progress.round}</Text>
				<Text dimColor> · </Text>
				<Text color={progress.newFindings === 0 ? 'green' : 'yellow'}>
					+{progress.newFindings} findings
				</Text>
				<Text dimColor> ({progress.totalFindings} total)</Text>
				<Text dimColor> · {formatCost(progress.costUsd)}</Text>
			</Box>
			{roundTrend.length > 0 ? (
				<Box>
					<Text dimColor>trend </Text>
					{roundTrend.map((count, index) => (
						<Box key={index}>
							{index > 0 ? <Text dimColor>→</Text> : null}
							<Text color={count === 0 ? 'green' : 'yellow'}>{count}</Text>
						</Box>
					))}
				</Box>
			) : null}
			{dimensions.length > 0 ? (
				<Box>
					{dimensions.map(([dimension, count], index) => {
						const costUsd = progress.dimensionCostUsd[dimension];
						return (
							<Box key={dimension}>
								{index > 0 ? <Text dimColor> · </Text> : null}
								<Text dimColor>{dimension} </Text>
								<Text color={count > 0 ? 'yellow' : 'green'}>{count}</Text>
								{hasDimensionCost && costUsd != null ? (
									<Text dimColor> ~{formatCost(costUsd)}</Text>
								) : null}
							</Box>
						);
					})}
				</Box>
			) : null}
		</Box>
	);
}

export const ReviewProgressPanel = React.memo(ReviewProgressPanelInner);
