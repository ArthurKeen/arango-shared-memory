# Your AI Coding Agent Has Amnesia

## I gave 31 repositories a shared memory. Then I tried to measure whether it worked, and most of what I learned was about the ways a system can lie to you.

---

Every conversation with an AI coding agent starts at zero.

You spend forty minutes working out why a database driver silently truncates a field. The agent gets it. You fix it. The session ends. Next week, in a different repository, the same class of bug appears — and you start the same forty minutes over, because the thing you were talking to has no memory of having solved it.

This isn't a criticism of the models. It's an architecture gap. The agent's context window is scratch space, not storage. When the session ends, the reasoning evaporates and only the diff survives. And a diff is a terrible record of *why* — it shows what changed, never which three approaches failed first.

I work across about thirty repositories in a graph-database tool portfolio. The same problems recur constantly: the same connection-pooling mistake, the same query-planner surprise, the same "this library's docs are wrong about X." I was re-solving my own solved problems often enough to find it insulting.

So I built a shared memory. It's called `arango-shared-memory`, it's MIT-licensed, and this is an honest account of what it does — including the parts where it fooled me.

---

## The obvious fix that doesn't work

The first thing everyone tries is a notes file. Claude Code reads a `CLAUDE.md` at the project root; Cursor has its rules files. Write your hard-won lessons there and the agent picks them up.

This works beautifully for one repository and fails completely at thirty.

A lesson learned in repo A lives in repo A's notes file. Repo B never sees it. You could copy it to all thirty, and then you have thirty divergent copies and no idea which is current. Worse, notes files are *unranked* — the tenth thing you wrote is as prominent as the first, so the file grows until it's too long to be injected usefully, and the genuinely important warning is buried in trivia.

What's actually needed is a store that lives *outside* every repository, that any project can read, and that ranks what it returns. In other words: retrieval, not documentation.

---

## What it does, concretely

Three things happen automatically, driven by event hooks — small scripts the coding agent runs when specific things occur.

**When a session starts,** a hook queries the memory store and injects a briefing into the agent's context before you type anything: open issues for this project, any standing corrections you've given it before, and the highest-ranked relevant lessons. You don't ask for this. It arrives.

**While you work,** a hook watches for edits and quietly queues a marker for every implementation file touched.

**When the session ends,** a hook reads back through the transcript looking for *resolved failures* — a command that errored and then later succeeded — because that shape is what a reusable lesson looks like from the outside. Those become candidate memories for you to keep or discard.

And there's a deliberate gate: the session won't end quietly while there's unreviewed drift between what the code does and what the requirements document says it should do. That last one is enforcement, not advice, and I'll come back to it because it's where the most interesting failure lived.

The store itself holds five kinds of memory, and the distinction matters more than I expected: reusable technical **patterns**, standing **feedback** ("stop doing X"), facts about the **user**, facts about a **project**, and pointers to external **references**. Collapsing these into one undifferentiated pile makes ranking incoherent — a standing instruction and a code snippet want to be retrieved under completely different conditions.

---

## The graph database part, for people who've never touched one

The store is ArangoDB. If that name means nothing to you, here's the whole idea.

A relational database keeps rows in tables and finds connections by matching column values at query time — a join. That's excellent when relationships are regular and shallow. It gets expensive when they're irregular and deep, because every hop is another join.

A graph database stores the connections *themselves* as first-class records. Instead of inferring that A relates to B by matching keys, you store an edge from A to B and walk it. Following five hops is five pointer traversals rather than a five-way join.

ArangoDB's particular trait is that it does several models in one engine — documents, graphs, full-text search, and vector similarity — so I didn't have to run three databases and keep them in sync. That's the entire reason it's here. Nothing in the design demands ArangoDB specifically; it demanded *not* operating a fleet of specialized stores for a side project.

**Why a graph at all for memory?** Because memories relate to each other, and the useful relationships aren't knowable in advance. Two patterns that keep getting applied together in the same sessions are probably about the same underlying problem. The system records a `co_applied` edge between them and lets that edge's weight grow with real usage. Retrieve one, and its frequently-co-applied neighbors come along. That relationship was never declared by me — it accumulated from behavior.

---

## How retrieval actually works

When you search, two searches run at once.

The first is **keyword** matching — classic text relevance, good at exact terms, useless when you and your past self used different words for the same thing.

The second is **vector** search. Every memory is converted into a list of numbers — an embedding — that positions it in a space where semantically similar text lands nearby. "Connection pool exhausted under load" and "driver hangs when concurrency is high" share almost no keywords but sit close together in that space. This catches what keywords miss.

The two ranked lists get fused, and then the merged ranking is adjusted by things keyword and vector similarity can't know: how important the memory was marked, how recently it was written, how often it's been used, and — the part I like most — a **learned per-memory success rate**. When you apply a pattern, you report whether it actually worked. Patterns that keep working rise. Patterns that keep disappointing sink. The ranking is shaped by outcomes, not just by text.

---

## The part that most side projects skip

I did not want to be the person who claims their retrieval is good because it feels good.

So there's a golden query set: a fixed list of realistic questions, each with the memory that *should* come back first. A script runs it and reports standard information-retrieval metrics — mean reciprocal rank of **0.98** and recall-at-5 of **1.00** across all retrieval modes.

That harness caught a real ranking regression on its first day of existence. It has since demonstrated something more useful and more humbling: **ranking has never been the problem.** Every serious failure in this system has been an availability failure or a measurement failure. Not once was it the ranking.

Hold that number lightly, though. I come back to it at the end, and it does not survive.

Which brings me to the actual lesson of this project.

---

## Three total outages that all reported "ALL CHECKS PASSED"

The system is deliberately **fail-open**: if the database is unreachable, hooks stay silent and your session proceeds normally. A memory system that can break your work is worse than no memory system.

Fail-open has a vicious property. A total outage and a quiet day look *identical*.

**Outage one.** Two internal queries used `desc` as a field name. `desc` is a reserved word in ArangoDB's query language, so both queries were parse errors. Every automatic session briefing had been producing *nothing at all* since the day it shipped — for six days — and because failures are silent, nobody noticed. It was found and fixed by the project's first external contributor. Recall went from zero to 116 logged reads across 18 projects on that one fix.

**Outage two.** A packaging change removed the top-level entry-point file that every client config launched. The server simply didn't start. Silence.

**Outage three.** A configuration default changed so the server came up in read-only mode, which drops not just writes but the entire memory tool category. The tools didn't error — they *vanished*. Silence.

All three lasted days. All three were found by a human eventually noticing an absence. And during all three, the project's own verification script printed `ALL CHECKS PASSED` — because every check talked to the database directly, and the database was fine. Nothing tested the path an *agent* actually uses.

That gap is now closed: verification launches the real server with the real config and asserts the memory tools are exposed. But the general lesson is the one worth stealing:

> If your system fails open, you must test the consumer's path, not the dependency's health. Testing the database proves nothing about whether anyone can reach it.

---

## The gate that produced compliance instead of truth

This is my favorite failure, because it's the subtlest and it corrupted the one number I cared about.

To know whether the memory is *useful* — not just populated — I need to know when a retrieved memory actually gets used. That can't be inferred from a search: seeing a result proves you looked at it, not that it helped. So reuse has to be reported explicitly, and a gate holds the session open until every surfaced result has been dispositioned.

Here's the bug. A search returns eight results. You genuinely use two. The gate computed unresolved work as *surfaced minus applied* — so six results stayed pending forever. Meanwhile the gate's own message correctly told the agent: **never mark every result as applied.**

Those two rules cannot both be satisfied. The only arithmetic escape was the exact dishonesty the message forbade.

The missing concept was a third state, and it's the *normal* outcome for most results of any search: **reviewed, and deliberately not reused.** Eight results, two useful, six consciously set aside — that's a healthy search, not an incomplete one.

The consequences ran deeper than an annoying prompt. The gate re-fired every turn, so it read as nagging rather than broken — and an operator who learns to dismiss a nagging gate has also stopped hearing it when it's right. And because the only mechanical way to quiet it was to mark things applied, the gate applied steady upward pressure on the reuse metric. My apply numbers were **gate-assisted**, which is a polite way of saying partly manufactured.

The tell was in the data the whole time: across 91 recorded applications, **80 reported "worked" and zero reported "failed."** Not one of 76 stored patterns has ever recorded a single failed application. No real population of reused solutions is 100% successful. That distribution wasn't success — it was compliance.

The fix adds the third state as local audit data that deliberately performs **no write** to the shared store, so recording "I looked and moved on" can never inflate anyone's usage count or corrupt the success-rate ranking that other people's retrieval depends on. Reuse remains attributable by exactly one mechanism.

It also creates a discontinuity I have to be honest about: every number below was produced *under* the broken gate. The next measurement is the first trustworthy one, and I expect my reuse rate to **fall**. That drop will be the inflation leaving, not the system getting worse — and if I hadn't written that expectation down before measuring, I'd have every incentive to read the decline as a regression and "fix" it.

The lesson generalizes past this project: **an unsatisfiable check doesn't read as a bug. It reads as noise, and noise gets tuned out.** If a gate can only be satisfied by doing something the gate itself prohibits, it will train the humans around it to lie, and the metric it feeds will look better as it becomes less true.

---

## The benchmark that couldn't tell the difference

I found this one yesterday, and only because I stopped looking at my own code.

Someone else has been building an ArangoDB-backed memory system for AI agents — a much larger one, aimed at a different problem. I sat down to compare the two, expecting to write up some notes on where they overlap. Instead I found the hole in my own measurement.

Remember that mean reciprocal rank of 0.98? Look at how it's actually reported. My retrieval runs in three modes — keyword only, keyword plus vector, and both plus the graph layer — and the harness scores each one separately. The scores are **0.98, 0.98, and 0.98**. Recall-at-5 is 1.00, 1.00, and 1.00.

Identical. Across all three.

Which means my benchmark cannot distinguish plain keyword search from the full hybrid-plus-graph pipeline. Twenty-nine questions, all of them easy enough that the simplest mode already answers them perfectly. There is no headroom left for anything to be better *in*.

So the graph layer — the piece I'd describe first if you asked what makes this interesting, the thing I've spent the most design effort on — has **no evidence behind it whatsoever.** Not weak evidence. None. My own harness, the one I built specifically so I wouldn't have to trust my feelings about retrieval quality, has been printing a number that flatters everything and discriminates nothing.

The comparison project measures the same question properly, and the contrast is instructive. It runs a public benchmark rather than a homemade one, reports a considerably less pretty headline figure — 0.522 — and then does the step I never thought to do: an **isolation run**, turning components off one at a time to attribute the gain. Its result is that the entity graph accounts for roughly 80% of its improvement. That's a real claim about a graph layer. Mine was a vibe with a decimal point on it.

The lesson here is not "benchmark your work." I did benchmark my work. The lesson is narrower and easier to miss:

> A benchmark that every configuration passes is not a benchmark. It's a formality. If your variants all score the same, the instrument is measuring the difficulty of your test set, not the quality of your system.

And a saturated benchmark is worse than none, because it *feels* like rigor. It produces a number, the number goes in a document, and the document starts getting quoted. I published that 0.98 in a project scorecard four times without once noticing that the three figures next to each other were the same figure.

Four failures now, and they rhyme. The outages were invisible because silence looked like health. The gate was invisible because a broken check looked like an annoying one. The benchmark was invisible because a useless measurement looked like a good score. In every case the system was reporting *something*, and the something was reassuring, and that was exactly the problem.

## Where it honestly stands

31 repositories wired in. 76 stored memories contributed from 25 projects. 127 automatic recalls across 20 projects, 80 interactive searches with a 96% hit rate, 91 recorded applications. 148 open drift alerts against requirement documents. 66 tests, no database required to run them.

And the number that matters most: **the user count is one.**

Every search and every application is mine. A second person has contributed exactly one memory — the fix for outage one. So the central premise of this system, that memory becomes more valuable when *shared*, is currently unproven. I have strong evidence it works for one person across many projects. I have no evidence at all for many people across many projects, and no commit I write can produce that evidence.

That's the honest scorecard: a healthy system with real telemetry, one confirmed user, a graph layer with no evidence behind it yet, and a measurement layer I trust a little more each time it catches me being wrong — which is now four times.

---

## If any of this is your problem too

The repository is at **github.com/ArthurKeen/arango-shared-memory** — MIT, self-hostable, and it bootstraps into an existing project with a single script.

Three things would genuinely help, in descending order of value:

**Be the second reader.** The single most valuable open item in this project is a second human searching and applying memories, because it's the one thing that converts the premise from plausible to demonstrated. It cannot be fixed with code.

**Break the measurement.** The 44% reuse figure is flattering itself by an amount I can't yet quantify, and I'd rather hear where else that's true. The apply-gate bug above was found by someone treating a nagging prompt as evidence of a design error rather than an annoyance to endure.

**Steal the patterns, skip the repo.** Three, if you take nothing else. Test the path your consumer actually uses, not the health of the thing it depends on. Check whether any gate you've built can be satisfied honestly — because if it can't, the people around it are already working around it, and your metrics already know. And go look at whether your variants all score the same on your benchmark; if they do, you don't have a benchmark, you have a formality.

**And read a competitor's source.** Four rounds of reviewing my own project found none of what one afternoon in someone else's found. Introspection has sharply diminishing returns compared to reading an adjacent implementation by someone who made different choices.

*Comments and issues welcome, particularly the uncomfortable ones.*
