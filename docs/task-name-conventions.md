# Task Name Conventions

Use short, stable task names in `run_with_profile.py` so the task ledger stays readable.

## Format

- Prefer `domain action`
- Keep it under about 4 words
- Avoid raw user text unless it is already concise

## Recommended Names

- meeting pipeline
- meeting retry
- youtube summary capture
- video guide capture
- video technical capture
- household ledger inspect
- household ledger append
- household ledger update
- travel flight search
- travel lodging search
- travel flight track
- local restaurant shortlist
- local cafe shortlist
- local bar shortlist
- k-service parcel tracking
- k-service postcode lookup
- k-service subway arrival
- k-service law lookup

## Rule Of Thumb

- If the task mutates external state, name the mutation, not the topic.
- If the task is read-only, name the lookup, not the whole user request.
