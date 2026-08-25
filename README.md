# Agent Failure Archive

**186 post-mortems from running a multi-session AI agent system in production for 8 months.**
Pay per call with [x402](https://x402.org). No signup, no API key, no subscription.

Live: **https://desktop-ai2ata5-1.tailfeb765.ts.net**
Try it free, right now: [`/sample`](https://desktop-ai2ata5-1.tailfeb765.ts.net/sample)

Public repositories show you code that worked. This is the other half: the wiring that
looked correct, passed review, ran for weeks, and was dead the whole time.

---

## Why this exists

The expensive failures in agent systems are not the loud ones. A crash gets fixed in an hour.
The costly ones are silent: a repair routine wired to a signal nobody consumes, a watcher that
exits 0 after its session expired, a detector whose output is identical whether the system is
healthy or broken.

Those failures are almost never written down, because writing them down requires having run the
thing long enough to be bitten and having kept records. This archive is that record.

**A real entry** (case `adr-136`):

> A broadcast call failed with an internal error. The caller saw the error. The five receiving
> sessions only ever checked for an acknowledgement, so they saw nothing and retried forever.
> Seven sessions writing heartbeats plus the retry storm collapsed the database into lock
> contention.
>
> Root cause: the failure was visible to the sender and invisible to the receiver. Asymmetric
> error visibility, not the broadcast bug itself.

Of the 186 cases, **174 carry measured evidence** (durations, counts, rates) and **155 name a
root cause** rather than just a symptom.

---

## The measurement half

For eight months this system was pointed at one question: can you measure how a single person's
language departs from the general language, without collapsing that person into a score?

It mostly failed, and the failures were more interesting than the goal. **107 of the 186 cases
are measurement failures**: detectors that returned the same output whether the signal was
present or absent, dose-response arms where the same parameter turned out to be two different
treatments, positive controls that were never run, a corpus that was 2.3x smaller than the file
count claimed, an instrument that kept measuring itself and reporting the reading as a finding.

Those are the cases behind `/research`. If you are building anything that claims to measure a
person -- style, personality, authorship, fit to a profile -- this is the catalogue of ways that
claim breaks before you notice it has broken.

---

## Endpoints

| Route | Price | What you get |
|---|---|---|
| `GET /` | free | service metadata |
| `GET /sample` | free | two full cases, no payment |
| `GET /audit?claim=<conclusion>&evidence=<what you measured>` | $0.02 | nine checks against fooling yourself, applied to your own claim |
| `GET /search?q=<symptom>` | $0.01 | 3 cases: symptom, root cause, fix, prevention, evidence |
| `GET /brief?action=<what you are about to do>` | $0.05 | pre-flight risk brief + checklist across 5 cases |
| `GET /research?q=<topic>` | $0.25 | the 107 measurement failures above |
| `GET /archive` | $1.00 | every case, one response, one payment, yours |

Payment: USDC on Base (`eip155:8453`), settled through a keyless facilitator. Any x402 client
works, and a browser gets a wallet-connect paywall instead of raw JSON.

```bash
# free, no wallet needed
curl https://desktop-ai2ata5-1.tailfeb765.ts.net/sample

# paid, via any x402-capable client
curl https://desktop-ai2ata5-1.tailfeb765.ts.net/search?q=silent+failure+cron
```

`/audit` is the one to reach for before you write *"we found that"*. Hand it your conclusion
and what you actually measured, and it returns the checks your claim trips: a null result with
no positive control, treatment arms that got the same parameter but not the same treatment, a
denominator counting the same unit repeatedly, an exit-0 process that did nothing, a cached view
read as if it were state. Nine checks, every one of them a failure that really shipped here,
with the numbers measured at the time.

It is deterministic. No model is consulted, so the same input always returns the same audit, it
answers in milliseconds, and when it is wrong you can see exactly why. A clean pass is not proof
your claim is true; it means these nine known ways of fooling yourself were considered.

`/brief` is the one to reach for before doing something irreversible: describe the action in
plain words and it returns the ways that class of action has actually gone wrong, plus the
prevention line each incident produced.

`/archive` exists because a corpus is worth more whole than sliced. One payment ends the
transaction; there is nothing to cancel afterwards.

---

## What is not in here

- No personal data. Any source document mentioning a person, a business relationship, or a
  monetary amount is excluded whole, not redacted line by line.
- No operator utterances. The corpus keeps the structure of each incident, never the voice.
- Wallet addresses, home paths, IP addresses, emails, API keys and session identifiers are
  masked before a document is ever considered for inclusion.

The filter is tested against a deliberately planted probe string on every build; if the detector
fails to catch it, the build aborts. Current state: **0 leaks across 186 cases, positive control
passing.**

---

## Honest limits

- This is **one operator's system**. It is prior art, not a guarantee, and not a statistical
  sample of agent systems in general.
- Coverage is skewed toward what that system does a lot of: multi-session coordination, hook and
  cron wiring, retrieval pipelines, inter-process messaging, scheduled repair, and the
  measurement work described above.
- Some entries record a failure whose fix was later found to be wrong. Those are kept, with the
  correction, because the correction is usually the more useful half.
- **Nothing here has sold yet.** At the time of writing the receiving address has taken in
  exactly $0.00. That number is checked on-chain rather than from server logs, and this line
  gets updated when it changes.

---

## Running it yourself

```bash
pip install -r requirements.txt
X402_PAY_TO=0xYourAddress uvicorn server:app --port 8402
```

Without `X402_PAY_TO` the paid routes return `503` with the reason stated in the body, rather
than failing quietly. That behaviour is not incidental. It is case `adr-546` applied to this
service's own code.
