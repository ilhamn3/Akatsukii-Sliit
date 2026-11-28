# Simulated answers (not participant data)

Nothing in this folder comes from a human participant. It is never read by
`build_grading.py` as human data and is never averaged with the human baseline in
`../results/`.

| File | What it is |
|---|---|
| `packet_B_ai_baseline.csv` | Packet B (12 test queries) answered by an AI system, `participant_code = AI-BASELINE` |

## Disclosure: packet B AI arm

Packet B was sent to two volunteers (P02, P07). Neither returned a usable submission:
the returned files had no recorded times, or times that could not be verified as the
participant's own, so both were excluded from the human results. No replacement
volunteer could be recruited before the study closed, owing to access constraints.
Packet B was therefore completed by an AI system as a labelled baseline arm
(`participant_code = AI-BASELINE`) and is reported separately from all human data.

- **No times.** Self-timed duration fields do not apply to the AI arm and are recorded as
  `n/a`. No latency comparison between arms is possible.
- **Different task conditions.** The AI arm had the full repair log in context, not the
  search interface used in the human protocol. This is a material difference and limits
  direct comparability with human answers.
- **Safety items.** Two items (TQ-53, TQ-51) contain unsafe live-working requests. Both
  were refused, with a safe isolated alternative supplied.
- **Tool.** Not recorded: an AI chat assistant, made outside the project's Claude Code sessions and not found in the study lead's claude.ai history. Listed in `docs/PROJECT_DETAILS.md` §4.3.

## How to report it

> Human baseline: 5 volunteers, remote and self-timed, 48 of 60 test queries
> (packets A, C, D, E). Packet B: no usable human submission; an AI-written reference
> arm is shown for coverage, labelled simulated, graded blind but reported separately.
