import React from 'react';
import {Box, Text} from 'ink';

import type {LastLoopState} from '../types.js';

/**
 * Iterate resume panel (断点续跑画面).
 *
 * Rendered at TUI startup when the project has iterate history: summarizes
 * the last finished loop (mode, verdict, rounds, severity buckets, finding
 * preview) and points at the resume entry points. Hidden automatically once
 * a new loop starts emitting `review_progress` events this session.
 */
function IterateResumePanelInner({state}: {state: LastLoopState}): React.JSX.Element {
	const severity = state.severity;
	const severityText =
		`${severity.critical}c/${severity.high}h/` +
		`${severity.medium}m/${severity.low}l`;
	const preview = Array.isArray(state.preview) ? state.preview : [];

	return (
		<Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} marginTop={1}>
			<Box>
				<Text color="cyan" bold>{'◉ last iterate run '}</Text>
				<Text dimColor>[{state.mode}] </Text>
				<Text dimColor>verdict {state.verdict}</Text>
				<Text dimColor> · round {state.rounds}</Text>
				<Text dimColor> · </Text>
				<Text color={state.totalFindings > 0 ? 'yellow' : 'green'}>
					{state.totalFindings} findings
				</Text>
				<Text dimColor> ({severityText})</Text>
				<Text dimColor> · {state.timestamp}</Text>
			</Box>
			{preview.map((finding, index) => (
				<Box key={index}>
					<Text dimColor>
						{'  '}[{finding.severity}] {finding.file} {finding.dimension}: {finding.summary}
					</Text>
				</Box>
			))}
			{state.lastIntervention ? (
				<Box>
					<Text dimColor>
						{'  '}last intervention: {state.lastIntervention.action} (r{state.lastIntervention.round})
					</Text>
				</Box>
			) : null}
			<Box>
				<Text dimColor>
					{'  '}resume with <Text color="cyan">/iterate resume</Text>
					{'  ·  '}trend with <Text color="cyan">/iterate trend</Text>
				</Text>
			</Box>
		</Box>
	);
}

export const IterateResumePanel = React.memo(IterateResumePanelInner);
