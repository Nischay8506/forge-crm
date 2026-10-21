# Approval inbox: 15-minute usability test

F12.3's Done-When is "every card decidable in under 60 seconds". Claude's automated browser check
passes (median 3 seconds), but a script reads fast. Decision H8 C and Step 5 safeguard 7 keep the
Done-When **pending until real people** have used the inbox. This page is the whole test.

**Pass:** across all sessions, the **median decision time is under 60 seconds**, and no participant
was stuck on a card (gave up, or asked what a card meant before deciding).

## Before each session (5 minutes, the facilitator)

1. Start Forge (HANDOVER section 4): Postgres and Redis, the API, the worker, the frontend.
2. Make a fresh company for this participant (one per person, so their numbers are their own):
   ```bash
   cd backend
   uv run python -m app.cli seed-usability --slug ut-<name>      # e.g. ut-priya
   ```
   It prints the logins. Everyone's password is `Usability-2026` (change it with `--password`).
   The company has 11 waiting cards: payments with and without ERP figures, a two-step card (Sales
   Manager then Finance), recommendations to approve, reject and counter, one card near its deadline,
   one past it, and one the participant asked for themselves.
3. Run it **just before** the participant starts. One customer's ERP figures count as current for
   15 minutes; to make them current again: `uv run python -m app.cli seed-usability --slug ut-<name> --refresh`.
4. For the phone part: put `VITE_API_URL=/api/v1` in `frontend/.env`, start the frontend with
   `npm run dev -- --host`, and open `http://<PC address>:5173` on a phone on the same Wi-Fi (Vite
   prints the address as "Network"). Windows may ask to allow Node through the firewall: allow it on
   private networks. Push notifications need HTTPS, so they work on the PC (`localhost`) but not on a
   phone at a plain `http://` address; the test does not need them.

Nothing in the test company is real: the payments go to the Sandbox Agent's own table and the ERP
connections are paused, so Forge never calls an ERP.

## The session (15 minutes)

Say: *"Forge's agents ask a person before doing certain things. These cards are those requests. Decide
each one as you would at work. There are no wrong answers; we are testing the screen, not you.
Please think aloud."* Do not explain the cards. Note where they hesitate.

Sign the participant in as `tester@ut-<name>.example.com`, company `ut-<name>`.

| # | Task | Watch for |
|---|---|---|
| 1 | On the PC: open **Decisions** and decide every card you can. | Do they find the reason, the history and the recommendation without help? |
| 2 | On one card, change the amount instead of approving it as asked (**Counter…**). | Do they understand the counter may need another approver? |
| 3 | Hand one card to Rahul Finance (**Hand to…**). | |
| 4 | Find the card you cannot decide, and say why. | It says a second person must decide it, because they asked for it. |
| 5 | On the phone: sign in, open a card and decide it. | Can they read and tap everything without zooming? |
| 6 | Optional: on **Away and hand-overs**, set an out-of-office for next week. | Do they understand the card types and the maximum amount? |

Afterwards ask: *"Was any card hard to decide? What was missing?"* Write down the answers.

## Reading the results (2 minutes)

Sign in as `admin@ut-<name>.example.com` and open **Decisions → Approval numbers**:
- **Median decision time** for this participant (the KPI card and the "By approver" row). It counts
  from when a card came on screen, or from their previous decision on the page if that was later.
- **Overrides**: where they decided differently from the agent's recommendation. High numbers mean the
  recommendation or its reason was not convincing, or the card did not show enough.

Record for each participant: name, date, median decision time, cards decided, overrides, and the
notes. Send them to Claude (or add them to `docs/BUILD_LOG.md`). With two or three participants and an
overall median under 60 seconds, the Done-When is met and Claude marks F12.3 done on the Build Map.

If a participant got stuck on a card, that card's design is the thing to fix, whatever the median.

## Results so far

| Participant | Date | Median | Notes |
|---|---|---|---|
| Nischay | 2026-10-10 | — | Ran the whole script himself, including step 5 on a phone: the inbox reads and taps fine there. No card left him stuck. |

**Still needed for the Done-When:** two or three more people, each on their own fresh company, with an
overall median under 60 seconds. Until then F12.3 stays pending on the Build Map (decision H8 C).
