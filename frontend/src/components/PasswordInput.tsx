// Password input with a show/hide (eye) toggle on the right, so users can
// confirm what they typed before submitting. Used by the Login/Register and
// Reset-password pages. `type` is managed internally (toggles "password" /
// "text"); every other <input> prop passes through unchanged.
import { useState } from "react";

type PasswordInputProps = Omit<React.ComponentProps<"input">, "type">;

export function PasswordInput({ disabled, className, ...rest }: PasswordInputProps) {
  const [show, setShow] = useState(false);
  return (
    <div className="pw-wrap">
      <input
        {...rest}
        type={show ? "text" : "password"}
        disabled={disabled}
        className={className ? `pw-input ${className}` : "pw-input"}
      />
      <button
        type="button"
        className="pw-toggle"
        // Prevent the click from stealing focus / moving the caret and from
        // being forwarded to the <input> by the wrapping <label>.
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => setShow((s) => !s)}
        disabled={disabled}
        aria-label={show ? "Hide password" : "Show password"}
      >
        {show ? <EyeOffIcon /> : <EyeIcon />}
      </button>
    </div>
  );
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}
