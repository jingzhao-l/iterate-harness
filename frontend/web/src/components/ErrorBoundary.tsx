// ErrorBoundary — top-level render-error containment (design §17 UX).
// A crash in any routed page or component shows a recoverable error card
// instead of blanking the whole WebUI. The boundary also exposes a "reload"
// action and logs the error to the console for diagnosis.

import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  message: string;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, message: "" };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, message: error?.message ?? String(error) };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Deliberate logging: a render crash needs an audit trail in the console.
    // eslint-disable-next-line no-console
    console.error("[iterate-webui] page crash:", error, info.componentStack);
  }

  private reset = (): void => {
    this.setState({ hasError: false, message: "" });
  };

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="error-card" role="alert">
        <div className="error-card-icon" aria-hidden="true">
          !
        </div>
        <h2 className="error-card-title">页面渲染出错</h2>
        <p className="error-card-msg">界面在渲染时遇到了未预期的错误，请重试或检查后台日志。</p>
        {this.state.message && (
          <pre className="error-card-detail">{this.state.message}</pre>
        )}
        <div className="error-card-actions">
          <button className="btn primary" onClick={this.reset}>
            重试
          </button>
          <button className="btn" onClick={() => window.location.reload()}>
            刷新页面
          </button>
        </div>
      </div>
    );
  }
}
