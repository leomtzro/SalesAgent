# SalesAgent — a prospecting agent that finds you food

Give it a company name. It goes online, works out who the decision maker is,
digs up a way to reach them, finds a reason to call them *this week*, and hands
you a one-page brief with the first email, the cold-call opener, and the
answers to the three objections you're about to hear.

You do the closing. That's the point.

```
$ python -m prospector find "Acme Supplements"

Researching Acme Supplements...

  ✓ Acme Supplements [fit 86]
      Jane Doe - VP of Operations
      jane.doe@acmesupplements.com (pattern_guess)
      why now: Raised a $12M Series A in March
      ! No published address for Jane Doe. 3 guesses added - verify before a real send.
      outputs/briefs/acme-supplements-jane-doe.md

Read the brief: outputs/briefs/acme-supplements-jane-doe.md
```

---

## Start here (10 minutes)

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Add your API key

```bash
cp .env.example .env
```

Open `.env` and paste in a key from
[console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys).
That key is the only account you need — web search runs through the same API,
so there is no scraper to maintain and no second subscription to buy.

### 3. Tell it what you sell

Open **`config/offer.yaml`** and fill it in. **This is the only file you have
to write**, and it is where almost all of the quality comes from. It holds:

- what you sell, in plain language
- the job titles that can actually sign
- the **buying signals** that mean "they need this now"
- your **proof points** — and this one matters: *the agent is only allowed to
  claim results that appear in that list.* Leave it empty and your outreach
  makes zero results claims. That's deliberate. An invented case study is how
  you lose a deal and your reputation in the same afternoon.

### 4. Check your setup

```bash
python -m prospector doctor          # offline: key, deps, DNS, config
python -m prospector doctor --live   # one real API call to prove it works
```

`doctor` checks everything it can for free. `doctor --live` then makes a single
real request — one web search — and confirms four things that are otherwise
only discovered halfway through a batch: the key is accepted, the model is
available to you, web search is enabled for your account, and a search
actually returns results. It costs a fraction of a cent. **Run it before your
first batch.**

### 5. Run it

```bash
# one company
python -m prospector find "Acme Supplements" --domain acmesupplements.com

# a whole list
cp config/targets.example.csv config/targets.csv   # then edit it
python -m prospector batch config/targets.csv

# see your pipeline, best fit first
python -m prospector list

# read one brief
python -m prospector show acme
```

---

## What you get per prospect

Three files, because they serve three different moments:

| File | What it's for |
|---|---|
| `outputs/briefs/<name>.md` | The one-pager you read in the 60 seconds before you dial |
| `outputs/pipeline.csv` | Paste into a CRM or a sheet — one row per prospect |
| `outputs/prospects.jsonl` | The full archive, every field, for anything you build later |

The brief contains:

- **Who** — name, title, seniority, LinkedIn, who they report to (your
  escalation path), and *why this person* owns the problem
- **How to reach them** — every email and phone number found, each one labelled
  with how much to trust it and linked to the page it came from
- **Why now** — the buying signals, with dates and sources
- **The first approach** — three subject lines, the email, a cold-call opener,
  a voicemail, a LinkedIn DM, and a follow-up sequence
- **When they push back** — the three objections this specific person is most
  likely to raise, what's really behind each one, and what to say
- **Fact-check before sending** — every claim the copy makes about them, as a
  checklist. Tick them off before you hit send.
- **Fit score 0–100** and the reasons to skip them

---

## The two confidence labels to read before you act

### How sure we are it's the right *person*

This is the one that surprises people. Most companies under a few hundred
employees **do not publish an ops leadership page**, so the decision maker's
name usually turns up only in a third-party directory whose data can be a year
stale. Opening a call by asking for someone who left eight months ago is worse
than asking for the role.

| Label | Meaning | What to do |
|---|---|---|
| `CONFIRMED` | Company's own site, press release, filing, or their own post | Use the name |
| `DIRECTORY ONLY` | A third-party listing is the only source | Use it, but confirm on the call before trusting it |
| `ROLE ONLY` | The role is proven; nobody is named | Call the main line and ask for the role — the brief tells you what to say |

`ROLE ONLY` is a **useful result, not a failure.** The agent reads job postings
to prove the role exists — a posting that says *"this role reports directly to
the VP of Ops"* confirms there's a VP of Ops to ask for, even with no name
anywhere. That call still connects. The brief hands you the exact phrasing.

Job postings are mined for two more things while it's in there: the seniority
ceiling (if the most senior supply-chain title they're hiring is a Director,
there's probably no COO above them) and the responsibilities, which are a
plain-language list of what's currently broken, in their own words.

### How sure we are about the *email*

Every email address comes with a label. Read it before you send.

| Label | Meaning | What to do |
|---|---|---|
| `VERIFIED` | Published on a real page, with the URL | Send |
| `LIKELY` | Inferred, but the company's address format was confirmed from a real address at that domain | Send, expect the odd bounce |
| `GUESS` | Pattern guess. Nobody published this. | **Verify first.** |

This exists because an LLM asked for "the email of the VP of Ops at Acme" will
cheerfully produce a plausible address it has never seen. So the work is split:

- **the agent** reports only addresses it actually *saw on a page*, with the URL
- **plain Python** (`prospector/contact.py`) derives everything else from the
  company's own published address format, and labels it a guess
- **DNS** confirms the domain can even receive mail before you bother — and
  it proves DNS works on a known-good domain *first*, because some networks
  answer "no such domain" for everything, which would otherwise stamp a false
  "this will bounce" warning on every prospect you research

Want only the sure things? `--verified-only` throws the guesses away.

Want higher hit rates? Put a [hunter.io](https://hunter.io) key in `.env` as
`HUNTER_API_KEY` and it'll use that before falling back to guessing. Entirely
optional — nothing breaks without it.

---

## How it works

```
  "Acme Supplements"
          │
          ▼
  ┌─────────────────────────────────────────┐
  │ 1. RESEARCH          research.py        │
  │    Claude + live web search             │
  │    → who, what's happening, what's      │
  │      published, with sources            │
  └─────────────────────────────────────────┘
          │  raw notes
          ▼
  ┌─────────────────────────────────────────┐
  │ 2. EXTRACT           client.py          │
  │    No web access. Reshapes the notes    │
  │    into validated fields. Can't find    │
  │    it → null, never invented.           │
  └─────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────┐
  │ 3. CONTACT           contact.py         │
  │    Plain Python. Address format, MX     │
  │    check, confidence labels.            │
  └─────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────┐
  │ 4. WRITE             outreach.py        │
  │    The approach + objection responses.  │
  │    Can only claim your proof points.    │
  │    Then a linter checks the draft.      │
  └─────────────────────────────────────────┘
          │
          ▼
    brief.md + pipeline.csv + prospects.jsonl
```

Two deliberate choices worth knowing about:

**Research and writing are separate calls.** The researcher has web access; the
writer does not. The writer can only work from what the researcher cited. That
one boundary is what stops the copy drifting into fiction.

**Searching is server-side.** `web_search` and `web_fetch` are tools that run on
Anthropic's infrastructure, not on your machine. No scraper, no proxies, no
search API key, nothing to maintain when a site changes its HTML.

| File | Job |
|---|---|
| `config/offer.yaml` | **What you sell. The file you edit.** |
| `prospector/models.py` | Every field the agent may return |
| `prospector/offer.py` | Loads your config, renders it into the prompts |
| `prospector/client.py` | API setup, the research loop, structured extraction |
| `prospector/research.py` | Stage 1+2 and their prompts |
| `prospector/contact.py` | Email inference, MX checks, confidence |
| `prospector/outreach.py` | Stage 4 and the draft linter |
| `prospector/pipeline.py` | Wires the stages together, runs batches |
| `prospector/store.py` | Writes the brief, CSV, and archive |
| `prospector/cli.py` | The commands |

---

## Commands

```bash
python -m prospector doctor                    # check setup (free)
python -m prospector doctor --live             # prove it against the real API
python -m prospector find "Company"            # research one
python -m prospector batch targets.csv         # research many
python -m prospector list                      # your pipeline
python -m prospector show <name>               # print a brief
```

Useful flags:

| Flag | Does |
|---|---|
| `--domain acme.com` | Skip the "which Acme?" problem |
| `--note "met at Expo West"` | Context you already have; it gets used in the copy |
| `--verified-only` | Drop guessed addresses |
| `--min-fit 60` | Don't waste tokens writing copy for bad fits |
| `--max-searches 6` | Cap the spend per company |
| `--no-copy` | Research only, no writing |
| `--concurrency 5` | Companies in parallel during a batch (default 3) |
| `--limit 10` | Only the first N rows of the CSV |
| `--redo` | Re-research companies already on file |

Batches skip companies already in `prospects.jsonl`, so a re-run after a
failure doesn't pay twice. One company erroring never kills the batch.

---

## Cost

Each prospect is a few web searches plus two or three model calls. Control it:

- `--max-searches 6` — searches are billed per use, so this is the big lever
- `--min-fit 60` — research everyone, only write copy for the good ones
- `PROSPECTOR_EXTRACT_MODEL=claude-sonnet-5-5` in `.env` — the extraction step
  is just reshaping text, so a cheaper model does it fine. The researcher and
  the writer are where you want the good model.

Run on five companies first and look at the token counts printed after each
`find` before you point it at five hundred.

---

## Before you send anything

The agent drafts. **You send.** That's not a limitation, it's the design — and
two of those reasons are legal, not stylistic.

- **Check the facts.** The brief's "Fact-check before sending" list is every
  claim the copy makes about them. Research can be wrong or stale.
- **Check the address label.** A `GUESS` that bounces costs you more than one
  email — bounce rates wreck your sending domain's reputation for everyone you
  mail afterwards.
- **US cold email is legal, with conditions.** CAN-SPAM requires accurate
  headers, no deceptive subject line, and a working opt-out. The agent puts
  your `opt_out_line` in every draft and the linter fails the draft if it's
  missing — but *honouring* opt-outs is on you.
- **Phones are stricter than email.** Numbers labelled `mobile`, and anything
  that might be a mobile, carry real restrictions in the US (TCPA, and the
  national Do-Not-Call list). Business switchboards are the safe lane. Check
  before you auto-dial anything.
- **Don't let it run unattended into a mail server.** There's no send step in
  this repo on purpose.

---

## Tests

```bash
python -m pytest tests/ -q
```

80 tests, no network and no API key needed. Three layers:

- **Unit** — name parsing, email-pattern inference, config loading, the draft
  linter.
- **Pipeline** (`test_pipeline_e2e.py`) — the whole flow against a substituted
  client. Pins the behaviour that matters most: *an address the researcher
  never saw must come out labelled a guess.*
- **Wire** (`test_wire_protocol.py`) — the real SDK talking to a local server
  that speaks the Messages API. This is the layer that catches a malformed
  request: a tool type the API would reject, an `output_config` the SDK
  silently drops, a schema that doesn't serialize. A substituted client can't
  see any of those.

There are also regression tests for a network that answers NXDOMAIN for every
domain, because that produced a false "mail will bounce" warning on every
prospect until it was fixed.

---

## What's next

This is the top of the funnel — it finds and opens. Natural next steps:

1. **Reply handling** — classify inbound replies (interested / not now / wrong
   person / unsubscribe) and draft the response. `models.py` already has the
   objection shapes to build on.
2. **The closing agent** — pulls the brief in as context for a live call.
3. **Signal monitoring** — run `batch` on a schedule against a watchlist and
   alert you when a buying signal appears, instead of researching on demand.
4. **CRM sync** — `prospects.jsonl` has every field; push it wherever you work.

Start by running it on ten companies you know well. You'll spot immediately
where `config/offer.yaml` needs sharpening — that file, not the code, is what
makes the difference between generic and sharp.
