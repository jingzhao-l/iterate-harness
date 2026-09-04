import React, {useDeferredValue, useEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useApp, useInput} from 'ink';

import {CommandPicker} from './components/CommandPicker.js';
import {ConversationView} from './components/ConversationView.js';
import {IterateResumePanel} from './components/IterateResumePanel.js';
import {ModalHost} from './components/ModalHost.js';
import {PromptInput} from './components/PromptInput.js';
import {ReviewProgressPanel} from './components/ReviewProgressPanel.js';
import {SelectModal, type SelectOption} from './components/SelectModal.js';
import {Spinner} from './components/Spinner.js';
import {StatusBar} from './components/StatusBar.js';
import {SwarmPanel} from './components/SwarmPanel.js';
import {TodoPanel} from './components/TodoPanel.js';
import {useBackendSession} from './hooks/useBackendSession.js';
import {ThemeProvider, useTheme} from './theme/ThemeContext.js';
import type {FrontendConfig} from './types.js';

const rawReturnSubmit = process.env.ITERATE_FRONTEND_RAW_RETURN === '1';
const HISTORY_LIMIT = 100;
const SHUTDOWN_GRACE_MS = 300;
const scriptedSteps = (() => {
	const raw = process.env.ITERATE_FRONTEND_SCRIPT;
	if (!raw) {
		return [] as string[];
	}
	try {
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : [];
	} catch {
		return [];
	}
})();

const SELECTABLE_COMMANDS = new Set([
	'/provider',
	'/model',
	'/theme',
	'/output-style',
	'/permissions',
	'/resume',
	'/effort',
	'/passes',
	'/turns',
	'/fast',
	'/vim',
	'/voice',
	'/iterate',
]);

type SelectModalState = {
	title: string;
	options: SelectOption[];
	onSelect: (value: string) => void;
} | null;

export function App({config}: {config: FrontendConfig}): React.JSX.Element {
	const initialTheme = String((config as Record<string, unknown>).theme ?? 'default');
	return (
		<ThemeProvider initialTheme={initialTheme}>
			<AppInner config={config} />
		</ThemeProvider>
	);
}

function AppInner({config}: {config: FrontendConfig}): React.JSX.Element {
	const {exit} = useApp();
	const {theme, setThemeName} = useTheme();
	const [input, setInput] = useState('');
	const [modalInput, setModalInput] = useState('');
	const [history, setHistory] = useState<string[]>([]);
	const [historyIndex, setHistoryIndex] = useState(-1);
	const [transientMessage, setTransientMessage] = useState<string | null>(null);
	const [scriptIndex, setScriptIndex] = useState(0);
	const [pickerIndex, setPickerIndex] = useState(0);
	const [selectModal, setSelectModal] = useState<SelectModalState>(null);
	const [selectIndex, setSelectIndex] = useState(0);
	const session = useBackendSession(config, () => exit());
	// Guards for one-shot key flows (raw-return question submit, Ctrl+C exit).
	const questionHandledRef = useRef(false);
	const exitRequestedRef = useRef(false);
	const deferredTranscript = useDeferredValue(session.transcript);
	const deferredAssistantBuffer = useDeferredValue(session.assistantBuffer);
	const deferredStatus = useDeferredValue(session.status);
	const deferredTasks = useDeferredValue(session.tasks);
	const deferredTodoMarkdown = useDeferredValue(session.todoMarkdown);
	const deferredSwarmTeammates = useDeferredValue(session.swarmTeammates);
	const deferredSwarmNotifications = useDeferredValue(session.swarmNotifications);
	const deferredReviewProgress = useDeferredValue(session.reviewProgress);
	const deferredReviewRoundTrend = useDeferredValue(session.reviewRoundTrend);
	const deferredLastLoopState = useDeferredValue(session.lastLoopState);

	useEffect(() => {
		if (!transientMessage) {
			return;
		}
		const timer = setTimeout(() => setTransientMessage(null), 2500);
		return () => clearTimeout(timer);
	}, [transientMessage]);

	// Backend select_prompt modal (iterate pause menu) — reuse the shared
	// selectIndex state; reset whenever a new prompt arrives.
	const backendSelectPrompt =
		session.modal?.kind === 'select_prompt'
			? {
					requestId: String(session.modal.request_id ?? ''),
					title: String(session.modal.question ?? 'Select'),
					options: (Array.isArray(session.modal.options) ? session.modal.options : []).map((o) => {
						const opt = (o ?? {}) as Record<string, unknown>;
						return {
							value: String(opt.value ?? ''),
							label: String(opt.label ?? ''),
							description: opt.description ? String(opt.description) : undefined,
						};
					}),
					cancelValue: String(session.modal.cancel_value ?? ''),
				}
			: null;

	useEffect(() => {
		if (backendSelectPrompt) {
			setSelectIndex(0);
		}
	}, [backendSelectPrompt?.requestId]);

	useEffect(() => {
		const nextTheme = session.status.theme;
		if (typeof nextTheme === 'string' && nextTheme) {
			setThemeName(nextTheme);
		}
	}, [session.status.theme, setThemeName]);

	// Current tool name for spinner
	const currentToolName = useMemo(() => {
		for (let i = deferredTranscript.length - 1; i >= 0; i--) {
			const item = deferredTranscript[i];
			if (item.role === 'tool') {
				return item.tool_name ?? 'tool';
			}
			if (item.role === 'tool_result' || item.role === 'assistant') {
				break;
			}
		}
		return undefined;
	}, [deferredTranscript]);

	// Command hints
	const commandHints = useMemo(() => {
		const value = input.trim();
		if (!value.startsWith('/')) {
			return [] as string[];
		}
		return session.commands.filter((cmd) => cmd.startsWith(value)).slice(0, 10);
	}, [session.commands, input]);

	const showPicker = commandHints.length > 0 && !session.busy && !session.modal && !selectModal;
	// Panels' keyboard shortcuts (ctrl+w / ctrl+t) only fire when no modal or
	// picker is open, so they never steal keys meant for those surfaces.
	const panelInputsActive = !session.modal && !selectModal && !backendSelectPrompt && !showPicker;
	const outputStyle = String(session.status.output_style ?? 'default');

	useEffect(() => {
		setPickerIndex(0);
	}, [commandHints.length, input]);

	// Handle backend-initiated select requests (e.g. /resume session list)
	useEffect(() => {
		if (!session.selectRequest) {
			return;
		}
		const req = session.selectRequest;
		if (req.options.length === 0) {
			session.setSelectRequest(null);
			return;
		}
		const initialIndex = req.options.findIndex((option) => option.active);
		setSelectIndex(initialIndex >= 0 ? initialIndex : 0);
		setSelectModal({
			title: req.title,
			options: req.options.map((o) => ({value: o.value, label: o.label, description: o.description, active: o.active})),
			onSelect: (value) => {
				session.sendRequest({type: 'apply_select_command', command: req.command, value});
				session.setBusy(true);
				setSelectModal(null);
			},
		});
		session.setSelectRequest(null);
	}, [session.selectRequest]);

	// Intercept special commands that need interactive UI
	const handleCommand = (cmd: string): boolean => {
		const trimmed = cmd.trim();

		if (SELECTABLE_COMMANDS.has(trimmed)) {
			session.sendRequest({type: 'select_command', command: trimmed.slice(1)});
			return true;
		}

		// /permissions → show mode picker
		if (trimmed === '/permissions' || trimmed === '/permissions show') {
			session.sendRequest({type: 'select_command', command: 'permissions'});
			return true;
		}

		// /plan → toggle plan mode
		if (trimmed === '/plan') {
			const currentMode = String(session.status.permission_mode ?? 'default');
			if (currentMode === 'plan') {
				session.sendRequest({type: 'submit_line', line: '/plan off'});
			} else {
				session.sendRequest({type: 'submit_line', line: '/plan on'});
			}
			session.setBusy(true);
			return true;
		}

		// /resume → request session list from backend (will trigger select_request)
		if (trimmed === '/resume') {
			session.sendRequest({type: 'select_command', command: 'resume'});
			return true;
		}

		return false;
	};

	useInput((chunk, key) => {
		const isPaste = chunk.length > 1 && !key.ctrl && !key.meta;
		const isEscape = key.escape || chunk === '\u001B';

		// Ctrl+C interrupts a running turn; when idle it exits the TUI.
		if (key.ctrl && chunk === 'c') {
			if (session.busy) {
				session.sendRequest({type: 'interrupt'});
				session.setBusyLabel('Stopping current operation...');
				return;
			}
			if (!exitRequestedRef.current) {
				exitRequestedRef.current = true;
				session.sendRequest({type: 'shutdown'});
				// Give the backend a moment to flush and ack the shutdown
				// request before unmounting, so graceful shutdown is not
				// skipped by an immediate exit.
				setTimeout(() => exit(), SHUTDOWN_GRACE_MS);
			}
			return;
		}

		// Let ink-text-input handle pasted text directly.
		if (isPaste) {
			return;
		}

		// --- Select modal (permissions picker etc.) ---
		if (selectModal) {
			if (key.upArrow) {
				setSelectIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setSelectIndex((i) => Math.min(selectModal.options.length - 1, i + 1));
				return;
			}
			if (key.return) {
				const selected = selectModal.options[selectIndex];
				if (selected) {
					selectModal.onSelect(selected.value);
				}
				return;
			}
			if (key.escape) {
				setSelectModal(null);
				return;
			}
			// Number keys for quick selection
			const num = parseInt(chunk, 10);
			if (num >= 1 && num <= selectModal.options.length) {
				const selected = selectModal.options[num - 1];
				if (selected) {
					selectModal.onSelect(selected.value);
				}
				return;
			}
			return;
		}

		// --- Scripted raw return ---
		// Only handles question-modal submission; normal input is submitted by
		// TextInput's onSubmit (suppressed while the command picker is open) so
		// Enter neither double-submits nor bypasses command completion.
		if (rawReturnSubmit && key.return && !key.shift && !showPicker) {
			if (session.modal?.kind === 'question') {
				questionHandledRef.current = true;
				session.sendRequest({
					type: 'question_response',
					request_id: session.modal.request_id,
					answer: modalInput,
				});
				session.setModal(null);
				setModalInput('');
				return;
			}
			return;
		}

		// --- Permission modal (MUST be before busy check — modal appears while busy) ---
		if (session.modal?.kind === 'permission') {
			if (chunk.toLowerCase() === 'y') {
				session.sendRequest({
					type: 'permission_response',
					request_id: session.modal.request_id,
					allowed: true,
				});
				session.setModal(null);
				return;
			}
			if (chunk.toLowerCase() === 'n' || isEscape) {
				session.sendRequest({
					type: 'permission_response',
					request_id: session.modal.request_id,
					allowed: false,
				});
				session.setModal(null);
				return;
			}
			return;
		}

		// --- Question / MCP auth modal (also appears while busy) ---
		if (session.modal?.kind === 'question' || session.modal?.kind === 'mcp_auth') {
			// Esc cancels/rejects the pending request instead of falling
			// through to the interrupt or idle-Esc branches.
			if (isEscape) {
				session.sendRequest({
					type: 'question_response',
					request_id: session.modal.request_id,
					answer: '',
				});
				session.setModal(null);
				setModalInput('');
				return;
			}
			return; // Let TextInput in ModalHost handle input
		}

		// --- Backend select prompt (iterate pause menu; appears while busy) ---
		if (backendSelectPrompt && backendSelectPrompt.options.length > 0) {
			const answerSelect = (value: string): void => {
				session.sendRequest({
					type: 'question_response',
					request_id: backendSelectPrompt.requestId,
					answer: value,
				});
				session.setModal(null);
			};
			if (key.upArrow) {
				setSelectIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setSelectIndex((i) => Math.min(backendSelectPrompt.options.length - 1, i + 1));
				return;
			}
			if (key.return) {
				const selected = backendSelectPrompt.options[selectIndex];
				if (selected) {
					answerSelect(selected.value);
				}
				return;
			}
			if (key.escape) {
				if (backendSelectPrompt.cancelValue) {
					answerSelect(backendSelectPrompt.cancelValue);
				} else {
					// No explicit cancel value — send an explicit empty cancel
					// instead of accidentally selecting the first option.
					answerSelect('');
				}
				return;
			}
			const num = parseInt(chunk, 10);
			if (num >= 1 && num <= backendSelectPrompt.options.length) {
				const selected = backendSelectPrompt.options[num - 1];
				if (selected) {
					answerSelect(selected.value);
				}
				return;
			}
			return;
		}

		if (session.busy && isEscape) {
			session.sendRequest({type: 'interrupt'});
			session.setBusyLabel('Stopping current operation...');
			return;
		}

		// --- Ignore input while busy ---
		if (session.busy) {
			return;
		}

		// Empty-input Tab cycles the task mode (code ↔ iterate). This replaces
		// the former "empty-input Tab opens the permissions picker" behavior
		// (design §20.6.1); permissions switching moved to the /permissions
		// command. Non-empty input Tab keeps its completion/tab behavior.
		if (!showPicker && key.tab && input.trim() === '') {
			const currentMode = String(session.status.task_mode ?? 'iterate');
			const nextMode = currentMode === 'code' ? 'iterate' : 'code';
			session.sendRequest({type: 'set_task_mode', value: nextMode});
			return;
		}

		// --- Command picker ---
		if (showPicker) {
			if (key.upArrow) {
				setPickerIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setPickerIndex((i) => Math.min(commandHints.length - 1, i + 1));
				return;
			}
			if (key.return) {
				const selected = commandHints[pickerIndex];
				if (selected) {
					setInput('');
					if (!handleCommand(selected)) {
						onSubmit(selected);
					}
				}
				return;
			}
			if (key.tab) {
				const selected = commandHints[pickerIndex];
				if (selected) {
					// Complete to the selected command with no trailing space —
					// the user can hit Enter immediately to run it, or keep
					// typing to add args. The trailing space made it look like
					// Tab was "committing" with a token, which broke the flow.
					setInput(selected);
				}
				return;
			}
			if (isEscape) {
				setInput('');
				setHistoryIndex(-1);
				return;
			}
		}

		if (isEscape) {
			// Single Esc clears the input (with feedback); busy Esc is handled
			// above as an interrupt.
			if (input) {
				setInput('');
				setHistoryIndex(-1);
				setTransientMessage('input cleared');
			}
			return;
		}

		// --- History navigation ---
		if (!showPicker && key.upArrow) {
			const nextIndex = Math.min(history.length - 1, historyIndex + 1);
			if (nextIndex >= 0) {
				setHistoryIndex(nextIndex);
				setInput(history[history.length - 1 - nextIndex] ?? '');
			}
			return;
		}
		if (!showPicker && key.downArrow) {
			const nextIndex = Math.max(-1, historyIndex - 1);
			setHistoryIndex(nextIndex);
			setInput(nextIndex === -1 ? '' : (history[history.length - 1 - nextIndex] ?? ''));
			return;
		}

		// Note: normal Enter submission is handled by TextInput's onSubmit in
		// PromptInput.  Do NOT duplicate it here — that causes double requests.
	});

	const addHistory = (value: string): void => {
		const line = value.trim();
		setHistory((items) => {
			const next = [...items, line];
			if (next.length > HISTORY_LIMIT) {
				next.splice(0, next.length - HISTORY_LIMIT);
			}
			// Drop adjacent duplicates (e.g. repeat /plan toggles).
			const deduped: string[] = [];
			for (const item of next) {
				if (deduped[deduped.length - 1] !== item) {
					deduped.push(item);
				}
			}
			return deduped;
		});
	};

	const onSubmit = (value: string): void => {
		if (session.modal?.kind === 'question') {
			if (questionHandledRef.current) {
				questionHandledRef.current = false;
				return;
			}
			session.sendRequest({
				type: 'question_response',
				request_id: session.modal.request_id,
				answer: value,
			});
			session.setModal(null);
			setModalInput('');
			return;
		}
		if (!value.trim() || session.busy || !session.ready) {
			if (session.busy && value.trim() === '/stop') {
				session.sendRequest({type: 'interrupt'});
				session.setBusyLabel('Stopping current operation...');
				setInput('');
				return;
			}
			// Give feedback instead of silently dropping the submit while busy.
			if (value.trim() && session.busy) {
				setTransientMessage('busy — press esc to stop or type /stop');
			}
			return;
		}
		// Check if it's an interactive command
		if (handleCommand(value)) {
			addHistory(value);
			setHistoryIndex(-1);
			setInput('');
			return;
		}
		session.sendRequest({type: 'submit_line', line: value});
		addHistory(value);
		setHistoryIndex(-1);
		setInput('');
		session.setBusy(true);
	};

	// Scripted automation
	useEffect(() => {
		if (scriptIndex >= scriptedSteps.length) {
			return;
		}
		if (session.busy || session.modal || selectModal) {
			return;
		}
		const step = scriptedSteps[scriptIndex];
		const timer = setTimeout(() => {
			onSubmit(step);
			setScriptIndex((index) => index + 1);
		}, 200);
		return () => clearTimeout(timer);
	}, [scriptIndex, session.busy, session.modal, selectModal]);

	return (
		<Box flexDirection="column" paddingX={1} height="100%">
			{/* Conversation area */}
			<Box flexDirection="column" flexGrow={1}>
				<ConversationView
					items={deferredTranscript}
					assistantBuffer={deferredAssistantBuffer}
					showWelcome={session.ready && outputStyle !== 'compact'}
					outputStyle={outputStyle}
					version={String((config as Record<string, unknown>).version ?? '')}
				/>
			</Box>

			{/* Backend modal (permission confirm, question, mcp auth) */}
			{session.modal ? (
				<ModalHost
					modal={session.modal}
					modalInput={modalInput}
					setModalInput={setModalInput}
					onSubmit={onSubmit}
				/>
			) : null}

			{/* Frontend select modal (permissions picker, etc.) */}
		{/* Frontend select modal takes priority over the backend select prompt
			so the two never stack. */}
		{selectModal ? (
			<SelectModal
				title={selectModal.title}
				options={selectModal.options}
				selectedIndex={selectIndex}
			/>
		) : backendSelectPrompt && backendSelectPrompt.options.length > 0 ? (
			<SelectModal
				title={backendSelectPrompt.title}
				options={backendSelectPrompt.options}
				selectedIndex={selectIndex}
			/>
		) : null}

			{/* Command picker */}
			{showPicker ? (
				<CommandPicker hints={commandHints} selectedIndex={pickerIndex} />
			) : null}

			{/* Todo panel */}
		{session.ready && deferredTodoMarkdown ? (
			<TodoPanel markdown={deferredTodoMarkdown} active={panelInputsActive} />
		) : null}

		{/* Iterate last-run resume hint (hidden once a live loop dashboard appears) */}
		{session.ready && deferredLastLoopState && !deferredReviewProgress ? (
			<IterateResumePanel state={deferredLastLoopState} />
		) : null}

		{/* Iterate review convergence dashboard */}
		{session.ready && deferredReviewProgress ? (
			<ReviewProgressPanel progress={deferredReviewProgress} roundTrend={deferredReviewRoundTrend} />
		) : null}

			{/* Swarm panel */}
			{session.ready && (deferredSwarmTeammates.length > 0 || deferredSwarmNotifications.length > 0) ? (
				<SwarmPanel teammates={deferredSwarmTeammates} notifications={deferredSwarmNotifications} active={panelInputsActive} />
			) : null}

			{/* Status bar (only after backend is ready) */}
			{session.ready ? (
				<StatusBar status={deferredStatus} tasks={deferredTasks} activeToolName={session.busy ? currentToolName : undefined} />
			) : null}

			{/* Input — show loading indicator until backend is ready */}
			{!session.ready ? (
				session.connectError ? (
					<Box flexDirection="column">
						<Text color={theme.colors.error}>{session.connectError}</Text>
					</Box>
				) : (
					<Box>
						<Spinner label="Connecting to backend..." />
					</Box>
				)
			) : session.modal || selectModal ? null : (
				<PromptInput
					busy={session.busy}
					input={input}
					setInput={setInput}
					onSubmit={onSubmit}
					toolName={session.busy ? currentToolName : undefined}
					statusLabel={session.busy ? (session.busyLabel ?? (currentToolName ? `Running ${currentToolName}...` : 'Running agent loop...')) : undefined}
					suppressSubmit={showPicker}
					taskMode={String(session.status.task_mode ?? 'iterate')}
				/>
			)}

			{/* Keyboard hints (only after backend is ready) */}
			{session.ready && !session.modal && !selectModal ? (
				<Box>
					<Text dimColor>
						<Text color={theme.colors.primary}>shift+enter</Text> newline{'  '}
						<Text color={theme.colors.primary}>enter</Text> send{'  '}
						<Text color={theme.colors.primary}>/</Text> commands{'  '}
						<Text color={theme.colors.primary}>tab</Text> mode{'  '}
						<Text color={theme.colors.primary}>{'\u2191\u2193'}</Text> history{'  '}
						<Text color={theme.colors.primary}>{session.busy ? '/stop' : 'esc'}</Text>{' '}
						{session.busy ? 'stop' : 'clear input'}{'  '}
						<Text color={theme.colors.primary}>ctrl+c</Text> {session.busy ? 'stop' : 'exit'}
					</Text>
				</Box>
			) : null}

			{transientMessage ? (
				<Box>
					<Text color={theme.colors.info} dimColor>{transientMessage}</Text>
				</Box>
			) : null}
		</Box>
	);
}
