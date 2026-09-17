import { expect, test } from '@playwright/test'

const useAI = process.env.E2E_USE_AI === 'true'

/** A Wednesday at least two weeks out, so the search avoids weekend sell-outs and past-date errors. */
function upcomingWednesday(): { checkIn: string; checkOut: string } {
  const date = new Date()
  date.setDate(date.getDate() + 14)
  while (date.getDay() !== 3) date.setDate(date.getDate() + 1)
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const out = new Date(date)
  out.setDate(out.getDate() + 2)
  return { checkIn: iso(date), checkOut: iso(out) }
}

/** The conversation lives at #chat; the landing page is what a guest sees at /. */
async function openChat(page: import('@playwright/test').Page) {
  await page.goto('/#chat')
}

async function ask(page: import('@playwright/test').Page, text: string) {
  await page.getByLabel('Ask a question').fill(text)
  await page.getByRole('button', { name: 'Send message' }).click()
}

test('guest asks questions, follows up, and checks availability end to end', async ({ page }) => {
  await openChat(page)
  await expect(page.getByRole('heading', { name: 'The Palm Grove Resort' })).toBeVisible()

  // 1. Property question goes through the backend and is answered with a source.
  const chatResponse = page.waitForResponse((r) => r.url().endsWith('/messages') && r.request().method() === 'POST')
  await ask(page, 'What time is check-in?')
  expect((await chatResponse).status()).toBe(200)
  const log = page.getByRole('log', { name: 'Conversation' })
  await expect(log.getByText(/2:00 PM/).first()).toBeVisible()
  await expect(log.getByText(/Based on:/).first()).toBeVisible()
  await expect(page.getByText(useAI ? 'AI assistant' : 'FAQ mode')).toBeVisible()

  // 2. Follow-up question in the same conversation.
  await ask(page, 'Does the hotel have a swimming pool?')
  await expect(log.getByText(/7:00 AM to 8:00 PM/).first()).toBeVisible()

  // 3. Availability via the booking form.
  await page.getByRole('button', { name: /Check availability/ }).click()
  const form = page.getByRole('form', { name: 'Check availability' })
  const { checkIn, checkOut } = upcomingWednesday()
  await form.getByLabel('Check-in').fill(checkIn)
  await form.getByLabel('Check-out').fill(checkOut)
  await form.getByRole('button', { name: 'More adults' }).click()
  await form.getByRole('button', { name: 'Check 2 nights' }).click()

  const results = page.getByRole('region', { name: 'Availability results' })
  await expect(results.getByText('Deluxe Pool View Room')).toBeVisible()
  await expect(results.getByText('Family Suite')).toBeVisible()
  await expect(results.getByText('Garden Standard Room')).not.toBeVisible() // sleeps 2, party is 3 adults
  await expect(results.getByText('2 nights · 3 adults')).toBeVisible()
})

test('shows an error when the backend call fails and recovers on retry', async ({ page }) => {
  await openChat(page)
  await page.route('**/api/v1/hotels/*/conversations/*/messages', (route) => route.abort('connectionrefused'))

  await ask(page, 'What is the cancellation policy?')
  const alert = page.getByRole('alert')
  await expect(alert).toContainText("couldn't reach the hotel assistant")

  await page.unroute('**/api/v1/hotels/*/conversations/*/messages')
  await alert.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByText(/48 hours before the check-in date/).first()).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('asks for booking details when availability is requested without dates', async ({ page }) => {
  await openChat(page)
  await ask(page, 'Do you have rooms available?')
  await expect(page.getByRole('form', { name: 'Check availability' })).toBeVisible()
})

test('landing: quick action answers, follow-up keeps context', async ({ page }) => {
  await openChat(page)

  // Landing state: value proposition, quick actions and examples, with the input ready.
  await expect(page.getByText('Your stay, made easier')).toBeVisible()
  const actions = page.getByRole('group', { name: 'Quick actions' })
  await expect(actions.getByRole('button', { name: /Rooms/ })).toBeVisible()
  await expect(page.getByLabel('Ask a question')).toBeEnabled()

  await actions.getByRole('button', { name: /Breakfast/ }).click()

  const log = page.getByRole('log', { name: 'Conversation' })
  // The same text also appears as a suggestion chip once the answer arrives, so target the guest's bubble.
  await expect(log.getByText('Is breakfast included?').first()).toBeVisible()
  await expect(log.getByText(/Deluxe Pool View Room/).first()).toBeVisible() // grounded breakfast answer
  await expect(page.getByText('Your stay, made easier')).toHaveCount(0) // landing gave way to the conversation

  await ask(page, 'Which room is best for 3 guests?')
  await expect(log.getByText(/Deluxe Pool View Room|Family Suite/).last()).toBeVisible()
  await expect(page.getByRole('form', { name: 'Check availability' })).toHaveCount(0)
})

test('conversational questions never open the availability form, but asking for rooms does', async ({ page }) => {
  await openChat(page)
  const log = page.getByRole('log', { name: 'Conversation' })

  await ask(page, 'hi')
  await expect(log.getByText(/welcome to The Palm Grove Resort/i)).toBeVisible()
  await expect(page.getByRole('form', { name: 'Check availability' })).toHaveCount(0)

  await ask(page, 'How can you help me?')
  // The suggestion chip carries the same words, so assert on the assistant's own reply.
  await expect(log.getByText(/I can help with rooms, amenities/)).toBeVisible()
  await expect(log.getByText(/couldn't find|don't have reliable information/)).toHaveCount(0)
  await expect(page.getByRole('form', { name: 'Check availability' })).toHaveCount(0)

  await ask(page, 'thanks')
  await expect(log.getByText(/You're welcome/)).toBeVisible()
  await expect(page.getByRole('form', { name: 'Check availability' })).toHaveCount(0)

  // Availability intent: the form appears.
  await ask(page, 'Do you have rooms available?')
  await expect(page.getByRole('form', { name: 'Check availability' })).toBeVisible()
})


test('landing page: rooms, questions and the way into the conversation', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'The Palm Grove Resort', level: 1 })).toBeVisible()
  await expect(page.getByText('A beachside retreat in Candolim, North Goa')).toBeVisible()
  await expect(page.getByText(/Check-in from 2:00\s*[ap]m/i)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Garden Standard Room' })).toBeVisible()
  await expect(page.getByText('Indicative rates', { exact: false })).toBeVisible()
  await expect(page.getByLabel('Ask a question')).toHaveCount(0) // no composer on the landing page

  await page.getByRole('button', { name: 'Chat with assistant' }).first().click()

  await expect(page).toHaveURL(/#chat$/)
  await expect(page.getByLabel('Ask a question')).toBeEnabled()
  await expect(page.getByText('Your stay, made easier')).toBeVisible()

  await page.getByRole('button', { name: 'Back to home' }).click()
  await expect(page.getByRole('heading', { name: 'Garden Standard Room' })).toBeVisible()
})

test('landing page: a suggested question opens the conversation and asks it', async ({ page }) => {
  await page.goto('/')

  await page.getByRole('button', { name: 'What time is check-in?' }).click()

  const log = page.getByRole('log', { name: 'Conversation' })
  await expect(log.getByText('What time is check-in?').first()).toBeVisible()
  await expect(log.getByText(/2:00 PM/).first()).toBeVisible()
})
