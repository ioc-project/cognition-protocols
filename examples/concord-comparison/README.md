# concord-comparison

The hidden-profile task of `../concord-slim-agents` (`evacuation-west-city`), run in four settings that differ in the protocol (free
speech or CONCORD) and in the transport (a star or SLIM). Three leaders (`participant-a`, `participant-b`, `participant-c`) hold the
shared facts and their own private ones; a fourth agent, the **facilitator**, holds no facts and takes no part in the discussion. The
right answer (West City) shows only when the private facts are pooled.

| | Protocol | Transport | The facilitator |
|---|---|---|---|
| **A** | free speech | star over TCP | the hub. It posts the opening, and a model passes every post on with `delegate_to` (it picks the recipients, the harness sends the text and puts the author in front). It closes the session after 60 s without a post, 10 minutes, or 100 posts, and a model reads the discussion and says whether the participants agreed on one option. |
| **B** | free speech | SLIM group session (broadcast) | posts the opening, then watches. It closes and gives the verdict as in A. No model for the routing: SLIM delivers every post to everyone. |
| **C** | CONCORD, after a free-text step | star over TCP | the hub, as in A. It posts the opening, waits for the three step-1 posts (or 60 s), starts the run (fixed content from the scenario), and a model passes every message on. |
| **D** | CONCORD, after a free-text step | SLIM group session (broadcast) | as in C, with no model: the opening, the wait, the init, then it keeps its node going. |

A vs B and C vs D compare the transport; A vs C and B vs D the protocol.

**The first step.** Every discussion starts with the same opening, posted by the facilitator: each participant posts once the facts it
knows, says which option it leans towards if it already leans (a lean is provisional), and asks the others for what is missing. In
free speech (A, B) the discussion simply goes on from there. In CONCORD (C, D) the facilitator starts the CONCORD run once the three
have posted, and the participants read the posts of the others before their first answer. The opening differs only in its last sentence.

In free speech the participants have no CONCORD tools: on every delivery they `post` or `stay_silent`, as they like. There are no turns
or rounds. In every scenario, once the run or the session is over, each participant gives its final choice privately (`final_answer`,
never sent): it goes in its own record (`final`), so that the agents can be compared and not only the outcome.

**Where the prompts are.** Everything a model reads (system prompts, openings, what each turn says, tools, replies to a wrong call, the
hub's and the verdict's prompts) is in `concord_comparison/prompts.py`; `python -m concord_comparison.prompts` prints them. The CONCORD
instructions themselves are the library's (`ioc_concord.guidance`), and the facts and the init of a run are in `scenarios/*.yaml`.

## Run

    export LLM_BASE_URL=...  LLM_API_KEY=...  LLM_MODEL=...
    ./run.sh --mode free --transport star           # A (--idle 60 --max-time 600 --max-posts 100)
    ./run.sh --mode free --transport slim           # B
    ./run.sh --mode concord --transport star        # C
    ./run.sh --mode concord --transport slim        # D
    uv run python batch.py --scenarios A,B,C,D --thresholds 0.7 --repeats 10 --parallel 3      # the comparison
    uv run python show.py runs/<run folder>         # the messages of a run (--text for the full text, --verify for the referee)
    uv run python metrics.py runs/batch-<time>      # the cost of a batch (--judge labels the free-speech posts with the model)

**Tasks.** `scenarios/` holds three versions of the evacuation task, with the same options and the same structure (4 shared facts, 4 hidden
ones split over the three leaders) but different facts, so the right answer differs: `evacuation-west-city`, `evacuation-east-town`,
`evacuation-north-hill` (the facts are those of the `hidden-profile` task set). `batch.py --tasks all` (or `--tasks west-city,east-town`)
runs every scenario on each task, in turns; each run is scored against the correct answer of its own task, and the run folders carry the
task in their name (`B-east-town-r0`, `D-east-town-t0.7-r0`). Without `--tasks`, the task is `--scenario` (West City by default).

`--fake-llm` checks the plumbing without a key. Each run writes `runs/<time>/` (a log and a `<agent>.json` per agent, the
facilitator's included). The exit status of `run.sh` is 0 when the outcome (the winner, or the option the participants agreed on) is
the correct answer.

Batches before 2026-11-18 used other letters (A concord star, B concord slim, C free slim, D concord slim with the first step, E free
star, F concord star with the first step; A and B had no first step). `metrics.py` reads them and maps them to today's letters; the
two without a first step are listed as `x` and `y`.

## What `batch.py` reports

Per scenario, over the runs that passed their referee: how many were right (the outcome), how many agents chose the right option in
their private final answer, how many of the hidden facts reached the others (a rough word match on what the participants wrote: the
step-1 posts, rationales and reasonings in C and D, the posts in A and B; it says if a fact was shared by the end, not when), tokens,
seconds, messages, and, in A and C, the messages the hub model routed wrongly (`misrouted`: a recipient missing, the sender, an unknown
name, a message passed on twice) or never passed on (`dropped`). For A and B it also prints how the run closed.
The referee is `verify.py` for C and D (the protocol, replayed from the messages), plus a check of the hub's routing in C; for A and B
it is a check that the record is whole (a channel, a verdict, three final answers), plus the hub's routing in A. Every run also needs
the three final answers.

**When a run goes wrong.** A model call is cut after `LLM_TIMEOUT` seconds (default 120) and tried again up to `LLM_MAX_RETRIES` times
(default 4), so that one stalled request does not end an agent. If a participant still crashes (after retries, or for a bug), `run.sh`
stops the whole run at once, and the others write their records, instead of leaving them to wait for it until the protocol's own
timeouts (about 20 minutes). A participant exits with status 0 whatever it chose: a non-zero status means a crash. `batch.py` also
stops a run that has not ended in `--run-timeout` seconds (default 900) and counts it as `TIMEOUT`. Runs that crashed or timed out have
no result: they are listed under "not counted" and left out of the totals.

`--parallel N` runs N runs at once (default 1); each gets its own SLIM node and ports. The model endpoint then takes N times more
calls at the same time: if it slows down, the seconds of the runs and the idle close in A and B are less comparable, so use 1 when the time
matters and more when only the outcomes do.

The scenarios take turns in a batch, so that a drift of the model hits all of them alike.
