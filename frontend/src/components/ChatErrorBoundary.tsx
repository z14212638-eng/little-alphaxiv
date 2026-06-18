// Error boundary so a render error in the chat (e.g. a malformed message
// from an unusual provider) doesn't unmount the whole app and "lose" the
// input box. Shows a recoverable error card instead.

import React from "react";

interface State {
  error: Error | null;
}

export class ChatErrorBoundary extends React.Component<
  { children: React.ReactNode },
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // eslint-disable-next-line no-console
    console.error("[ChatErrorBoundary]", error);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="chat-error-boundary">
          <div className="msg msg-assistant">
            ⚠️ Something went wrong rendering this conversation:
            <pre>{String(this.state.error.message || this.state.error)}</pre>
          </div>
          <button
            className="link-btn"
            onClick={() => this.setState({ error: null })}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
