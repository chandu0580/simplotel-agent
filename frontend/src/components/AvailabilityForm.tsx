import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import type { AvailabilityRequest, BookingDetails } from '../api/types'
import { addDays, nightsBetween, nightsLabel } from '../format'
import { useI18n } from '../i18n/context'

const MAX_NIGHTS = 30
const MAX_ADULTS = 10
const MAX_CHILDREN = 6

interface Props {
  today: string
  prefill: BookingDetails | null
  serverError?: string | null
  onSubmit: (details: AvailabilityRequest) => Promise<void>
  disabled?: boolean
  autoFocus?: boolean
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function initialDate(value: string | null | undefined, today: string): string {
  return value && value >= today ? value : ''
}

export function AvailabilityForm({ today, prefill, serverError, onSubmit, disabled, autoFocus }: Props) {
  const { t } = useI18n()
  const id = useId()
  const checkInRef = useRef<HTMLInputElement>(null)
  const [checkIn, setCheckIn] = useState(initialDate(prefill?.check_in, today))
  const [checkOut, setCheckOut] = useState(initialDate(prefill?.check_out, today))
  const [adults, setAdults] = useState(clamp(prefill?.adults ?? 2, 1, MAX_ADULTS))
  const [children, setChildren] = useState(clamp(prefill?.children ?? 0, 0, MAX_CHILDREN))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(serverError ?? null)

  useEffect(() => {
    // Move focus into a newly opened form so keyboard and screen-reader users land on the first field.
    if (autoFocus) checkInRef.current?.focus()
  }, [autoFocus])

  const nights = checkIn && checkOut ? nightsBetween(checkIn, checkOut) : 0
  const validationError = !checkIn || !checkOut
    ? null
    : nights <= 0
      ? t('form.errorOrder')
      : nights > MAX_NIGHTS
        ? t('form.errorTooLong', { max: MAX_NIGHTS })
        : null
  const canSubmit = Boolean(checkIn && checkOut) && !validationError && !submitting && !disabled

  function changeCheckIn(value: string) {
    setCheckIn(value)
    if (value && (!checkOut || checkOut <= value)) setCheckOut(addDays(value, 1))
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({ check_in: checkIn, check_out: checkOut, adults, children })
    } catch (err) {
      setError(err instanceof ApiError && err.kind === 'validation' ? err.message : t('error.availability'))
      setSubmitting(false)
    }
  }

  return (
    <form className="booking-form" onSubmit={handleSubmit} aria-label={t('form.label')}>
      <div className="booking-form__dates">
        <label htmlFor={`${id}-in`}>
          {t('form.checkIn')}
          <input
            ref={checkInRef}
            id={`${id}-in`}
            type="date"
            min={today}
            value={checkIn}
            onChange={(e) => changeCheckIn(e.target.value)}
            required
          />
        </label>
        <label htmlFor={`${id}-out`}>
          {t('form.checkOut')}
          <input
            id={`${id}-out`}
            type="date"
            min={checkIn ? addDays(checkIn, 1) : addDays(today, 1)}
            value={checkOut}
            onChange={(e) => setCheckOut(e.target.value)}
            required
          />
        </label>
      </div>

      <div className="booking-form__guests">
        <Stepper label={t('form.adults')} value={adults} min={1} max={MAX_ADULTS} onChange={setAdults} />
        <Stepper label={t('form.children')} value={children} min={0} max={MAX_CHILDREN} onChange={setChildren} />
      </div>

      {(validationError || error) && (
        <p className="form-error" role="alert">
          {validationError ?? error}
        </p>
      )}

      <button type="submit" className="btn btn--primary" disabled={!canSubmit}>
        {submitting
          ? t('form.checking')
          : nights > 0 && !validationError
            ? t('form.submitNights', { nights: nightsLabel(t, nights) })
            : t('form.submit')}
      </button>
    </form>
  )
}

interface StepperProps {
  label: string
  value: number
  min: number
  max: number
  onChange: (value: number) => void
}

function Stepper({ label, value, min, max, onChange }: StepperProps) {
  const { t } = useI18n()
  return (
    <div className="stepper" role="group" aria-label={label}>
      <span className="stepper__label">{label}</span>
      <div className="stepper__controls">
        <button type="button" onClick={() => onChange(value - 1)} disabled={value <= min} aria-label={t('form.fewer', { label: label.toLowerCase() })}>
          −
        </button>
        <output aria-live="polite">{value}</output>
        <button type="button" onClick={() => onChange(value + 1)} disabled={value >= max} aria-label={t('form.more', { label: label.toLowerCase() })}>
          +
        </button>
      </div>
    </div>
  )
}
