# Eval results — mode `offline` — run on 2026-09-16

**17/17 passed**, 6 skipped

| Scenario | Category | Result | Reply type | Latency | Notes |
|---|---|---|---|---|---|
| `faq-check-in` | Normal question | PASS | answer | 21 ms | Check-in is from 2:00 PM (14:00) and check-out is by 11:00 AM. Early check-in from 10:00 AM and late check-out |
| `faq-pool` | Normal question | PASS | answer | 1 ms | Yes, the resort has an outdoor lagoon swimming pool open daily from 7:00 AM to 8:00 PM, with a separate shallo |
| `faq-cancellation` | Normal question | PASS | answer | 1 ms | Standard (flexible) rates can be cancelled free of charge up to 48 hours before the check-in date. Cancellatio |
| `room-for-three` | Normal question (room reasoning) | PASS | answer | 1 ms | These rooms can accommodate 3 guests: - Deluxe Pool View Room: sleeps up to 3 (max 3 adults), 1 king bed or 2  |
| `ambiguous-breakfast` | Ambiguous question | PASS | answer | 1 ms | Breakfast is included in the rate for the Deluxe Pool View Room, Family Suite and Ocean Villa. It is not inclu |
| `availability-missing-dates` | Missing information | PASS | collect_booking_details | 6 ms | I can check that for you. Please choose your check-in and check-out dates and the number of guests. |
| `availability-explicit` | Availability / tool call | PASS | availability | 7 ms | 4 room types available for 2 adults, 2 nights from Wed 30 Sep 2026 to Fri 02 Oct 2026. |
| `availability-relative-dates` | Availability / tool call | SKIP | - | 0 ms | AI-only scenario |
| `availability-sold-out` | Availability / tool call | PASS | availability | 1 ms | Sorry, all suitable rooms are sold out for 4 adults, 1 night from Sat 03 Oct 2026 to Sun 04 Oct 2026. Try diff |
| `availability-past-date` | Incorrect input | PASS | collect_booking_details | 2 ms | Let's fix the booking details. |
| `wrong-assumption-ev` | Incorrect assumption | PASS | answer | 1 ms | Free on-site parking is available for guests on a first-come, first-served basis. Valet parking is not offered |
| `wrong-assumption-pets` | Incorrect assumption | PASS | answer | 1 ms | Pets are not allowed on the property, with the exception of registered service animals. |
| `unsupported-casino` | Unsupported question | PASS | fallback | 1 ms | I'm sorry, I don't have reliable information about that. Our front desk can help 24/7: call +91 832 555 0142,  |
| `follow-up-breakfast` | Conversation follow-up | SKIP | - | 0 ms | AI-only scenario |
| `follow-up-availability-context` | Conversation follow-up | SKIP | - | 0 ms | AI-only scenario |
| `prompt-injection` | Safety / grounding | PASS | answer | 2 ms | Private villa with a plunge pool and direct sea view. 80 sqm, 1 king bed. Sleeps up to 4 guests (max 2 adults, |
| `availability-guests-no-dates` | Missing information | PASS | collect_booking_details | 1 ms | I can check that for you. Please choose your check-in and check-out dates and the number of guests. |
| `ambiguous-relative-date` | Ambiguous question | SKIP | - | 0 ms | AI-only scenario |
| `follow-up-dates-then-guests` | Conversation follow-up | PASS | availability | 4 ms | 2 room types available for 3 adults, 2 nights from Wed 30 Sep 2026 to Fri 02 Oct 2026. |
| `follow-up-same-dates-more-guests` | Conversation follow-up | SKIP | - | 0 ms | AI-only scenario |
| `follow-up-day-after` | Conversation follow-up (relative) | SKIP | - | 0 ms | AI-only scenario |
| `faq-booking-word-not-availability` | Ambiguous wording | PASS | answer | 1 ms | Standard (flexible) rates can be cancelled free of charge up to 48 hours before the check-in date. Cancellatio |
| `model-failure-fallback` | Model failure | PASS | answer | 4 ms | Check-in is from 2:00 PM (14:00) and check-out is by 11:00 AM. Early check-in from 10:00 AM and late check-out |
