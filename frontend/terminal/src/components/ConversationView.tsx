import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {MarkdownText} from './MarkdownText.js';
import {ToolCallDisplay} from './ToolCallDisplay.js';
import {WelcomeBanner} from './WelcomeBanner.js';

type ToolPair = readonly [TranscriptItem, TranscriptItem];
type GroupedItem = TranscriptItem | ToolPair;

const MAX_VISIBLE_ITEMS = 40;

function groupToolPairs(items: TranscriptItem[]): GroupedItem[] {
	const result: GroupedItem[] = [];
	let i = 0;
	while (i < items.length) {
		const cur = items[i];
		const next = items[i + 1];
		if (cur.role === 'tool' && next?.role === 'tool_result') {
			result.push([cur, next] as const);
			i += 2;
		} else {
			result.push(cur);
			i++;
		}
	}
	return result;
}

function ConversationViewInner({
	items,
	assistantBuffer,
	showWelcome,
	outputStyle,
	version,
}: {
	items: TranscriptItem[];
	assistantBuffer: string;
	showWelcome: boolean;
	outputStyle: string;
	version?: string;
}): React.JSX.Element {
	const {theme} = useTheme();
	const isCompactStyle = outputStyle === 'compact';
	// Fold older items, but never split a tool/tool_result pair across the
	// fold boundary: back up by one when the slice would start mid-pair.
	let start = Math.max(0, items.length - MAX_VISIBLE_ITEMS);
	if (start > 0 && items[start]?.role === 'tool_result' && items[start - 1]?.role === 'tool') {
		start -= 1;
	}
	const foldedCount = start;
	const visible = items.slice(start);
	const grouped = groupToolPairs(visible);

	return (
		<Box flexDirection="column" flexGrow={1}>
			{showWelcome && items.length === 0 ? <WelcomeBanner version={version} /> : null}

			{foldedCount > 0 ? (
				<Box marginTop={0}>
					<Text dimColor>{`… 更早 ${foldedCount} 条已折叠`}</Text>
				</Box>
			) : null}

			{grouped.map((group, index) => {
				if (Array.isArray(group)) {
					const [toolItem, resultItem] = group as [TranscriptItem, TranscriptItem];
					return (
						<ToolCallDisplay
							key={index}
							item={toolItem}
							resultItem={resultItem}
							outputStyle={outputStyle}
						/>
					);
				}
				return (
					<MessageRow
						key={index}
						item={group as TranscriptItem}
						theme={theme}
						outputStyle={outputStyle}
					/>
				);
			})}

			{assistantBuffer ? (
				isCompactStyle ? (
					<Box flexDirection="row" marginTop={0}>
						<Text>{assistantBuffer}</Text>
					</Box>
				) : (
					<Box marginTop={1} marginBottom={0} flexDirection="column">
						<Text>
							<Text color={theme.colors.success} bold>{theme.icons.assistant}</Text>
						</Text>
						<Box marginLeft={2} flexDirection="column">
							<MarkdownText content={assistantBuffer} />
						</Box>
					</Box>
				)
			) : null}
		</Box>
	);
}

export const ConversationView = React.memo(ConversationViewInner);

function MessageRow({
	item,
	theme,
	outputStyle,
}: {
	item: TranscriptItem;
	theme: ReturnType<typeof useTheme>['theme'];
	outputStyle: string;
}): React.JSX.Element {
	const isCompactStyle = outputStyle === 'compact';

	switch (item.role) {
		case 'user':
			if (isCompactStyle) {
				return (
					<Box marginTop={0}>
						<Text>
							<Text dimColor>{'> '}</Text>
							<Text>{item.text}</Text>
						</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={1} marginBottom={0}>
					<Text>
						<Text color={theme.colors.secondary} bold>{theme.icons.user}</Text>
						<Text>{item.text}</Text>
					</Text>
				</Box>
			);

		case 'assistant':
			if (isCompactStyle) {
				return (
					<Box marginTop={0} marginBottom={0}>
						<Text>{item.text}</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={1} marginBottom={0} flexDirection="column">
					<Text>
						<Text color={theme.colors.success} bold>{theme.icons.assistant}</Text>
					</Text>
					<Box marginLeft={2} flexDirection="column">
						<MarkdownText content={item.text} />
					</Box>
				</Box>
			);

		case 'tool':
		case 'tool_result':
			return <ToolCallDisplay item={item} outputStyle={outputStyle} />;

		case 'system':
			if (isCompactStyle) {
				return (
					<Box marginTop={0}>
						<Text>
							<Text color={theme.colors.warning}>[system]</Text>
							<Text> {item.text}</Text>
						</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={0}>
					<Text>
						<Text color={theme.colors.warning}>{theme.icons.system}</Text>
						<Text color={theme.colors.warning}>{item.text}</Text>
					</Text>
				</Box>
			);

		case 'status':
			return (
				<Box marginTop={0}>
					<Text color={theme.colors.info}>{item.text}</Text>
				</Box>
			);

		case 'log':
			return (
				<Box>
					<Text dimColor>{item.text}</Text>
				</Box>
			);

		default:
			return (
				<Box>
					<Text>{item.text}</Text>
				</Box>
			);
	}
}
