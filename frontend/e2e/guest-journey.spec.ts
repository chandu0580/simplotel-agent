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

async function ask(page: import('@playwright/test').Page, text: string) {
  await page.getByLabel('Ask a question').fill(text)
  await page.getByRole('button', { name: 'Send message' }).click()
}

test('guest asks questions, follows up, and checks availability end to end', async ({ page }) => {
  await page.goto('/')
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
  await page.goto('/')
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
  await page.goto('/')
  await ask(page, 'Do you have rooms available?')
  await expect(page.getByRole('form', { name: 'Check availability' })).toBeVisible()
})
