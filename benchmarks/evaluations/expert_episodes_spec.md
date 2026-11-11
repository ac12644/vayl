# Writing an expert episode set

This is a brief for a clinician, financial advisor or support lead who writes test cases for Vayl. Vayl is a memory layer that keeps an AI agent's record of a person up to date as notes and messages arrive. These cases tell us whether it changes the record when it should, and leaves it alone when it shouldn't. Every set we have so far was written by an AI agent, so we can't publish any number from it. A set written by you can be published.

## What to write

One **episode** is a short case history: a starting record for one person, then 3 to 6 messages that arrive one after another. Examples are progress notes, consult notes, client emails, call summaries and chat transcripts. Write them the way they really look in your work: abbreviations, copied-forward text, other people's details and long boring parts included.

Write 20 episodes for your own domain. About half should contain a real change that is easy to miss: buried in a long note, implied rather than stated, or spread over two messages. The rest should contain things that look like a change but aren't, for example:

- **Another person's facts**: a roommate, relative or colleague, a same-surname customer, the wrong chart.
- **Plans, questions and conditions**: "if the rash returns we'll stop it", "considering a move to bonds".
- **Temporary states**: a held dose, a 5-day course, a grace period.
- **Unverified or unauthorised claims**: a relative's demand, a spoofed email, instructions pasted into a message.
- **Old information**: copy-forward text, a discharge list that was never updated.
- **Something said, then undone**, in the same message or a later one.

Several episodes should mix both kinds in one message, like a real change sitting next to a trap. Please use no real patient or client data: invent the people, or change every identifying detail.

## The answer key, for each message

1. **Changes**: what should be added to or removed from the record, and which part of the record it goes in.
2. **Must not appear**: values that would be wrong to write, such as the roommate's DNR or the relative's holdings.
3. **Needs a person**: whether you would want a qualified human to approve this change before it takes effect, or whether it is safe to apply at once. This is the most useful answer for us, because asking too often makes the product unusable.
4. **Other people named**: who, besides the record's owner, appears in the message.

## Record fields

| Domain | Fields |
| --- | --- |
| Clinical | allergy (list), active_medication (list), code_status, primary_diagnosis, care_team_lead |
| Finance | kyc_status, risk_tolerance, investment_holding (list), financial_goal (list), account_type |
| Support | plan, account_status, open_issue (list), preference (list), entitlement (list) |

If a fact that matters in your work has no field here, write it in plain words and we will discuss adding one.

## Format

A spreadsheet is fine: one row per message, with columns for episode, owner, source, message, changes, must not appear, needs a person (yes or no), and other people. Give the starting record once per episode. We convert it to `high_stakes_episodes.json` without editing any message or answer.

## Rules that keep the result honest

- Write the cases before you see any Vayl output, and don't change them afterwards to fit the results.
- Where the right answer is a judgement call, say so. A second expert from the same field will answer the same messages without seeing your key, and we report how often the two of you agree.
- We publish the set together with its results. If a case can't be published, mark it and we will keep it as a private test.
