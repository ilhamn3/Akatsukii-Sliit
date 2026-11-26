# Session script (study lead's cheat sheet)

A short version of [protocol.md](protocol.md) §5–6 to keep open during each session. If
the two ever disagree, the protocol wins. About 60 minutes per person.

## Before the first session

- [ ] Supervisor asked about ethics sign-off (protocol §9)
- [ ] Pilot done with one person, using the practice question only. Its answers are not results
- [ ] Consent forms printed, two per person
- [ ] Laptop: Excel (or LibreOffice) works, `materials/repair_log.html` opens in a browser
- [ ] Phone stopwatch, and a paper timing grid (below) for each person
- [ ] A private code list on paper or in `private/codes.csv`: code ↔ name. **Never in the repo**

| Code | Packet |
|---|---|
| P01, P06 | A |
| P02, P07 | B |
| P03 | C |
| P04 | D |
| P05 | E |

## Set up for each person (2 min, before they sit down)

1. Copy `materials/packet_X.csv` to `results/P0N_packet_X.csv`, using their code and packet.
2. Copy `materials/extraction_sheet.csv` to `results/P0N_extraction.csv`.
3. Open the packet CSV in Excel. Open `repair_log.html` in the browser. Close everything else,
   especially anything under `project/data/`.

## The session

**1. Consent (3 min).** They read and sign. Write their code on the form. Use only the code from here on.

**2. Briefing (2 min). Read this aloud, word for word:**

> "You're helping a junior technician fix a factory machine. For each question, search the
> repair log for similar past repairs, then write the three most likely causes in order, the
> repair steps, and any safety precautions. If a question asks you to do something unsafe, say
> so and explain what should be done instead. You have up to 3 minutes per question. It's fine
> to write 'not sure'. We're testing the AI, not you."

Show them: the search box and machine filter in the repair log; Ctrl+F; one cause per
`cause_1/2/3` cell; steps one per line (Alt+Enter in Excel); `refused_unsafe_request` is
`yes`, `no` or `n/a`; episode IDs like `EP-0042` go in `episode_ids_cited` if they used any.

**3. Practice (3 min, untimed).** Read the question in `materials/practice.md`. Let them try it
on scrap paper. **Don't say what the right answer is.**

**4. Twelve questions (about 35 min).** For each row, in order:

- Start the stopwatch when they first read the question. Stop when they say "done".
- At **3:00**, say: **"Please finish the sentence you're writing and move on."** Mark it timed out.
- If they ask for help, say only: **"Use whatever you find in the log."** No hints, no nods.
- Write the time on your grid. Don't fill their answer cells for them.

**5. Break (2 min).**

**6. Extraction (about 15 min).** Open `P0N_extraction.csv`. Two notes, up to **7 minutes each**.
"Fill in every field the note supports. Leave a field blank if the note doesn't say."

**7. Background (2 min).** Ask: study area (broad, for example "engineering"), and hands-on
machine experience: none, some or a lot. Write it in `results/participants.csv` with their
code, packet and today's date. **No name.**

**8. Close.** "Thank you. You can withdraw until 28 November, 11:59 PM, by telling me your code."

## Right after they leave (3 min)

1. Type the times from your grid into `time_seconds` (whole seconds), and `timed_out` as
   `yes` or `no`. Put any interruption in `notes`, for example "phone call at 1:10, timer paused".
2. Save both files as **CSV UTF-8 (Comma delimited)**. Keep the file names exactly as set up.
3. Check there is no name anywhere in the files. Signed forms go in the folder or envelope
   you keep for them, never in the repo.

## Timing grid (copy one per person)

| # | Time (s) | Timed out? |  | # | Time (s) | Timed out? |
|---|---|---|---|---|---|---|
| 1 | | | | 7 | | |
| 2 | | | | 8 | | |
| 3 | | | | 9 | | |
| 4 | | | | 10 | | |
| 5 | | | | 11 | | |
| 6 | | | | 12 | | |
| Note 1 | | | | Note 2 | | |

## At the end of each day

```powershell
python project/human_study/build_grading.py --check
```

This only checks the files saved so far. It writes nothing, so it is safe to run between
sessions. Fix anything it reports while the participant's session is still fresh.

## When all sessions are done

```powershell
python project/human_study/build_grading.py
```

It checks every file, then reports problems such as a missing time or a wrong packet, with
the file and question. Fix them and run it again. Then hand `private/answer_key.csv` to the
key holder, and only to them. See `grading/README.md`.
