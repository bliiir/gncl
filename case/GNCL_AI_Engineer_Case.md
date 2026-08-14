# AI Engineer – Take-Home Case
**Go Nordic Cruiseline (GNCL)**

## Context

GNCL runs guest data across three systems that don't talk to each other: BookIT (booking system), LS Retail (onboard shop/restaurant POS), and HubSpot (CRM/marketing). There is no shared guest ID. The same guest can show up as three different, unlinked records — one per system, each with its own ID format and its own gaps.

This case is a simplified version of a real problem we're solving right now.

## The task

You'll get three small synthetic datasets (attached separately): `bookit_guests.csv`, `ls_retail_transactions.csv`, `hubspot_contacts.csv`. Same guest population, different schemas, deliberately messy — typos in names, missing emails, inconsistent date formats, a cabin number recorded correctly in one system and dropped in another.

Build something that:

1. **Matches guests across the three sources** and produces a single output file: one row per real guest, with the corresponding IDs from each source (where found) and a confidence score per match.
2. **Handles the fact that not everything can be matched deterministically.** Some matches will be exact (email or booking reference), some will need fuzzy logic (name + cabin + date range), some won't have a strong signal at all. Your output should be honest about which is which.
3. **Uses an LLM somewhere in the pipeline** where it earns its place — e.g. resolving ambiguous name variants, adjudicating borderline matches, or generating a human-readable explanation for why two records were (or weren't) linked. Don't bolt it on for the sake of it; use it where it does something rule-based matching struggles with. Use Ollama with a local open model (Llama, Mistral, Qwen — your choice) so you don't need a paid API key; that's also what runs on GNCL's own infrastructure. If you'd rather use a hosted API you're welcome to, but it's not required and won't be reimbursed.
4. **Exposes the result somehow** — a CLI, a small API endpoint, or a minimal UI. Doesn't need to be polished. We want to see you make something runnable, not something pretty.

## What to submit

- Code, in a git repo or zip.
- A short README (half a page is fine) covering:
  - Your matching approach and why.
  - Where you used the LLM and why there vs. a rule.
  - What you'd change to run this against millions of real records instead of a few hundred synthetic ones.
  - What you didn't have time to do.

## What we're not asking for

- A production system. This is a sketch of your thinking, not a deliverable we'll deploy.
- A frontend. A working script with clear output is worth more than a UI wrapped around weak logic.
- Perfect matching. We'd rather see calibrated confidence and honest gaps than a system that pretends to be certain.

## Time budget

Aim for 3–5 hours. We're not timing you strictly, but if you're at hour 8, stop and ship what you have with notes on what's next.

## What we're evaluating

- Can you reason about messy, real-world data instead of assuming clean input.
- Do you know when to use an LLM and when not to.
- Is your code something a colleague could pick up and extend.
- Can you communicate trade-offs briefly and precisely.

Questions during the case: email [contact] — we'd rather you ask than guess wrong on something we forgot to specify.
