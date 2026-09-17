import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { useI18n } from '../i18n/context'
import { IconCalendar, IconSend } from './icons'

const MAX_CHARS = 1000

interface Props {
  pending: boolean
  onSend: (text: string) => void
  onOpenBookingForm: () => void
}

export function Composer({ pending, onSend, onOpenBookingForm }: Props) {
  const { t } = useI18n()
  const [text, setText] = useState('')
  const trimmed = text.trim()
  const canSend = trimmed.length > 0 && !pending

  function submit(event?: FormEvent) {
    event?.preventDefault()
    if (!canSend) return
    onSend(trimmed)
    setText('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      {/* One bar: the date shortcut and send sit inside the field, so the input is the widest thing on the row. */}
      <div className="composer__bar">
        <button
          type="button"
          className="icon-btn composer__availability"
          onClick={onOpenBookingForm}
          disabled={pending}
          aria-label={t('composer.checkAvailability')}
          title={t('composer.checkAvailability')}
        >
          <IconCalendar />
        </button>
        <label htmlFor="composer-input" className="visually-hidden">
          {t('composer.label')}
        </label>
        <textarea
          id="composer-input"
          rows={1}
          value={text}
          maxLength={MAX_CHARS}
          placeholder={t('composer.placeholder')}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button type="submit" className="icon-btn icon-btn--send" disabled={!canSend} aria-label={t('composer.sendLabel')}>
          {pending ? <span className="icon-btn__spinner" aria-hidden="true" /> : <IconSend />}
        </button>
      </div>
      {text.length > MAX_CHARS * 0.8 && (
        <p className="composer__count" aria-live="polite">
          {text.length}/{MAX_CHARS}
        </p>
      )}
    </form>
  )
}
