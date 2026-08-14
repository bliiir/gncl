# Questions to ask the chat tab

Open `uv run gncl serve`, go to the Chat tab, and ask these. It answers from the output CSV, the three raw sources, the case brief and these docs -- nothing else -- and cites a `guest_id` or a file name for every claim.

Follow-ups work: the transcript is sent with each question, so "why?" and "which of those two?" mean what they look like.

## The tour

1. What was the assignment?
2. What was the gotcha?
3. How did we solve it?
4. How are we using local vs hosted models, and why?
5. What was done beyond what the brief asked for?

Asked in that order they walk the whole project: the brief, the traps in the data and the one in the tooling (`docs/SPECIFICATION.md` sections 2 and 5.2), the cascade that answers them (section 3), the local/hosted split and its reasoning (section 5.1), and the work beyond the brief (`docs/MODEL_EVAL.md`, `docs/DATA_ANALYSIS.md`).

Question 5 is deliberately phrased that way. Asked as "what did we do to show off our skills?" the model declines -- correctly, since intent is not something the documents record -- and the answer reads as though there is nothing to report. Ask what the material states, not what it implies.

## Following on

- Why is G-BK1021 flagged for review?
- Who are the two Anna Larsens, and how do you know they are different people?
- Why was phone country code not used as a signal?
- Which links did the local model audit, and what did it decide?
- What would break first at a million records?
- What is unverified in this work?

## What it will refuse

- Anything needing a count that the pipeline did not produce. Aggregates are computed in pandas and handed to the model; it is told not to derive numbers.
- Accuracy claims. "35 guests" is an output count, not a correctness figure, and there is no labelled ground truth to score against.
- Intent, motive, and anything else the documents do not state.
