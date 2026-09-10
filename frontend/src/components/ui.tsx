import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  busy?: boolean
}

export function Button({ variant = 'secondary', busy, className, children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      className={`btn btn-${variant}${className ? ` ${className}` : ''}`}
      disabled={disabled || busy}
      {...rest}
    >
      {busy && <Spinner size={14} />}
      {children}
    </button>
  )
}

export function Spinner({ size = 18 }: { size?: number }) {
  return (
    <span
      className="spinner"
      style={{ width: size, height: size, borderWidth: Math.max(2, size / 8) }}
      aria-label="Loading"
      role="status"
    />
  )
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={`card${className ? ` ${className}` : ''}`}>{children}</div>
}

type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger'

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: BadgeTone }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function TextField({
  label,
  hint,
  ...rest
}: { label: string; hint?: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <input className="field-input" {...rest} />
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

export function SelectField({
  label,
  children,
  ...rest
}: { label: string; children: ReactNode } & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <select className="field-input" {...rest}>
        {children}
      </select>
    </label>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>
}

export function InlineError({ children }: { children: ReactNode }) {
  return <div className="inline-error">{children}</div>
}

export function Banner({
  children,
  onDismiss,
}: {
  children: ReactNode
  onDismiss?: () => void
}) {
  return (
    <div className="banner">
      <span>{children}</span>
      {onDismiss && (
        <button type="button" className="banner-dismiss" onClick={onDismiss} aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  )
}
