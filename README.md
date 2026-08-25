# Agent Failure Archive

**186 post-mortems from running a multi-session AI agent system in production for 8 months.**
Pay per call with [x402](https://x402.org). No signup, no API key, no subscription.

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

## Endpoints

| Route | Price | What you get |
|---|---|---|
| `GET /` | free | service metadata |
| `GET /sample` | free | two full cases, no payment |
| `GET /search?q=<symptom>` | $0.01 | 3 cases: symptom, root cause, fix, prevention, evidence |
| `GET /brief?action=<what you are about to do>` | $0.05 | pre-flight risk brief + checklist across 5 cases |

Payment: USDC on Base (`eip155:8453`). Any x402 client works.

```bash
# free
curl https://<host>/sample

# paid, via an x402-capable client
curl https://<host>/search?q=silent+failure+cron
```

`/brief` is the one to reach for before doing something irreversible: describe the action in
plain words and it returns the ways that class of action has actually gone wrong, plus the
prevention line each incident produced.

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
  cron wiring, retrieval pipelines, inter-process messaging, scheduled repair.
- Some entries record a failure whose fix was later found to be wrong. Those are kept, with the
  correction, because the correction is usually the more useful half.

---

## Running it yourself

```bash
pip install -r requirements.txt
X402_PAY_TO=0xYourAddress uvicorn server:app --port 8402
```

Without `X402_PAY_TO` the paid routes return `503` with the reason stated in the body, rather
than failing quietly. That behaviour is not incidental. It is case `adr-546` applied to this
service's own code.
