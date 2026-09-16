import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { useI18n } from '../i18n/context'

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
      <button type="button" className="btn btn--secondary composer__availability" onClick={onOpenBookingForm} disabled={pending}>
        <span aria-hidden="true">📅</span> {t('composer.checkAvailability')}
      </button>
      <div className="composer__row">
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
        <button type="submit" className="btn btn--primary composer__send" disabled={!canSend} aria-label={t('composer.sendLabel')}>
          {pending ? '…' : t('composer.send')}
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
