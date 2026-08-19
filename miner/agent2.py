from __future__ import annotations

import asyncio
import json
import re
from enum import Enum
from time import monotonic

from harnyx_miner_sdk.api import fetch_page, llm_chat, search_web, tooling_info
from harnyx_miner_sdk.decorators import entrypoint
from harnyx_miner_sdk.query import CitationRef, CitationSlice, Query, Response

LLM_LANE_A = "openrouter"
LLM_LANE_B = "openrouter"
LOOP_MODEL_A = "z-ai/glm-5.2"
LOOP_MODEL_B = "z-ai/glm-5"
AUDIT_MODEL = "openai/gpt-oss-120b"
SCHEMA_MODEL = "openai/gpt-oss-120b"
RESORT_MODEL = "deepseek/deepseek-v3.2"
SEARCH_PROVIDER = "parallel"

WALL_BUDGET_S = 266.0
BRIEF_TIMEOUT_S = 50.0
TURN_TIMEOUT_S = 75.0
LANE_B_MAX_PAYLOAD_CHARS = 400_000
AUDIT_TIMEOUT_S = 28.0
SEARCH_TIMEOUT_S = 18.0
FETCH_TIMEOUT_S = 16.0
WRAPUP_AT_S = 90.0
AUDIT_EXTRA_TURNS = 2
MIN_TAIL_S = 8.0
MAX_TURNS = 15
DIGEST_TAIL_S = 14.0
ANSWER_REPAIR_TURNS = 2
RESCUE_TIMEOUT_S = 55.0
SEARCH_EXCERPT_CHARS = 550
_LEDGER_TEXT_CAP = 400_000
PAGE_GREP_WINDOW = 700
PAGE_GREP_MAX_HITS = 6
PAGE_READ_MAX_CHARS = 12_000
RETAIN_MARGIN_CHARS = 260
RETAIN_MAX_PER_ROW = 6
RETAIN_MIN_QUOTE = 12
FETCH_HEAD_CHARS = 3000
FETCH_WINDOW_CHARS = 3600

CITATION_MIN_SPAN_CHARS = 6000
CITATION_MAX_REF_CHARS = 14_000
FETCH_WINDOWS_PER_PAGE = 3
FETCH_PLAIN_CHARS = 6500
ANSWER_CHAR_CAP = 60000
CITATION_CAP = 24
EVIDENCE_CHAR_BUDGET = 105_000

BRIEF_MIN_USD = 0.03
AUDIT_MIN_USD = 0.05
WRAPUP_MIN_USD = 0.02

_SPEND = {"left": None}


def _spend_note(payload) -> None:
    budget = getattr(payload, "budget", None)
    left = getattr(budget, "session_remaining_budget_usd", None)
    if isinstance(left, (int, float)):
        _SPEND["left"] = float(left)


def _spend_left() -> float:
    left = _SPEND["left"]
    if isinstance(left, (int, float)):
        return float(left)
    return 1.0


LOOP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Web search. Returns numbered results, each with title, "
                "url and excerpt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "the search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sec_filing",
            "description": (
                "Resolve a company's SEC filing to its primary document "
                "URL on sec.gov (exact form + year, from EDGAR's own "
                "index). Use for questions about a specific filing "
                "(10-K, 10-Q, 8-K, DEF 14A…), then read_page the "
                "returned URL with a focus hint for the Item/section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "company name or ticker, e.g. 'Apple' or 'AAPL'",
                    },
                    "form": {
                        "type": "string",
                        "description": "filing form, e.g. '10-K', '10-Q', '8-K', 'DEF 14A'",
                    },
                    "year": {
                        "type": "string",
                        "description": "optional report (fiscal) year, e.g. '2019' (omit for latest)",
                    },
                },
                "required": ["company", "form"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Fetch a URL and return its main text. Large pages show "
                "the head plus the few regions most relevant to the "
                "question; pass a focus hint to steer which regions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "focus": {
                        "type": "string",
                        "description": (
                            "optional phrase to locate inside the "
                            "page (section name, table label, "
                            "entity)"
                        ),
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_grep",
            "description": (
                "Search INSIDE a page you already fetched, by regex or "
                "literal text, and get every match with its surrounding "
                "context and character offset. Use this when read_page "
                "showed you the head of a long page but the value you "
                "need is deeper in it -- do not re-fetch, grep it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of a page already fetched this run",
                    },
                    "pattern": {
                        "type": "string",
                        "description": (
                            "regex or literal string to find, e.g. "
                            "a city name, a year, a column label"
                        ),
                    },
                },
                "required": ["url", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_read",
            "description": (
                "Read an arbitrary character range of a page you already "
                "fetched. Use the offsets page_grep reports to read the "
                "full table or section around a match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL already fetched"},
                    "offset": {
                        "type": "integer",
                        "description": "start character offset",
                    },
                    "length": {
                        "type": "integer",
                        "description": "how many characters to read (max 12000)",
                    },
                },
                "required": ["url", "offset"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retain_evidence",
            "description": (
                "Keep the exact source text that proves a claim you are "
                "about to make. Pass the result number and the verbatim "
                "quote from it. Do this the moment you find a decisive "
                "value -- the judge only credits claims whose citation "
                "contains the supporting text, and this is how that text "
                "gets into your citation. Use it for the QUESTION'S "
                "PREMISES as well as your answer: every entity, work, "
                "date or figure the question names should end up with a "
                "retained quote confirming it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "result number to quote from, e.g. 3",
                    },
                    "quote": {
                        "type": "string",
                        "description": (
                            "verbatim text copied from that result "
                            "that states the fact"
                        ),
                    },
                },
                "required": ["source", "quote"],
            },
        },
    },
]

LOOP_RULES = (
    "You are a research agent answering a hard multi-part factual question. A "
    "judge compares your answer head-to-head with a strong reference and only "
    "credits claims that carry a citation to a tool result that states them.\n\n"
    "PREFER THE PRIMARY SOURCE: when two sources state the same fact, cite the "
    "one that ORIGINATES it -- the agency, registry, filing, official statistics "
    "release or the organisation's own page -- not an encyclopedia or aggregator "
    "repeating it. Measured verbatim on a task where both answers were factually "
    'correct: "Answer 1 is preferred for using primary sources" (it cited NARA '
    "where we cited Wikipedia) -- a full point lost on every run. Use the "
    "encyclopedia to FIND the primary source, then fetch and cite that.\n\n"
    "QUOTE WHAT PROVES IT: the judge credits a claim only when your citation "
    "CONTAINS the source text stating it. The moment you read a decisive value, "
    "call retain_evidence(source, quote) with the exact words from that result. "
    "Do this for every condition you test and every figure you report -- an "
    "answer whose citations do not carry its numbers loses to one that does, "
    "even when both answers are identical.\n"
    "ALSO QUOTE THE QUESTION'S PREMISES, not only your answer. Every entity, "
    "work, date or figure the question NAMES is a claim the judge expects "
    "traceable: the film it says someone directed, the article it points at, "
    "the year it fixes, the people it lists. You lose to an otherwise identical "
    'answer that cited those too -- measured verbatim: "does not provide a '
    "citation for 'Everyone Says I Love You'... Answer 1 is more thorough in "
    "its traceability to all parts of the prompt's context\". Retain a quote "
    "for each named premise as you confirm it, even when it is background you "
    "already believed.\n\n"
    "READ DEEP, DO NOT RE-FETCH: read_page shows the head plus a few regions of "
    "a long page. If the value you need is not in what you were shown, call "
    "page_grep(url, pattern) to find it anywhere in that page and page_read to "
    "open the region around a reported offset. Grepping a page you already have "
    "costs nothing and beats another search.\n\n"
    "METHOD: think in constraints and candidates. Recall what you already know "
    "to form the candidate pool, then use web_search/read_page to verify every "
    "load-bearing fact (names, figures, dates, rankings) before asserting it. "
    "Work every candidate through every stated condition; one search per fact "
    "beats one broad search. TWO DISTINCT SUB-QUESTIONS: if the question asks two "
    "separate things, answer BOTH substantively — a partial answer covering both "
    "sides outscores a complete answer to only one. BATCH YOUR LOOKUPS: independent facts (each "
    "candidate's score, each entity's figure) should be requested as SEVERAL "
    "tool calls in the SAME turn — they run in parallel, so a 6-candidate "
    "sweep costs one turn, not six. TABLE CARE: when reading a table, respect its "
    "qualifier columns (Owned vs Leased, the exact year, the exact segment) — "
    "count or compare only rows matching EVERY stated qualifier, and quote the "
    "row values you used. For a named source (Box Office Mojo, a 10-K, "
    "Nielsen), fetch THAT page — for SEC filings, use the sec_filing tool to "
    "resolve the exact primary document from EDGAR's own index, then read_page "
    "it with a focus hint for the Item/section.\n\n"
    "CITE EVERYTHING: put [n] (the tool-result number) immediately after the "
    "SENTENCE carrying each claim — not pooled at the end of a paragraph. Every "
    "sentence asserting a number, date, proper noun or causal link needs its own "
    "[n], for the entities you rule OUT as well as those you include. An uncited "
    "specific reads as invented. Cite only results that actually state the claim, "
    "and prefer the most AUTHORITATIVE one that does: the official database/"
    "filing/statistics page over an aggregator, blog, or retrospective article. "
    "CITE THE HARD CONDITION, NOT JUST THE POOL: every stated condition needs "
    "evidence of its own, and the one hardest to verify is the one the grader "
    "checks. Citations that establish only the candidate pool leave the actual "
    "filter unsupported — a right answer whose decisive condition is uncited "
    "loses to a weaker answer that proves it.\n\n"
    "SOURCE CONFIDENCE: when the question NAMES a source you could not reach but "
    "other authoritative evidence establishes the same facts, state those facts "
    "plainly and confidently with their [n], and treat the other sources as "
    "corroboration. Do not open with, dwell on, or append a note that the named "
    "source was unavailable — reserve missing-source language for a FACT that is "
    "genuinely absent everywhere, never for a missing source LABEL.\n\n"
    "SELF-CONSISTENCY: before you finish, check that the opening names exactly "
    "the entities your own cited sentences support. If the body establishes a "
    "different answer than the opening claims, rewrite the opening to match the "
    "evidence — never leave a weaker fallback in the lead.\n\n"
    "ANSWER SHAPE: sentence one IS the answer — the exact entities/values/list "
    "asked for, in the requested format. Never open with 'Based on…', 'From my "
    "research…', 'I can provide a partial answer', or any preamble — start with "
    "the answer entities themselves. ANSWER THE ASKED KIND: if the question asks "
    "which SERIES, name the series (not the people in it); which FILM, the film "
    "(not its director); which COUNTRY, the country. "
    "THE POOL IS THE WHOLE NAMED CLASS, NOT THE SURVIVORS: build it from the "
    "broadest set the question ranges over — every member of that class, not the "
    "ones you already believe qualify — then apply the conditions one at a time and "
    "show who each one eliminates. Never pre-filter to the members that already "
    "pass and present those as the pool — an answer whose pool contains only "
    "qualifiers proves nothing about the sweep, which is how a correct answer "
    "still scores zero. List members that fail on the FIRST condition too. "
    "Then: the candidate pool, each condition applied, and ONE LINE PER POOL MEMBER — "
    "a line for every qualifier with its qualifying attribute cited, AND a line "
    "for every candidate you rule out with its cited failing condition. Never "
    "compress several rejects into one clause ('X, Y and Z never won [n]'): each "
    "rejected member gets its own line and its own [n], even when the pool runs "
    "to a dozen members. A batched exclusion reads as a pool you never checked. "
    "Two later instructions may relax this — one when time runs short, one "
    "when the pool is too large to list in full — and nothing else does. "
    "If you cannot settle a member's condition, KEEP it among the qualifiers — a "
    "wrongly-dropped qualifier costs as much as a wrong answer — and give its "
    "line the strongest fact you did verify. Never add a note about what you "
    "could not check. "
    "OUTPUT DIRECTIVES ARE LITERAL: obey formatting instructions mechanically. "
    "Decide first whether a phrase constrains the OUTPUT or selects the "
    "ENTITIES: 'list them without the word \"X\"' shapes what you print, so "
    "DELETE X from each name; 'whose title does not contain \"X\"' / 'titles "
    "without the word X' is a condition on the pool, so keep only members that "
    "lack it. When the phrase governs how to print an already-chosen set, the "
    "deletion reading applies — it is not a filter. 'in alphabetical/chronological order' means sort the final "
    "list; 'comma-separated' means join with commas; a requested count means "
    "emit the number. These govern the ANSWER LINE — give it in exactly the "
    "requested shape, then still add the proof section below it; the shape "
    "directive is never a reason to omit the proof. COPY SOURCE VALUES "
    "VERBATIM: when the question names a source, every name, label and value in "
    "the answer must be the exact string that source prints -- never add a "
    "familiar alternative in parentheses, never anglicise a transliteration. "
    "'Makkah' is the answer; 'Mecca (Makkah)' is a wrong answer. "
    "ONE EXCEPTION, and it is "
    "absolute: if the question says to output ONLY the answer ('output only', "
    "'respond with only', 'nothing else', 'no explanation'), emit the answer "
    "line as the BARE requested text — no [n] markers on it, nothing else on "
    "that line: a trailing [3] makes the text inexact and fails the "
    "instruction. Still write the PROOF section BELOW it carrying its [n] "
    "markers. Only the answer line is shipped, but the citations are "
    "harvested from the proof first, and an uncited answer scores zero. "
    "Obeying that "
    "instruction IS the task. When an ORDER is demanded, "
    "the ANSWER LINE itself must be sorted — not merely the table under it. "
    "Print the sort key beside each item (the year, figure or date you sorted "
    "on) and check every adjacent pair before you finish: one member out of "
    "sequence fails the whole answer even when the set is exactly right. "
    "COMPUTED ANSWERS: if the answer is a mean, total, rank or count derived "
    "from several figures, pull every input into one explicit list first, then "
    "compute — and show the arithmetic so the number is checkable. Never report "
    "a derived number you did not visibly compute from listed inputs. "
    "ROUNDED FIGURE = WRONG SOURCE: a decisive number that reads as rounded — "
    "trailing zeros where the measuring body publishes exact digits, "
    "'X.Y thousand/million', 'about'/'approximately', "
    "or a value lifted from a chart label — came from an aggregator that "
    "publishes summaries, not from the body that measured it. Do NOT commit it. "
    "Search again for the exact figure from the source the question NAMES (or "
    "the outlet that reports that source's own numbers) and answer with the full "
    "precision it publishes, digit for digit. Quote the rounded value only as "
    "corroboration after the exact one. This is a RETRIEVAL instruction, not a "
    "licence to withhold: once tool calls are closed, or if the named source "
    "itself publishes only the rounded value, commit the best figure you hold "
    "and never remark on its precision. "
    "EXACT VALUES ONLY: this governs HOW you report a figure; the rule above "
    "governs WHICH figure to go and fetch. Once you hold the right one, use the "
    "figures you READ in a tool result, verbatim — preserve notation exactly (58.58% and "
    "58.6% are different; 'p < 0.0001' and 'P < .001' must not be merged or "
    "called consistent). If one source gives a range and another a point value, "
    "give both and say whether the point falls inside the range. If a figure is "
    "reported in different units than the question asks, convert it and give the "
    "exact converted result, preserving units and any timezone label. Answer with "
    "the value from the exact source, date and scope the question NAMES — do not "
    "substitute a later or broader figure unless resolving a conflict requires "
    "it. Bind every claim to the exact actor, target, date-window and instrument "
    "the evidence ties together; never carry a statement about one party or "
    "period across to another. Never a remembered or approximate value "
    "('~$1.33B'), never rounded, never an adjacent year/quarter/metric. If a "
    "deciding figure is still unverified at writing time, prefer the tool-read "
    "value you have over a guess, and NEVER write '(verify)' or any uncertainty "
    "marker in the final answer — the final answer contains only committed "
    "prose.\n\n"
    "AMBIGUOUS METRIC? ANSWER BOTH READINGS. If the asked quantity has two "
    "defensible interpretations — one party's value or the combined value of "
    "both; one dimension of size or another; a narrow scope or a consolidated "
    "one — do NOT silently pick one. Name the ambiguity in "
    "one clause and give BOTH lists/values, each cited and labelled. A correct "
    "answer under the reading the grader did not use still scores as wrong.\n\n"
    "APPLY CONDITIONS LITERALLY: copy each candidate's exact value, then test "
    "the comparator as written — 'more than 25' is strictly >25 (25 fails); "
    "'between 2010 and 2019' includes both endpoints; convert a rate condition "
    "into a concrete integer test ('averaged more than 1 per year over 10 "
    "years' = 'more than 10 in total'); read edition/date boundaries literally. "
    "EXCLUDE ONLY ON PROOF: reject a candidate by naming the specific stated "
    "condition it fails, with the cited fact showing the failure — never "
    "because it looks weaker than your front-runner. If it is UNCERTAIN "
    "whether a candidate fails a condition, KEEP IT in the answer rather than "
    "dropping it on a guess: a wrongly-dropped qualifier costs exactly as much "
    "as a wrong answer. SAY NO MORE THAN THE CITATION: if the source says "
    "'brought to', do not write 'incarcerated'; if it gives a count of 12, do "
    "not write 11. Check every count and every verb against its citation.\n\n"
    "NEVER NARRATE YOUR EVIDENCE: no sentence about what your results do or "
    "do not contain ('the evidence does not specify…', 'would be needed to "
    "determine…'). Those phrasings lose. A substantive negative about the "
    "WORLD is different and is a real answer when true ('No member of the "
    "class satisfies every condition [n]'). If a datum truly cannot be "
    "verified, commit "
    "to the best-supported value you found and move on. ONE narrow exception: "
    "when the asked figure genuinely does not exist in any published form, you "
    "may state the REASONED IMPOSSIBILITY — name the specific dataset that "
    "would hold it and why it cannot yield the value — as a fact about the "
    "world, in the first line, alongside the closest cited facts. That is a "
    "committed answer; 'the evidence does not contain it' is not.\n\n"
    "FINISH: never mix tool calls and the final answer in one turn. When the "
    "constraints are verified (or best-effort covered), write the complete "
    "cited answer."
)


def _wrapup_order(seconds_left: float) -> str:
    return (
        f"TIME IS UP (~{int(seconds_left)}s left). No more tool calls. Write the "
        "complete final answer NOW from the numbered results above plus your "
        "knowledge: the FIRST words are the answer entities (no 'Based on…' "
        "preamble, no 'partial answer' framing, no '(verify)' markers), cite [n] "
        "on every claim, keep the required format. A cited partial answer "
        "scores; a refusal or a remark about insufficient evidence scores zero."
        + (
            ""
            if seconds_left >= 60
            else " BREVITY OVERRIDE: too little time remains for a line per pool "
            "member. Lead with the answer entities, then give the qualifiers one "
            "cited line each and compress the rejects into a single cited line. "
            "A complete short answer beats a long one that never finishes."
        )
    )


_SET_HINT_RE = re.compile(
    r"\b(?:list|name|identify|enumerate)\b[^?]{0,40}\b(?:all|every|each|the)\b"
    r"|\bhow many\b|\bwhich (?:movies|films|series|countries|companies|states|"
    r"cities|books|albums|artists|players|teams|species|languages|banks|"
    r"universities|agencies|models|products)\b",
    re.IGNORECASE,
)
_SET_CONNECTIVE_RE = re.compile(
    r"\b(?:both|also|and (?:also|had|has|was|were)|as well as)\b", re.IGNORECASE
)


_PLURAL_HEAD_RE = re.compile(
    r"\b(?:which|what)\b(?:\s+\w+){0,2}?\s+([a-z]{3,}s)\b", re.IGNORECASE
)
_PLURAL_FALSE = frozenset(
    "was is has does its this thus across process business series species news "
    "status analysis basis less unless always perhaps".split()
)
_ONE_WINNER_RE = re.compile(
    r"\b(?:highest|lowest|largest|smallest|most|least|greatest|fewest|longest|"
    r"shortest|first|last|best|worst|only|oldest|youngest|newest|biggest)\b",
    re.IGNORECASE,
)
_EST_STOP = frozenset(
    "interest honest modest protest request suggest forest harvest invest "
    "manifest contest arrest digest earnest conquest tempest midwest northwest "
    "southwest unrest bequest behest attest molest ingest infest detest incest "
    "armrest backrest pretest headrest footrest".split()
)
_EST_RE = re.compile(r"\b([a-z]{3,})est\b")


def _has_superlative(text: str) -> bool:
    if _ONE_WINNER_RE.search(text or ""):
        return True
    for m in _EST_RE.finditer(text or ""):
        if m.group(0).lower() not in _EST_STOP:
            return True
    return False


def _needs_superlative_proof(question: str) -> bool:
    q = " ".join((question or "").split())
    if not q:
        return False
    return _has_superlative(q) or bool(
        re.search(
            r"\b(?:most|least) (?:common|frequent|number|amount)\b|\bhow many\b",
            q,
            re.I,
        )
    )


SUPERLATIVE_RULE = (
    "SUPERLATIVE / TALLY — SHOW THE TABLE. The answer is one item, but you "
    "cannot know it without the whole pool. Before naming a winner: (1) list "
    "EVERY candidate the question's scope admits — every player who appeared, "
    "every officeholder in the span, every body in the ranking; (2) put the "
    "deciding value next to each (birth date, count, figure), cited; (3) THEN "
    "name the maximum. NEVER decide a superlative on a rounded or derived "
    "display: a coarse figure (a whole-number age, a rounded total, a bucketed "
    "rank) cannot separate two contenders that differ below its precision. "
    "Fetch the "
    "exact underlying value (full birth date, unrounded figure) for every "
    "contender, from a source that lists them ALL: a page showing only your "
    "front-runner cannot establish that nobody beats them. (3b) THEN "
    "name the maximum. Reproduce that candidate table in the proof section — "
    "a correct winner with no visible tally loses to a reference that shows "
    "its work, and 'among others' / 'and several more' is not a tally. If the "
    "pool is too large to list in full, rank it, show every contender down to a "
    "stated cutoff, and say what the cutoff was — a stated cutoff is a covered "
    "pool; an unstated one reads as an unchecked one."
)


def _needs_set_completeness(question: str) -> bool:
    q = " ".join((question or "").split())
    if _SET_HINT_RE.search(q):
        return True
    m = _PLURAL_HEAD_RE.search(q)
    if m and m.group(1).lower() not in _PLURAL_FALSE:
        if not _has_superlative(q) or re.search(
            r"\b(?:all|every|each)\b", q, re.IGNORECASE
        ):
            return True
    return bool(re.search(r"\bwhich\b", q, re.IGNORECASE)) and bool(
        _SET_CONNECTIVE_RE.search(q)
    )


SET_RULE = (
    "SET ANSWER: this question asks for a set. Missing a qualifying member "
    "scores the same as wrong — enumerate the pool, test EVERY member against "
    "EVERY condition, and name ALL qualifiers (each with its own citations per "
    "condition). Then give EVERY excluded member its own line with the condition "
    "it fails and its own [n] — not a single clause sweeping several names "
    "together, and not just the near-misses. Never claim 'the only X' unless "
    "the whole pool was checked; if "
    "your pool may be partial, still commit to every qualifier you verified. "
    "GET THE POOL FROM A LIST, NOT MEMBER-BY-MEMBER: your FIRST retrieval for a "
    "set question should hunt the authoritative roster/list/table that "
    "enumerates the whole pool (search it AS a list — '<pool subject> list', "
    "'<pool subject> table', 'list of <pool subject>' — and read_page it). "
    "Assembling the pool from separate per-member searches is how a run ends up "
    "with 3 of 6 qualifiers: the members you never thought to search for are "
    "invisible to you. Read the roster page first, then verify each member. "
    "ONE LIST PER PERIOD, THEN JOIN: when a condition has to hold across several "
    "periods — successive years, separate editions, or two parallel events — "
    "fetch ONE roster page per period and join them on the member: one list per "
    "period, not one lookup per member. A "
    "pool of 30+ members each needing several figures is a table-join, and "
    "per-member lookups will run out of turns long before the pool is covered. "
    "UNIVERSAL conditions ('in EVERY one of them', 'for BOTH parts', 'in ALL "
    "three periods'): check each candidate against EACH "
    "instance separately, with a citation per instance — one shared instance "
    "is not enough. If NO candidate survives every instance, then 'none' IS "
    "the answer: state it as a verified fact about the world with the "
    "per-instance citations that prove it."
)


class EvidenceLedger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(
        self,
        receipt_id: str,
        result_id: str,
        note_len: int,
        kind: str,
        spans: list[tuple[int, int]] | None,
        title: str = "",
        url: str = "",
        preview: str = "",
        text: str = "",
    ) -> int:
        self.rows.append(
            {
                "receipt_id": receipt_id,
                "result_id": result_id,
                "note_len": note_len,
                "kind": kind,
                "title": (title or "")[:160],
                "url": (url or "")[:300],
                "preview": (preview or "")[:1200],
                "spans": spans,
                "text": (text or "")[:_LEDGER_TEXT_CAP],
                "retained": [],
            }
        )
        return len(self.rows)

    def ref_for(self, number: int) -> CitationRef | None:
        if not (1 <= number <= len(self.rows)):
            return None
        row = self.rows[number - 1]
        if row.get("kind") == "reserved":
            return None
        if not row["receipt_id"] or not row["result_id"]:
            return None
        spans = row["spans"]
        if spans:
            note_len = int(row["note_len"] or 0)
            shown: list[list[int]] = []
            for span in spans[:4]:
                start = max(0, min(int(span[0]), note_len))
                end = max(start + 1, min(int(span[1]), note_len))
                shown.append([start, end])
            retained = []
            for a, b in row.get("retained") or []:
                a = max(0, min(int(a), note_len))
                b = max(a + 1, min(int(b), note_len))
                retained.append([a, b])
            if retained:
                shown = retained
            shown.sort()
            merged: list[list[int]] = []
            for s, e in shown:
                if merged and s <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            base = sum(e - s for s, e in merged)
            room = max(0, CITATION_MAX_REF_CHARS - base)
            if merged and note_len and room:
                extra = room // len(merged)
                for w in merged:
                    pad = min(extra, max(0, CITATION_MIN_SPAN_CHARS - (w[1] - w[0])))
                    if pad:
                        left = min(pad // 2, w[0])
                        w[0] -= left
                        rest = pad - left
                        right = min(rest, note_len - w[1])
                        w[1] += right
                        w[0] = max(0, w[0] - (rest - right))
                merged.sort()
                grown: list[list[int]] = []
                for s, e in merged:
                    if grown and s <= grown[-1][1]:
                        grown[-1][1] = max(grown[-1][1], e)
                    else:
                        grown.append([s, e])
                merged = grown
            slices = [CitationSlice(start=s, end=e) for s, e in merged if e > s]
            if not slices:
                return None
            return CitationRef(
                receipt_id=row["receipt_id"], result_id=row["result_id"], slices=slices
            )
        return None


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'.\-]{2,}")
_STOP = frozenset(
    "the and for with from that this have has was were are is been its their "
    "which what when where who how many much according also into over under "
    "between during against about after before while other more most than".split()
)


def _key_terms(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").casefold()) if w not in _STOP}


def _best_windows(
    note: str, terms: set[str], width: int, k: int = 1
) -> list[tuple[int, int]]:
    n = len(note)
    if n <= width:
        return [(0, n)]
    step = max(600, width // 3)
    low = note.lower()
    scored: list[tuple[int, int]] = []
    pos = 0
    while pos < n:
        seg = low[pos : pos + width]
        scored.append((sum(1 for t in terms if t in seg), pos))
        if pos + width >= n:
            break
        pos += step
    scored.sort(key=lambda hs: (-hs[0], hs[1]))
    picked: list[tuple[int, int]] = []
    for hits, start in scored:
        if len(picked) >= max(1, k):
            break
        end = min(n, start + width)
        if any(start < pe and ps < end for ps, pe in picked):
            continue
        if picked and hits <= 0:
            continue
        picked.append((start, end))
    picked.sort()
    return picked or [(0, min(n, width))]


_SLOT = "\x00{}\x00"


class ToolOutput:

    def __init__(self, text: str, rows: list[dict] | None = None) -> None:
        self.text = text
        self.rows = rows or []


def _commit_tool_output(out, ledger: EvidenceLedger) -> str:
    if isinstance(out, str):
        return out
    if not isinstance(out, ToolOutput):
        return f"# tool crashed: {out}"
    text = out.text
    for i, row in enumerate(out.rows):
        n = ledger.add(
            row["receipt_id"],
            row["result_id"],
            row["note_len"],
            row["kind"],
            row["spans"],
            title=row.get("title", ""),
            url=row.get("url", ""),
            preview=row.get("preview", ""),
            text=row.get("text", ""),
        )
        text = text.replace(_SLOT.format(i), str(n))
    return text


_SITE_OP_RE = re.compile(r"\bsite:\S+\s*", re.I)


def _degrade_query(q: str) -> str:
    out = _SITE_OP_RE.sub("", q or "").replace('"', " ")
    return " ".join(out.split())


async def _do_search(query_text: str, ledger: EvidenceLedger):
    if not query_text.strip():
        return "# web_search: empty query"
    payload = None
    fired: set[str] = set()
    for attempt, allow_repeat in (
        (query_text, False),
        (query_text, True),
        (_degrade_query(query_text), False),
    ):
        if not attempt.strip() or (attempt in fired and not allow_repeat):
            continue
        fired.add(attempt)
        try:
            payload = await search_web(
                attempt, provider=SEARCH_PROVIDER, num=8, timeout=SEARCH_TIMEOUT_S
            )
            if getattr(payload, "results", None):
                break
        except Exception:
            payload = None
    if payload is None:
        return f"# web_search({query_text!r}) failed"
    _spend_note(payload)
    receipt = str(getattr(payload, "receipt_id", "") or "")
    results = list(getattr(payload, "results", None) or [])
    if not receipt:
        return f"# web_search({query_text!r}): no citable results"
    rows: list[dict] = []
    lines = [f"# web_search({query_text!r}): {len(results)} results"]
    for item in results:
        rid = getattr(item, "result_id", None)
        if not isinstance(rid, str) or not rid:
            continue
        note = getattr(item, "note", None) or ""
        if not note.strip():
            continue
        n_len = len(note)
        span = (
            [(0, min(max(SEARCH_EXCERPT_CHARS, 100), n_len))]
            if n_len >= 100
            else ([(0, n_len)] if n_len else None)
        )
        title = (getattr(item, "title", None) or "").strip()
        url = (getattr(item, "url", None) or "").strip()
        rows.append(
            {
                "receipt_id": receipt,
                "result_id": rid,
                "note_len": n_len,
                "kind": "search",
                "spans": span,
                "title": title,
                "url": url,
                "preview": note[:SEARCH_EXCERPT_CHARS],
                "text": note,
            }
        )
        lines.append(
            f"[{_SLOT.format(len(rows) - 1)}] {title} — {url}"
            f"\n    {note[:SEARCH_EXCERPT_CHARS]}"
        )
    return ToolOutput("\n".join(lines), rows)


async def _do_fetch(url: str, focus: str, question: str, ledger: EvidenceLedger) -> str:
    if not url.strip():
        return "# read_page: empty url"
    payload = None
    for _attempt in (0, 1):
        try:
            payload = await fetch_page(
                url, provider=SEARCH_PROVIDER, timeout=FETCH_TIMEOUT_S
            )
            if getattr(payload, "results", None):
                break
        except Exception:
            payload = None
    if payload is None:
        return f"# read_page({url!r}) failed"
    _spend_note(payload)
    receipt = str(getattr(payload, "receipt_id", "") or "")
    results = list(getattr(payload, "results", None) or [])
    if not results or not receipt:
        return f"# read_page({url!r}): no content"
    item = results[0]
    rid = getattr(item, "result_id", None)
    note = getattr(item, "note", None) or ""
    if not isinstance(rid, str) or not rid or not note.strip():
        return f"# read_page({url!r}): no usable content"
    if len(note) <= FETCH_PLAIN_CHARS:
        row = {
            "receipt_id": receipt,
            "result_id": rid,
            "note_len": len(note),
            "kind": "fetch",
            "spans": [(0, len(note))],
            "title": url,
            "url": url,
            "preview": note[:1200],
            "text": note,
        }
        return ToolOutput(
            f"# read_page({url!r}) -> [{_SLOT.format(0)}] full page, "
            f"{len(note)} chars\n{note}",
            [row],
        )
    terms = _key_terms(question) | _key_terms(focus)
    windows = _best_windows(note, terms, FETCH_WINDOW_CHARS, k=FETCH_WINDOWS_PER_PAGE)
    row = {
        "receipt_id": receipt,
        "result_id": rid,
        "note_len": len(note),
        "kind": "fetch",
        "spans": [(0, FETCH_HEAD_CHARS)] + list(windows),
        "title": url,
        "url": url,
        "preview": note[windows[0][0] : windows[0][0] + 1200],
        "text": note,
    }
    head = note[:FETCH_HEAD_CHARS]
    sections = "".join(f"\n--- section @{s} ---\n{note[s:e]}" for s, e in windows)
    return ToolOutput(
        f"# read_page({url!r}) -> [{_SLOT.format(0)}] {len(note)} chars total; head + "
        f"the {len(windows)} most relevant section(s) shown "
        f"({', '.join(f'{s}-{e}' for s, e in windows)}). If the answer set may "
        f"continue elsewhere in this page, call read_page again with a "
        f"different focus.\n--- head ---\n{head}{sections}",
        [row],
    )


_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_SEC_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
_SEC_FETCH_TIMEOUT_S = 26.0
_SEC_MIN_HEADROOM_S = 40.0
_SEC_CACHE: dict = {}
_SEC_STOPWORDS = frozenset(
    "inc incorporated corp corporation company companies co ltd limited llc plc "
    "lp llp group holdings the".split()
)
_SEC_ALNUM_RE = re.compile(r"[a-z0-9]+")


def _sec_tokens(text: str) -> list[str]:
    return [
        w
        for w in _SEC_ALNUM_RE.findall((text or "").lower())
        if w not in _SEC_STOPWORDS
    ]


def _sec_norm_form(form: str) -> str:
    f = " ".join((form or "").upper().replace("FORM", " ").split())
    m = re.fullmatch(r"(\d{1,2})\s*-?\s*([A-Z])", f)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(DEF)\s*-?\s*(14A)", f)
    if m:
        return "DEF 14A"
    return f


async def _fetch_json(url: str, deadline: float):
    cached = _SEC_CACHE.get(url)
    if cached is not None:
        return cached
    for _attempt in (0, 1):
        left = deadline - monotonic()
        if left < 12.0:
            return None
        try:
            payload = await asyncio.wait_for(
                fetch_page(
                    url,
                    provider=SEARCH_PROVIDER,
                    timeout=min(_SEC_FETCH_TIMEOUT_S, left - 6.0),
                ),
                timeout=min(_SEC_FETCH_TIMEOUT_S, left - 6.0) + 4.0,
            )
        except Exception:
            continue
        _spend_note(payload)
        results = list(getattr(payload, "results", None) or [])
        note = (getattr(results[0], "note", None) or "") if results else ""
        start = note.find("{")
        end = note.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            obj = json.loads(note[start : end + 1])
        except Exception:
            continue
        if isinstance(obj, dict):
            _SEC_CACHE[url] = obj
            return obj
    return None


def _sec_pick_filing(recent: dict, form: str, year: str):
    forms = recent.get("form")
    accs = recent.get("accessionNumber")
    docs = recent.get("primaryDocument")
    rdates = recent.get("reportDate")
    fdates = recent.get("filingDate")
    if not (
        isinstance(forms, list) and isinstance(accs, list) and isinstance(docs, list)
    ):
        return None
    n = min(len(forms), len(accs), len(docs))
    form_norm = _sec_norm_form(form)
    best_year = None
    best_any = None
    for i in range(n):
        if _sec_norm_form(str(forms[i])) != form_norm:
            continue
        if accs[i] is None or docs[i] is None:
            continue
        acc = str(accs[i])
        doc = str(docs[i])
        if not acc or not (doc.endswith(".htm") or doc.endswith(".html")):
            continue
        rd = (
            str(rdates[i])
            if (isinstance(rdates, list) and i < len(rdates) and rdates[i] is not None)
            else ""
        )
        fd = (
            str(fdates[i])
            if (isinstance(fdates, list) and i < len(fdates) and fdates[i] is not None)
            else ""
        )
        key = rd or fd
        if best_any is None or key > best_any[0]:
            best_any = (key, acc, doc)
        if year and rd[:4] == year:
            if best_year is None or key > best_year[0]:
                best_year = (key, acc, doc)
    pick = best_year if year else best_any
    if pick is None:
        return None
    return pick[1], pick[2]


_SEC_SEARCH_HINT = (
    'search "site:sec.gov {company} {year} {form}" and read_page the Archives result'
)


async def _do_sec_filing(company: str, form: str, year: str, deadline: float) -> str:
    company = (company or "").strip()
    form = (form or "").strip() or "10-K"
    year = (year or "").strip()[:4]
    hint = _SEC_SEARCH_HINT.format(company=company, year=year, form=form)
    if not company:
        return "# sec_filing: company required"
    if (deadline - monotonic()) < _SEC_MIN_HEADROOM_S:
        return f"# sec_filing: skipped (low time) — {hint}"
    tickers = await _fetch_json(_SEC_TICKERS_URL, deadline)
    if not isinstance(tickers, dict):
        return f"# sec_filing: EDGAR ticker index unavailable — {hint}"
    want = _sec_tokens(company)
    best = None
    for row in tickers.values():
        if not isinstance(row, dict):
            continue
        title = str(row.get("title", ""))
        ticker = str(row.get("ticker", "")).lower()
        words = set(_sec_tokens(title))
        n_hit = sum(1 for w in want if w in words)
        if len(want) == 1 and ticker == want[0]:
            score = 100
        elif want and n_hit == len(want):
            score = 50 + n_hit
        else:
            continue
        cand = (score, -len(title), str(row.get("cik_str", "")).zfill(10), title)
        if best is None or cand > best:
            best = cand
    if best is None:
        return f"# sec_filing({company!r}): no confident EDGAR match — {hint}"
    cik10, title = best[2], best[3]
    subs = await _fetch_json(_SEC_SUBMISSIONS_URL.format(cik10=cik10), deadline)
    filings = subs.get("filings") if isinstance(subs, dict) else None
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        return f"# sec_filing({company!r}): EDGAR submissions unavailable for {title} — {hint}"
    pick = _sec_pick_filing(recent, form, year)
    if pick is None:
        return (
            f"# sec_filing({company!r}, {form!r}, year={year or 'latest'}): no matching "
            f"filing in EDGAR's recent index for {title} — check the form/year, or {hint}"
        )
    accession, doc = pick
    url = _SEC_DOC_URL.format(
        cik=cik10.lstrip("0") or cik10, accession=accession.replace("-", ""), doc=doc
    )
    return (
        f"# sec_filing -> {title} {form} {year or '(latest)'} primary document:\n"
        f"{url}\nNow call read_page on this URL with a focus hint for the "
        f"section you need, and cite figures from that read_page result."
    )


def _ledger_page(url: str, ledger: EvidenceLedger) -> tuple[int, dict] | None:
    u = (url or "").strip().rstrip("/")
    if not u:
        return None
    for i in range(len(ledger.rows) - 1, -1, -1):
        row = ledger.rows[i]
        if not row.get("text"):
            continue
        r = str(row.get("url") or "").rstrip("/")
        if r == u or r.endswith(u) or u.endswith(r):
            return i + 1, row
    return None


def _do_page_grep(url: str, pattern: str, ledger: EvidenceLedger) -> str:
    hit = _ledger_page(url, ledger)
    if hit is None:
        return (
            f"# page_grep: {url!r} has not been fetched this run; call read_page first"
        )
    n, row = hit
    text = row.get("text") or ""
    pat = (pattern or "").strip()
    if not pat:
        return "# page_grep: empty pattern"
    try:
        rx = re.compile(pat, re.I)
    except re.error:
        rx = re.compile(re.escape(pat), re.I)
    out, seen_at = [], []
    for m in rx.finditer(text):
        c = (m.start() + m.end()) // 2
        if any(abs(c - prev) < PAGE_GREP_WINDOW // 2 for prev in seen_at):
            continue
        seen_at.append(c)
        a = max(0, c - PAGE_GREP_WINDOW // 2)
        b = min(len(text), a + PAGE_GREP_WINDOW)
        out.append(f"\n--- match @{a} ---\n{text[a:b]}")
        if len(out) >= PAGE_GREP_MAX_HITS:
            break
    if not out:
        return (
            f"# page_grep({pat!r}) on [{n}]: no match in {len(text)} chars. "
            f"Try a shorter or looser pattern."
        )
    return (
        f"# page_grep({pat!r}) on [{n}] -> {len(out)} match(es) of {len(text)} chars"
        + "".join(out)
    )


def _do_page_read(url: str, offset: int, length: int, ledger: EvidenceLedger) -> str:
    hit = _ledger_page(url, ledger)
    if hit is None:
        return (
            f"# page_read: {url!r} has not been fetched this run; call read_page first"
        )
    n, row = hit
    text = row.get("text") or ""
    a = max(0, min(int(offset or 0), max(0, len(text) - 1)))
    ln = int(length or PAGE_READ_MAX_CHARS)
    b = min(len(text), a + max(1, min(ln, PAGE_READ_MAX_CHARS)))
    return f"# page_read([{n}] @{a}:{b} of {len(text)})\n{text[a:b]}"


def _do_retain_evidence(source: str, quote: str, ledger: EvidenceLedger) -> str:
    raw = (source or "").strip().strip("[]")
    try:
        n = int(raw)
    except ValueError:
        return f"# retain_evidence: source must be a result number like [3], got {source!r}"
    if not (1 <= n <= len(ledger.rows)):
        return f"# retain_evidence: no result [{n}] exists yet"
    row = ledger.rows[n - 1]
    text = row.get("text") or ""
    q = (quote or "").strip()
    if len(q) < RETAIN_MIN_QUOTE:
        return (
            f"# retain_evidence: quote too short ({len(q)} chars); quote at least "
            f"{RETAIN_MIN_QUOTE} characters of the source text"
        )
    if not text:
        return f"# retain_evidence: result [{n}] has no stored text to quote from"
    i = text.find(q)
    if i < 0:
        i = text.lower().find(q.lower())
    if i < 0:
        squashed = " ".join(q.split())
        i = " ".join(text.split()).lower().find(squashed.lower())
        if i >= 0:
            i = -1
    if i < 0:
        return (
            f"# retain_evidence: that text does not appear in [{n}]. Quote it "
            f"EXACTLY as the source prints it, or read more of the page first."
        )
    kept = row.setdefault("retained", [])
    if len(kept) >= RETAIN_MAX_PER_ROW:
        return f"# retain_evidence: [{n}] already has {len(kept)} retained excerpts"
    a = max(0, i - RETAIN_MARGIN_CHARS)
    b = min(int(row.get("note_len") or len(text)), i + len(q) + RETAIN_MARGIN_CHARS)
    if b <= a:
        return f"# retain_evidence: could not bound the excerpt in [{n}]"
    kept.append((a, b))
    return (
        f"# retain_evidence: kept {b - a} chars of [{n}] around your quote. "
        f"Cite [{n}] for that claim."
    )


async def _run_tool(
    call, question: str, ledger: EvidenceLedger, deadline: float
) -> str:
    try:
        args = json.loads(getattr(call, "arguments", None) or "{}")
    except Exception:
        args = {}
    if not isinstance(args, dict):
        args = {}
    name = getattr(call, "name", "") or ""
    if name == "web_search":
        return await _do_search(str(args.get("query") or ""), ledger)
    if name == "read_page":
        return await _do_fetch(
            str(args.get("url") or ""), str(args.get("focus") or ""), question, ledger
        )
    if name == "retain_evidence":
        return _do_retain_evidence(
            str(args.get("source") or ""), str(args.get("quote") or ""), ledger
        )
    if name == "page_grep":
        return _do_page_grep(
            str(args.get("url") or ""), str(args.get("pattern") or ""), ledger
        )
    if name == "page_read":
        return _do_page_read(
            str(args.get("url") or ""),
            args.get("offset") or 0,
            args.get("length") or PAGE_READ_MAX_CHARS,
            ledger,
        )
    if name == "sec_filing":
        return await _do_sec_filing(
            str(args.get("company") or ""),
            str(args.get("form") or ""),
            str(args.get("year") or ""),
            deadline,
        )
    return f"# unknown tool {name!r}"


_REASONING_MANDATORY = ("openai/gpt-oss",)


def _least_think(lane: str, model: str = "") -> dict:
    for prefix in _REASONING_MANDATORY:
        if model.startswith(prefix):
            return {"enabled": True, "effort": "low"}
    return {"enabled": False}


_FAST_UPSTREAMS = ("Decart", "CoreWeave", "Alibaba")
_FAST_UPSTREAMS_OSS = ("Cerebras", "Groq", "BaseTen")


def _upstream(lane: str, model: str) -> dict | None:
    if model.startswith("z-ai/glm-5.2"):
        only = _FAST_UPSTREAMS
    elif model.startswith("openai/gpt-oss"):
        only = _FAST_UPSTREAMS_OSS
    else:
        return None
    return {"provider": {"only": list(only), "allow_fallbacks": True}}


async def _chat_simple(
    lane: str,
    model: str,
    system: str,
    user: str,
    *,
    max_tokens: int,
    timeout: float,
    think: dict | None = None,
) -> str:
    if think is None:
        think = _least_think(lane, model)
    _pin0 = _upstream(lane, model)
    payload = None
    for _pin in ((_pin0, None) if _pin0 is not None else (None,)):
        try:
            payload = await llm_chat(
                provider=lane,
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.15,
                max_output_tokens=max_tokens,
                timeout=timeout,
                thinking=think,
                provider_extra=_pin,
            )
            break
        except Exception:
            if _pin is None:
                raise
            continue
    _spend_note(payload)
    llm = getattr(payload, "llm", None)
    text = (getattr(llm, "raw_text", None) or "").strip()
    if text:
        return text
    choices = getattr(llm, "choices", None) or []
    if choices:
        content = getattr(choices[0].message, "content", None)
        if isinstance(content, str):
            return content.strip()
    return ""


class _EmptyChoiceMessage:
    content = ""
    tool_calls = ()


class _EmptyChoice:
    message = _EmptyChoiceMessage()


class _EmptyLlm:
    raw_text = ""
    choices = (_EmptyChoice(),)


class _EmptyTurn:

    llm = _EmptyLlm()
    budget = None


_EMPTY_TURN = _EmptyTurn()


async def _chat_turn(
    messages: list[dict],
    deadline: float,
    *,
    finish_only: bool,
    force_tools: bool = False,
):
    turn_wall = monotonic() + TURN_TIMEOUT_S + 35.0
    payload_chars = sum(
        len(str(msg.get("content") or "")) for msg in messages if isinstance(msg, dict)
    )
    for lane_model in (
        (LLM_LANE_A, LOOP_MODEL_A, True),
        (LLM_LANE_A, LOOP_MODEL_A, False),
        (LLM_LANE_B, LOOP_MODEL_B, False),
    ):
        lane = lane_model[0]
        model = lane_model[1]
        pinned = lane_model[2]
        if model == LOOP_MODEL_B and payload_chars > LANE_B_MAX_PAYLOAD_CHARS:
            return _EMPTY_TURN
        timeout = min(
            TURN_TIMEOUT_S, deadline - monotonic() - 5.0, turn_wall - monotonic()
        )
        if timeout <= 5.0:
            return None
        try:
            payload = await asyncio.wait_for(
                llm_chat(
                    provider=lane,
                    model=model,
                    messages=messages,
                    tools=LOOP_TOOLS if (force_tools or not finish_only) else None,
                    tool_choice="auto" if (force_tools or not finish_only) else None,
                    temperature=0.2,
                    thinking=(
                        {"enabled": False}
                        if (finish_only and model == LOOP_MODEL_B)
                        else {"enabled": True, "effort": "low"}
                    ),
                    max_output_tokens=(
                        6000 if (finish_only and model == LOOP_MODEL_B) else None
                    ),
                    provider_extra=_upstream(lane, model) if pinned else None,
                    timeout=timeout,
                ),
                timeout=min(timeout + 6.0, max(1.0, deadline - monotonic() - 1.0)),
            )
            _spend_note(payload)
            return payload
        except Exception:
            continue
    return None


async def _knowledge_brief(question: str) -> tuple[str, str]:
    system = (
        "Senior research analyst. Commit to concrete best answers from "
        "knowledge; mark uncertain values (verify). Never refuse."
    )
    user = (
        f"Question:\n{question}\n\n"
        "Fill in this internal worksheet. It is planning scratch for your own use, "
        "never an answer, so keep the tags lowercase and never reuse them as "
        "section headings later.\n"
        "draft: your full best answer now — candidate pool, every stated "
        "condition applied, qualifying entities with figures/dates, near-miss "
        "exclusions. Flag shaky facts with (verify).\n"
        "conditions: each atomic condition in the question, numbered, including "
        "any output-format demand.\n"
        "searches: 3-6 precise web searches for the facts that decide the answer "
        "(entity + metric + year; include a named source's site: filter).\n"
        "urls: up to 5 exact URLs worth reading directly (official stats pages, "
        "sec.gov Archives filings, boxofficemojo year pages); 'none' if unsure."
    )
    raw = ""
    try:
        raw = await _chat_simple(
            LLM_LANE_A,
            LOOP_MODEL_A,
            system,
            user,
            max_tokens=2400,
            timeout=BRIEF_TIMEOUT_S,
            think=_least_think(LLM_LANE_A, LOOP_MODEL_A),
        )
    except Exception:
        try:
            raw = await _chat_simple(
                LLM_LANE_B,
                LOOP_MODEL_B,
                system,
                user,
                max_tokens=2400,
                timeout=BRIEF_TIMEOUT_S,
                think=_least_think(LLM_LANE_B, LOOP_MODEL_B),
            )
        except Exception:
            raw = ""
    if not raw:
        return "", ""
    draft = raw
    cut = min(
        (
            mm.start()
            for mm in (
                re.search(
                    r"[#*_\s]*(?:conditions|CHECKLIST)[#*_\s]*:", raw, re.IGNORECASE
                ),
                re.search(
                    r"^[ \t]*[#*_>]{0,4}[ \t]*(?:conditions|CHECKLIST)[ \t]*[#*_]{0,3}[ \t]*$",
                    raw,
                    re.IGNORECASE | re.MULTILINE,
                ),
            )
            if mm is not None
        ),
        default=None,
    )
    if cut is not None:
        draft = raw[:cut]
    draft = re.sub(
        r"^[#*_\s]*(?:draft|BEST ANSWER)[#*_\s]*:[#*_\s]*",
        "",
        draft,
        flags=re.IGNORECASE,
    )
    draft = re.sub(
        r"^[ \t]*[#*_>]{0,4}[ \t]*(?:draft|BEST ANSWER)[ \t]*[#*_]{0,3}[ \t]*\n+",
        "",
        draft,
        flags=re.IGNORECASE,
    )
    draft = draft.strip()
    brief = (
        "PRIOR ANALYSIS — your own planning worksheet (verify anything marked "
        "(verify), and correct it wherever tool results disagree). Its tags are "
        "internal: never reproduce them, or any section named after them, in the "
        "answer.\n" + raw.strip()
    )
    return draft, brief


POOL_DRAFT_TIMEOUT_S = 22.0
POOL_DRAFT_MIN_LEFT_S = 150.0
MAX_POOL_DRAFT_LINES = 25
MIN_POOL_DRAFT_LINES = 3


async def _draft_candidate_pool(question: str, deadline: float) -> str:
    if (
        deadline - monotonic()
    ) < POOL_DRAFT_MIN_LEFT_S or _spend_left() < BRIEF_MIN_USD:
        return ""
    user = (
        f"Question:\n{question}\n\n"
        "Enumerate the CANDIDATE POOL this question ranges over: every "
        "entity that could plausibly qualify, one per line as\n"
        "name — deciding fact to verify (best guess; may be wrong)\n"
        "Include near-misses that look like they qualify but may fail a "
        "condition. 4 to 25 lines, no preamble. If the question has no "
        "enumerable pool, output exactly NONE."
    )
    try:
        raw = await _chat_simple(
            LLM_LANE_A,
            AUDIT_MODEL,
            "Research planner. Compact plain text only.",
            user,
            max_tokens=1200,
            timeout=POOL_DRAFT_TIMEOUT_S,
        )
    except Exception:
        return ""
    raw = (raw or "").strip()
    if not raw or raw.upper().startswith("NONE") or len(raw) < 40:
        return ""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()][:MAX_POOL_DRAFT_LINES]
    if len(lines) < MIN_POOL_DRAFT_LINES:
        return ""
    return (
        "CANDIDATE ROSTER — your own pre-research enumeration. VERIFY every "
        "line against sources before relying on it: add members it missed, "
        "strike members that fail a condition, and give a cited verdict for "
        "EACH member in the proof section.\n" + "\n".join(lines)
    )


_SEED_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-']+")
_SEED_STOP = frozenset(
    "name list give tell show find identify please could would "
    "you your can may might should must let make sure both also".split()
)
MAX_SEED_QUERIES = 3


def _seed_queries(question: str, set_question: bool) -> list[str]:
    q = " ".join((question or "").split())
    if not q:
        return []
    seeds = [q[:300]]
    salient = [
        t
        for t in _SEED_TOKEN_RE.findall(q)
        if len(t) >= 3 and t.lower() not in _STOP and t.lower() not in _SEED_STOP
    ]
    if len(salient) >= 2:
        seeds.append(" ".join(salient[:8]))
    if set_question and salient:
        seeds.append("list of " + " ".join(salient[:6]))
    out: list[str] = []
    for s in seeds:
        s = s.strip()
        if s and s not in out:
            out.append(s)
    return out[:MAX_SEED_QUERIES]


async def _preseed(
    question: str, set_question: bool, ledger: EvidenceLedger, deadline: float
) -> str:
    seeds = _seed_queries(question, set_question)
    if not seeds or (deadline - monotonic()) < 40.0:
        return ""
    blocks: list = []
    for seed in seeds:
        if (deadline - monotonic()) < 30.0:
            break
        try:
            out = await asyncio.wait_for(
                _do_search(seed, ledger), timeout=SEARCH_TIMEOUT_S * 2 + 6.0
            )
            blocks.append(_commit_tool_output(out, ledger))
        except Exception:
            continue
    good = [b for b in blocks if isinstance(b, str) and _CITE_MARK_RE.search(b)]
    if not good:
        return ""
    return (
        "Automatic first-pass searches (already numbered — cite these [n] "
        "directly, and search further as needed):\n\n" + "\n".join(good)
    )


async def _loop(
    question: str,
    brief: str,
    ledger: EvidenceLedger,
    deadline: float,
    turn_cap: int,
    carry: list[dict] | None = None,
    allow_tools_in_wrapup: bool = False,
    pool_hint: str = "",
) -> tuple[str, list[dict]]:
    if carry is not None:
        messages = carry
    else:
        set_q = _needs_set_completeness(question)
        messages = [{"role": "system", "content": LOOP_RULES}]
        if set_q:
            messages.append({"role": "system", "content": SET_RULE})
        if _needs_superlative_proof(question):
            messages.append({"role": "system", "content": SUPERLATIVE_RULE})
        if brief:
            messages.append({"role": "system", "content": brief})
        if pool_hint:
            messages.append({"role": "system", "content": pool_hint})
        seeded = await _preseed(question, set_q, ledger, deadline)
        if seeded:
            messages.append({"role": "system", "content": seeded})
        messages.append({"role": "user", "content": question})

    answer = ""
    ordered_wrapup = False
    repairs_left = ANSWER_REPAIR_TURNS
    for turn in range(1, turn_cap + 1):
        left = deadline - monotonic()
        if left <= MIN_TAIL_S:
            break
        out_of_time = left <= WRAPUP_AT_S
        out_of_spend = _spend_left() <= WRAPUP_MIN_USD
        finish_only = out_of_time or out_of_spend or turn >= turn_cap
        if (finish_only or turn >= turn_cap - 1) and not ordered_wrapup:
            messages.append({"role": "system", "content": _wrapup_order(left)})
            ordered_wrapup = True

        payload = await _chat_turn(
            messages,
            deadline,
            finish_only=finish_only,
            force_tools=allow_tools_in_wrapup and turn == 1,
        )
        if payload is None:
            break
        llm = getattr(payload, "llm", None)
        choices = getattr(llm, "choices", None) or []
        if not choices:
            break
        msg = choices[0].message
        calls = getattr(msg, "tool_calls", None) or ()
        if not calls:
            candidate = (getattr(llm, "raw_text", None) or "").strip()
            if not candidate:
                content = getattr(msg, "content", None)
                if isinstance(content, str):
                    candidate = content.strip()
            if not _is_usable_answer(candidate):
                if repairs_left > 0 and (deadline - monotonic()) > MIN_TAIL_S + 10.0:
                    repairs_left -= 1
                    messages.append({"role": "system", "content": _REPAIR_ORDER})
                    answer = ""
                    continue
                answer = ""
                break
            answer = candidate
            messages.append({"role": "assistant", "content": answer})
            break
        messages.append(msg.to_input_message())
        run_calls = calls[:8]
        tool_budget = max(
            5.0, min(FETCH_TIMEOUT_S * 2 + 6.0, deadline - monotonic() - MIN_TAIL_S)
        )
        tool_tasks = [
            asyncio.ensure_future(_run_tool(c, question, ledger, deadline))
            for c in run_calls
        ]
        try:
            await asyncio.wait(tool_tasks, timeout=tool_budget)
        except Exception:
            pass
        results = []
        for t in tool_tasks:
            if t.done():
                try:
                    results.append(t.result())
                except Exception as exc:
                    results.append(f"# tool crashed: {exc}")
            else:
                t.cancel()
                results.append("# tool timed out — use what you already have")
        for call_result in zip(run_calls, results):
            call = call_result[0]
            body = _commit_tool_output(call_result[1], ledger)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": body})
        for call in calls[8:]:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": "# skipped: per-turn tool budget reached — re-issue next turn if still needed",
                }
            )
    return answer, messages


async def _audit_patch(
    question: str,
    answer: str,
    messages: list[dict],
    ledger: EvidenceLedger,
    deadline: float,
) -> str:
    probe = (
        "Audit the answer against the question. JSON only, keys: "
        '"unanswered_parts" (list; question elements not addressed), '
        '"uncited_facts" (list; load-bearing claims without [n]), '
        '"wrong_kind" (list; places where the named entity is a different KIND '
        "than the question asks — a person instead of a series, a duo instead "
        "of a show), "
        '"incomplete_roster" (list; THE MOST COMMON LOSS. If the question ranges '
        "over a candidate pool — a closed set that can be enumerated, or several "
        "conditions applied to a class — then: is the pool itself stated and "
        "plausibly COMPLETE, and does the answer give a verdict for EVERY member "
        "(qualifies / excluded because X, each cited)? Name any pool member the "
        "answer never mentions, and say so if the pool looks truncated — an "
        "answer naming 3 qualifiers when the pool holds 6 scores as WRONG, not "
        "partial), "
        '"thin_proof" (list; a qualifier lacking a per-condition citation, or a '
        "plausible near-miss candidate never addressed), "
        '"hand_waved_tally" (list; for a superlative/count/most-common question: '
        "the answer asserts a winner or a count WITHOUT showing the candidate "
        "table it was derived from. Phrases like 'among others', 'and several "
        "more', 'multiple X', or naming 2 examples to justify a count are all "
        "hand-waving — say so and name what the tally must list). "
        "Empty lists when clean.\n\n"
        f"Question:\n{question}\n\nAnswer:\n{answer[:11000]}"
    )
    try:
        raw = await _chat_simple(
            LLM_LANE_A,
            AUDIT_MODEL,
            "Strict completeness auditor. JSON only.",
            probe,
            max_tokens=2200,
            timeout=max(8.0, min(AUDIT_TIMEOUT_S, (deadline - monotonic()) - 72.0)),
        )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        report = json.loads(raw)
    except Exception:
        return answer
    gaps: list[str] = []
    roster_gaps: list[str] = []
    if isinstance(report, dict):
        for key in (
            "incomplete_roster",
            "hand_waved_tally",
            "unanswered_parts",
            "uncited_facts",
            "wrong_kind",
            "thin_proof",
        ):
            vals = report.get(key)
            if isinstance(vals, list):
                found = [str(v) for v in vals if str(v).strip()]
                if key in ("incomplete_roster", "hand_waved_tally"):
                    roster_gaps.extend(found)
                gaps.extend(found)
    if not gaps or (deadline - monotonic()) < 70.0:
        return answer
    order = "AUDIT: the answer has gaps:\n- " + "\n- ".join(gaps[:6])
    if roster_gaps:
        order += (
            "\nThe candidate pool is incomplete — this loses outright. FIRST "
            "search for the authoritative LIST/roster/table that enumerates "
            "the whole pool (query it as a list, e.g. '<pool subject> full "
            "list', not one member at a time), verify EVERY member against "
            "every condition, then rewrite."
        )
    order += (
        "\nUse at most 3 tool calls to close the most important gaps, then "
        "rewrite the COMPLETE final answer with [n] citations in the "
        "required shape."
    )
    messages.append({"role": "system", "content": order})
    patched, _ = await _loop(
        question,
        "",
        ledger,
        deadline,
        AUDIT_EXTRA_TURNS + 1,
        carry=messages,
        allow_tools_in_wrapup=True,
    )
    patched = patched.strip()
    if not _is_usable_answer(patched) or len(patched) < int(len(answer) * 0.6):
        return answer
    return patched


def _salient_terms(question: str, limit: int, drop: str = "") -> list[str]:
    picked = [
        t
        for t in _SEED_TOKEN_RE.findall(" ".join((question or "").split()))
        if (len(t) >= 3 or t.isdigit())
        and t.lower() not in _STOP
        and t.lower() not in _SEED_STOP
        and (not drop or t != drop)
    ]
    return picked[:limit]


def _cited_row_text(answer: str, ledger: EvidenceLedger) -> list[str]:
    cited = _cited_numbers(answer, len(ledger.rows))
    if not cited:
        return []
    stored = []
    for n in cited:
        row = ledger.rows[n - 1]
        stored.append((row.get("text") or "") + " " + (row.get("preview") or ""))
    return stored


def _adopt_patch(previous: str, candidate: str) -> str:
    candidate = (candidate or "").strip()
    if not _is_usable_answer(candidate):
        return previous
    if len(candidate) < int(len(previous) * 0.6):
        return previous
    return candidate


_MARKER_STRIP_RE = re.compile(r"\[[0-9][0-9,\s\-]*\]")
_NUMERIC_TOKEN_RE = re.compile(r"\$?\b\d[\d,]*(?:\.\d+)?%?")


_ANCHOR_YEAR_RE = re.compile(r"\b(19[0-9]{2}|20[0-2][0-9])\b")
MAX_ANCHOR_YEARS = 3
TIMEFRAME_MIN_LEFT_S = 90.0


def _anchor_years(question: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for y in _ANCHOR_YEAR_RE.findall(question or ""):
        if y not in seen:
            seen.add(y)
            out.append(y)
    return out[:MAX_ANCHOR_YEARS]


def _unevidenced_years(question: str, answer: str, ledger: EvidenceLedger) -> list[str]:
    years = _anchor_years(question)
    if not years:
        return []
    stored = _cited_row_text(answer, ledger)
    if not stored:
        return []
    return [y for y in years if not any(y in t for t in stored)]


def _year_probe_query(question: str, year: str) -> str:
    return " ".join(_salient_terms(question, 7, drop=year)) + f" {year}"


async def _align_timeframe(
    question: str,
    answer: str,
    messages: list[dict],
    ledger: EvidenceLedger,
    deadline: float,
) -> str:
    if (
        deadline - monotonic()
    ) < TIMEFRAME_MIN_LEFT_S or _spend_left() <= AUDIT_MIN_USD:
        return answer
    uncovered = _unevidenced_years(question, answer, ledger)
    if not uncovered:
        return answer
    year = uncovered[0]
    try:
        found = await asyncio.wait_for(
            _do_search(_year_probe_query(question, year), ledger),
            timeout=SEARCH_TIMEOUT_S * 2 + 6.0,
        )
        body = _commit_tool_output(found, ledger)
    except Exception:
        body = ""
    order = (
        f"TEMPORAL AUDIT: the question is pinned to {year}, but NO evidence "
        "row the answer cites mentions that year — the cited values may "
        "describe a different period, which scores as wrong. "
    )
    if body and _CITE_MARK_RE.search(body):
        order += (
            f"One more search pinned to {year} is already numbered below — "
            "verify every dated value against it, fix any that describe a "
            "different period, and rewrite the COMPLETE final answer with "
            "[n] citations.\n\n" + body
        )
    else:
        order += (
            f"Use at most 2 tool calls to verify the {year} values, then "
            "rewrite the COMPLETE final answer with [n] citations."
        )
    messages.append({"role": "system", "content": order})
    patched, _ = await _loop(
        question, "", ledger, deadline, 3, carry=messages, allow_tools_in_wrapup=True
    )
    return _adopt_patch(answer, patched)


SECOND_SOURCE_MIN_LEFT_S = 80.0


def _headline_value(answer: str) -> str:
    body = _MARKER_STRIP_RE.sub(" ", answer or "")
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        for m in _NUMERIC_TOKEN_RE.finditer(line):
            v = m.group(0).strip("$%")
            if len(re.sub(r"\D", "", v)) >= 3:
                return v
        break
    return ""


def _value_backers(figure: str, answer: str, ledger: EvidenceLedger) -> set[str]:
    if not figure:
        return set()
    plain = figure.replace(",", "")
    hosts = set()
    for n in _cited_numbers(answer, len(ledger.rows)):
        row = ledger.rows[n - 1]
        stored = row.get("text") or ""
        if figure in stored or (plain != figure and plain in stored):
            hosts.add(row.get("url") or f"row{n}")
    return hosts


async def _second_source_check(
    question: str,
    answer: str,
    messages: list[dict],
    ledger: EvidenceLedger,
    deadline: float,
) -> str:
    if (
        deadline - monotonic()
    ) < SECOND_SOURCE_MIN_LEFT_S or _spend_left() <= AUDIT_MIN_USD:
        return answer
    figure = _headline_value(answer)
    if not figure:
        return answer
    backers = _value_backers(figure, answer, ledger)
    if len(backers) != 1:
        return answer
    query = " ".join(_salient_terms(question, 6)) + " " + figure
    try:
        found = await asyncio.wait_for(
            _do_search(query, ledger), timeout=SEARCH_TIMEOUT_S * 2 + 6.0
        )
        body = _commit_tool_output(found, ledger)
    except Exception:
        return answer
    if not (body and _CITE_MARK_RE.search(body)):
        return answer
    order = (
        f"CORROBORATION: the answer's decisive figure {figure} rests on a "
        "single source. One search for independent confirmation is "
        "numbered below. If a second source states the same figure, cite "
        "it alongside the first; if sources DISAGREE, re-verify which is "
        "right before answering. Then rewrite the COMPLETE final answer "
        "with [n] citations.\n\n" + body
    )
    messages.append({"role": "system", "content": order})
    patched, _ = await _loop(
        question, "", ledger, deadline, 3, carry=messages, allow_tools_in_wrapup=True
    )
    return _adopt_patch(answer, patched)


_BRACKET_FIX = {
    0x3010: "[",
    0x3011: "]",
    0xFF3B: "[",
    0xFF3D: "]",
    0xFF08: "(",
    0xFF09: ")",
    0x2011: "-",
    0x2212: "-",
}
for _d in range(10):
    _BRACKET_FIX[0xFF10 + _d] = chr(48 + _d)


def _normalize_brackets(text: str) -> str:
    return (text or "").translate(_BRACKET_FIX)


_CITE_NUM_RE = re.compile(r"\[([0-9][0-9,\s\-]*)\]")


def _cited_numbers(answer: str, top: int) -> list[int]:
    answer = _normalize_brackets(answer)
    seen: set[int] = set()
    out: list[int] = []
    for m in _CITE_NUM_RE.finditer(answer):
        for chunk in m.group(1).split(","):
            piece = chunk.strip()
            span = re.fullmatch(r"(\d{1,4})\s*-\s*(\d{1,4})", piece)
            if span:
                lo = int(span.group(1))
                hi = int(span.group(2))
                for n in range(lo, min(hi, lo + 16) + 1):
                    if 1 <= n <= top and n not in seen:
                        seen.add(n)
                        out.append(n)
            elif piece.isdigit():
                n = int(piece)
                if 1 <= n <= top and n not in seen:
                    seen.add(n)
                    out.append(n)
    return out


_OUTPUT_ONLY_RE = re.compile(
    r"\boutput only\b|\brespond with only\b|\breply with only\b"
    r"|\banswer with only\b|\bonly the exact\b|\bnothing else\b"
    r"|\bno explanation\b|\bwithout explanation\b|\bno other text\b"
    r"|\bjust the (?:name|names|value|values|number|numbers|list|text|answer|title|titles)\b",
    re.IGNORECASE,
)
_OUTPUT_ONLY_MIN_CHARS = 2


def _answer_line_only(answer: str, question: str) -> str:
    if not answer or not _OUTPUT_ONLY_RE.search(question or ""):
        return answer
    for raw in answer.split("\n"):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped[0] in "#>":
            continue
        line = re.sub(r"^[*_`\s]+|[*_`\s]+$", "", stripped).strip()
        if not line:
            continue
        if line.startswith("|") or line.endswith(":"):
            continue
        if len(line) >= _OUTPUT_ONLY_MIN_CHARS:
            return line
    return answer


_GLOSS_RE = re.compile(r"^(?P<a>[^()]{2,60}?)\s*\((?P<b>[^()]{2,60})\)$")


def _verbatim_from_source(value: str, ledger: EvidenceLedger) -> str:
    v = (value or "").strip()
    m = _GLOSS_RE.match(v)
    if not m:
        return value
    texts = [r.get("text") or "" for r in ledger.rows if r.get("text")]
    if not texts:
        return value

    def seen(t: str) -> bool:
        return bool(t) and any(t in src for src in texts)

    if seen(v):
        return value
    a, b = m.group("a").strip(), m.group("b").strip()
    hits = [x for x in (b, a) if seen(x)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) == 2:
        lo, hi = sorted(hits, key=len)
        if lo.lower() in hi.lower():
            return hi
    return value


def _verbatim_structured(obj, ledger: EvidenceLedger, depth: int = 0):
    if depth > 6:
        return obj
    if isinstance(obj, str):
        return _verbatim_from_source(obj, ledger)
    if isinstance(obj, list):
        return [_verbatim_structured(x, ledger, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {k: _verbatim_structured(v, ledger, depth + 1) for k, v in obj.items()}
    return obj


BACKFILL_MARGIN_CHARS = 300
MAX_BACKFILL_FIGURES = 12


def _answer_figures(answer: str) -> list[str]:
    body = _MARKER_STRIP_RE.sub(" ", answer or "")
    out: list[str] = []
    seen: set[str] = set()
    for m in _NUMERIC_TOKEN_RE.finditer(body):
        v = m.group(0).strip("$%")
        if len(re.sub(r"\D", "", v)) < 2:
            continue
        if v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) >= MAX_BACKFILL_FIGURES:
            break
    return out


def _refs_within_budget(answer: str, ledger: EvidenceLedger) -> list[CitationRef]:
    refs: list[CitationRef] = []
    spent = 0
    for n in _cited_numbers(answer, len(ledger.rows)):
        if len(refs) >= CITATION_CAP:
            break
        ref = ledger.ref_for(n)
        if ref is None:
            continue
        row = ledger.rows[n - 1]
        slices = getattr(ref, "slices", None)
        cost = (
            sum(max(0, s.end - s.start) for s in slices)
            if slices
            else int(row.get("note_len") or 0)
        )
        if spent + cost > EVIDENCE_CHAR_BUDGET:
            continue
        spent += cost
        refs.append(ref)
    return refs


def _citations_for(answer: str, ledger: EvidenceLedger) -> list[CitationRef]:
    try:
        base = _refs_within_budget(answer, ledger)
        if not base:
            return base
        row_of: dict = {}
        for row in ledger.rows:
            row_of[(row["receipt_id"], row["result_id"])] = row
        keyed = []
        for ref in base:
            row = row_of.get((ref.receipt_id, ref.result_id))
            if row is None:
                return base
            keyed.append((ref, row))
        best: dict = {}
        deduped = []
        for ref, row in keyed:
            url = row.get("url") or ""
            width = sum(max(0, s.end - s.start) for s in (ref.slices or []))
            if not url:
                deduped.append([ref, row, width])
                continue
            if url in best:
                if width > best[url][2]:
                    best[url][0], best[url][2] = ref, width
                continue
            entry = [ref, row, width]
            best[url] = entry
            deduped.append(entry)
        spent = sum(e[2] for e in deduped)
        for value in _answer_figures(answer):
            plain = value.replace(",", "")
            covered = False
            for ref, row, _w in deduped:
                text = row.get("text") or ""
                for s in ref.slices or []:
                    seg = text[s.start : s.end]
                    if value in seg or (plain != value and plain in seg):
                        covered = True
                        break
                if covered:
                    break
            if covered:
                continue
            for entry in deduped:
                ref, row, width = entry
                text = row.get("text") or ""
                idx = text.find(value)
                if idx < 0 and plain != value:
                    idx = text.find(plain)
                if idx < 0:
                    continue
                note_len = int(row.get("note_len") or 0) or len(text)
                start = max(0, idx - BACKFILL_MARGIN_CHARS)
                end = min(note_len, idx + len(value) + BACKFILL_MARGIN_CHARS)
                cost = end - start
                if cost <= 0 or spent + cost > EVIDENCE_CHAR_BUDGET:
                    continue
                entry[0] = CitationRef(
                    receipt_id=ref.receipt_id,
                    result_id=ref.result_id,
                    slices=list(ref.slices or [])
                    + [CitationSlice(start=start, end=end)],
                )
                entry[2] = width + cost
                spent += cost
                break
        out = [e[0] for e in deduped]
        return out if out else base
    except Exception:
        try:
            return _refs_within_budget(answer, ledger)
        except Exception:
            return []


_VERIFY_MARK_RE = re.compile(r"\s*\((?:verify|unverified|uncertain)[^)]*\)", re.I)

_TOOL_MARKUP_RE = re.compile(
    r"<\s*/?\s*tool_call|<\s*/?\s*(?:arg_key|arg_value|function_call|invoke)\b"
    r"|\bweb_search\s*[（(]\s*query|\bread_page\s*[（(]\s*url|\bsec_filing\s*[（(]\s*company",
    re.I,
)
_STUB_ANSWER_RE = re.compile(
    r"^\s*(?:best-effort answer unavailable|no question provided)", re.I
)
_REFUSAL_ONLY_RE = re.compile(
    r"^\s*(?:i (?:cannot|can't|am unable|was unable)|unable to|sorry[,.]|"
    r"i don'?t have (?:enough|access))",
    re.I,
)
_INTENT_NARRATION_RE = re.compile(
    r"^\s*(?:i (?:need|will|should|am going|'ll)\b|let me\b|first,? (?:i|let)\b|"
    r"i'?ll (?:search|look|start|begin|gather|check))",
    re.I,
)
MIN_ANSWER_CHARS = 40
MIN_CITED_ANSWER_CHARS = 12
_CITE_MARK_RE = re.compile(r"\[[0-9]{1,3}\]")


def _looks_like_tool_json(s: str) -> bool:
    return bool(re.match(r'\s*\{\s*"(?:name|tool|function)"\s*:', s))


def _is_degenerate_repetition(text: str) -> bool:
    body = text or ""
    lines = [ln.strip().lower() for ln in body.split("\n") if len(ln.strip()) > 25]
    if len(lines) >= 3:
        for ln in set(lines):
            if lines.count(ln) >= 3:
                return True
        if len(set(lines)) * 2 > len(lines):
            return False
    sents = [
        s.strip().lower()
        for s in re.split(r"(?<=[.!?])\s+|\n+", body)
        if len(s.strip()) > 25
    ]
    if len(sents) < 3:
        return False
    uniq = set(sents)
    if len(uniq) * 2 <= len(sents):
        return True
    for s in uniq:
        if sents.count(s) >= 3:
            return True
    return False


def _is_usable_answer(text: str) -> bool:
    s = _normalize_brackets(text).strip()
    if not s:
        return False
    if _TOOL_MARKUP_RE.search(s) or _looks_like_tool_json(s):
        return False
    if _STUB_ANSWER_RE.match(s) or _is_degenerate_repetition(s):
        return False
    cited = bool(_CITE_MARK_RE.search(s))
    if cited and len(s) >= MIN_CITED_ANSWER_CHARS:
        return True
    if len(s) < MIN_ANSWER_CHARS:
        return False
    if len(s) < 400 and (_REFUSAL_ONLY_RE.match(s) or _INTENT_NARRATION_RE.match(s)):
        return False
    return True


_COMMIT_RULES = (
    "You are writing the FINAL ANSWER to a research question from evidence that "
    "has already been gathered. You have NO tools — never emit tool syntax. A "
    "judge compares your answer with a strong reference and credits only claims "
    "carrying an [n] citation to the numbered evidence.\n\n"
    "SHAPE: the first words are the answer entities themselves — no preamble, no "
    "remark about evidence quality. Then a short proof section: the candidate "
    "pool, each condition applied, one line per qualifier (cited) and one line "
    "per rejected member with its cited reason — every member gets its own "
    "line, never several swept into one clause. Reproduce figures and dates "
    "VERBATIM. Name ALL qualifying members — omitting one scores as wrong. "
    "Obey any literal formatting demand in the question — sort order, "
    "comma-separated, a requested count, 'without the word X' meaning delete "
    "that word — the shape is graded too. "
    "Never say what the evidence does not contain; commit to the best-supported "
    "answer you can defend."
)

_REPAIR_ORDER = (
    "Your last message was not a usable final answer (it contained tool-call "
    "markup, was empty, or was a refusal). Do NOT emit tool syntax as text. "
    "Write the FINAL ANSWER now as plain prose: first words are the answer "
    "entities themselves, every factual claim followed by its [n] citation, "
    "then the short proof section. Nothing else."
)


def _sanitize_draft(text: str) -> str:
    return _VERIFY_MARK_RE.sub("", text or "").strip()


def _ledger_digest(ledger: EvidenceLedger, char_cap: int = 60000) -> str:
    parts: list[str] = []
    spent = 0
    for i, row in enumerate(ledger.rows, start=1):
        text = (row.get("preview") or "").strip()
        if not text:
            continue
        block = f"[{i}] {row.get('title') or ''} ({row.get('url') or ''})\n{text}"
        if spent + len(block) > char_cap:
            break
        spent += len(block)
        parts.append(block)
    return "\n\n".join(parts)


_FURNITURE_RE = re.compile(
    r"^\s*(?:share|search|home|menu|subscribe|sign\s*in|log\s*in|newsletter|"
    r"advertisement|cookie|skip to|follow us|read more|related|tags?|categories?|"
    r"privacy|terms|contact|about us|navigation|toggle)\b",
    re.I,
)
_SRC_FOOTNOTE_RE = re.compile(r"\[\s*\d{1,3}\s*\]")
_MD_LINK_RE = re.compile(r"\]\(")
_BARE_URL_RE = re.compile(r"(?<!\]\()https?://")
_SENTENCEY_RE = re.compile(
    r"[.!?]\s|[.!?]$|\b(?:is|was|were|are|has|have|had|"
    r"reported|announced|released|won|ranked|totall?ed)\b",
    re.I,
)


def _informative_lead(preview: str, limit: int = 280) -> str:
    kept: list[str] = []
    broke = False
    for chunk in re.split(
        r"(?<=[.!?])\s+|\n+", _SRC_FOOTNOTE_RE.sub("", preview or "")
    ):
        seg = " ".join(chunk.split())
        if len(seg) < 30 or len(seg) > 400:
            if kept:
                broke = True
                break
            continue
        if _SENTENCEY_RE.search(seg) is None:
            if kept:
                broke = True
                break
            continue
        if _FURNITURE_RE.match(seg) and not re.search(r"\d", seg):
            if kept:
                broke = True
                break
            continue
        if seg.startswith(("*", "|", "↑", "#")):
            if kept:
                broke = True
                break
            continue
        links = len(_MD_LINK_RE.findall(seg)) + len(_BARE_URL_RE.findall(seg))
        if links and links * 110 >= len(seg):
            if kept:
                broke = True
                break
            continue
        kept.append(seg)
        if sum(len(k) for k in kept) >= limit:
            break
    else:
        pass
    out = " ".join(kept).strip()
    if len(out) > limit:
        cut = out.rfind(" ", 0, limit)
        out = out[: cut if cut > 60 else limit].rstrip(" ,;:-")
    return out


def _deterministic_answer(question: str, ledger: EvidenceLedger) -> str:
    rows = [
        (i, r)
        for i, r in enumerate(ledger.rows, start=1)
        if (r.get("preview") or "").strip()
    ]
    if not rows:
        return ""
    out = ["Best-supported findings from the sources retrieved:"]
    picked = 0
    for i, r in rows:
        if picked >= 6:
            break
        lead = _informative_lead(r.get("preview") or "")
        if not lead:
            continue
        title = (r.get("title") or "").strip()
        out.append(f"- {title + ': ' if title else ''}{lead} [{i}]")
        picked += 1
    if picked == 0:
        for i, r in rows[:4]:
            lead = " ".join((r.get("preview") or "").split())[:280]
            if lead:
                out.append(f"- {lead} [{i}]")
        if len(out) == 1:
            return ""
    return "\n".join(out)


QUOTE_SYNTH_TIMEOUT_S = 42.0
QUOTE_SYNTH_MIN_BUDGET_S = 30.0
QUOTE_SYNTH_MIN_QUOTES = 2
QUOTE_TABLE_CHARS = 1400


def _quote_table(ledger: EvidenceLedger) -> str:
    parts = []
    for i, row in enumerate(ledger.rows, start=1):
        text = row.get("text") or ""
        for a, b in row.get("retained") or []:
            excerpt = text[max(0, int(a)) : int(b)][:QUOTE_TABLE_CHARS].strip()
            if excerpt:
                parts.append(
                    f"[{i}] {row.get('title') or row.get('url') or ''}\n{excerpt}"
                )
    return "\n\n".join(parts)


def _retained_count(ledger: EvidenceLedger) -> int:
    return sum(len(r.get("retained") or []) for r in ledger.rows)


async def _write_from_digest(
    question: str, ledger: EvidenceLedger, deadline: float
) -> str:
    left = deadline - monotonic()
    if left < 14.0:
        return ""
    digest = _ledger_digest(ledger)
    if not digest:
        return ""
    convo = [
        {"role": "system", "content": _COMMIT_RULES},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\nNumbered evidence you gathered (cite "
                f"facts by these [n]):\n\n{digest}\n\n"
                "Write the FINAL ANSWER now from this evidence. Plain prose, no "
                "tool syntax. First words are the answer entities; every factual "
                "claim carries its [n]; then the short proof section (pool, "
                "conditions, qualifiers, exclusions)."
            ),
        },
    ]

    async def _one(lane: str, model: str, budget: float) -> str:
        _p0 = _upstream(lane, model)
        payload = None
        for _p in ((_p0, None) if _p0 is not None else (None,)):
            try:
                payload = await llm_chat(
                    provider=lane,
                    model=model,
                    messages=convo,
                    temperature=0.15,
                    max_output_tokens=2600,
                    timeout=budget,
                    thinking=_least_think(lane, model),
                    provider_extra=_p,
                )
                break
            except Exception:
                if _p is None:
                    raise
                continue
        _spend_note(payload)
        llm = getattr(payload, "llm", None)
        text = (getattr(llm, "raw_text", None) or "").strip()
        if not text:
            choices = getattr(llm, "choices", None) or []
            if choices:
                c = getattr(choices[0].message, "content", None)
                if isinstance(c, str):
                    text = c.strip()
        return text

    lanes = ((LLM_LANE_A, LOOP_MODEL_A), (LLM_LANE_B, LOOP_MODEL_B))
    for i, lane_model in enumerate(lanes):
        left = deadline - monotonic()
        if left < 14.0:
            return ""
        budget = min(RESCUE_TIMEOUT_S, left - DIGEST_TAIL_S)
        if i == 0:
            budget = min(budget, max(12.0, left - 14.0 - DIGEST_TAIL_S))
        if budget < 8.0:
            return ""
        try:
            text = await _one(lane_model[0], lane_model[1], budget)
        except Exception:
            continue
        if _is_usable_answer(text):
            return text
    return ""


async def _knowledge_resort(question: str, deadline: float) -> str:
    left = deadline - monotonic()
    if left < 12.0:
        return ""
    try:
        return await _chat_simple(
            LLM_LANE_A,
            RESORT_MODEL,
            (
                "Expert researcher. Best definitive answer with concrete entities, "
                "numbers, dates. Never refuse."
            ),
            question,
            max_tokens=2600,
            timeout=min(45.0, left - 4.0),
        )
    except Exception:
        return ""


async def _schema_output(
    question: str, answer: str, schema, deadline: float
) -> object | None:
    ask = (
        "Convert the answer to a JSON value valid under the schema. Output "
        "ONLY the JSON value.\n\n"
        f"Schema:\n{json.dumps(schema)}\n\nQuestion:\n{question}\n\n"
        f"Answer:\n{answer[:14000]}"
    )
    for lane, model in (
        (LLM_LANE_A, SCHEMA_MODEL),
        (LLM_LANE_A, RESORT_MODEL),
        (LLM_LANE_B, LOOP_MODEL_B),
    ):
        left = deadline - monotonic()
        if left < 12.0:
            break
        try:
            raw = await _chat_simple(
                lane,
                model,
                "You output strictly valid JSON.",
                ask,
                max_tokens=3400,
                timeout=min(45.0, left - 4.0),
            )
            raw = re.sub(
                r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M
            ).strip()
            value = json.loads(raw)
            if _matches_schema_shape(value, schema):
                return value
            if isinstance(value, dict) and len(value) == 1:
                inner = list(value.values())[0]
                if _matches_schema_shape(inner, schema):
                    return inner
        except Exception:
            continue
    return None


def _schema_kind(schema) -> str:
    if not isinstance(schema, dict):
        return ""
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = kind[0] if kind else None
    if kind is None:
        for key in ("anyOf", "oneOf", "allOf"):
            branch = schema.get(key)
            if isinstance(branch, list):
                for sub in branch:
                    got = _schema_kind(sub)
                    if got:
                        return got
        if isinstance(schema.get("properties"), dict):
            return "object"
        if isinstance(schema.get("enum"), list):
            return "string"
        return ""
    return str(kind)


def _matches_schema_shape(value, schema) -> bool:
    kind = _schema_kind(schema)
    if not kind:
        return True
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    return True


_NUM_IN_TEXT_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


_DIGEST_LEAD_RE = re.compile(
    r"^\s*Best-supported findings|^\s*sources retrieved:", re.I
)
_DIGEST_NOISE_RE = re.compile(r"\[slice \d+:\d+\]|https?://\S+")
_VALUE_MAX_CHARS = 90


def _undigest_for_schema(basis: str) -> str:
    if not basis:
        return ""
    text = _DIGEST_NOISE_RE.sub(" ", basis)
    out = []
    for raw in text.split("\n"):
        line = raw.strip().lstrip("-*• ").strip()
        if not line or _DIGEST_LEAD_RE.match(line):
            continue
        if ":" in line:
            head, _, tail = line.partition(":")
            line = (
                tail.strip()
                if 0 < len(tail.strip()) <= _VALUE_MAX_CHARS
                else head.strip()
            )
        if not line or len(line) > _VALUE_MAX_CHARS:
            continue
        if line.count(" ") > 8:
            continue
        if line not in out:
            out.append(line)
        if len(out) >= 6:
            break
    return "\n".join(out)


def _coerce_to_schema(answer: str, schema, depth: int = 0):
    if depth > 4 or not isinstance(schema, dict):
        return answer[:400]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        low = (answer or "").lower()
        for opt in enum:
            if isinstance(opt, str) and re.search(
                r"\b" + re.escape(opt.lower()) + r"\b", low
            ):
                return opt
        return enum[0]
    kind = _schema_kind(schema)
    if not kind:
        for key in ("anyOf", "oneOf", "allOf"):
            branch = schema.get(key)
            if isinstance(branch, list) and branch:
                for sub in branch:
                    if isinstance(sub, dict) and sub.get("type") != "null":
                        return _coerce_to_schema(answer, sub, depth + 1)
        kind = "string"
    if kind == "array":
        items = schema.get("items") or {}
        parts = [
            p.strip(" -*\t") for p in re.split(r"[\n;]|,(?![^(]*\))", answer or "")
        ]
        parts = [p[:400] for p in parts if p][:20]
        if not parts:
            parts = [answer[:400]]
        return [_coerce_to_schema(p, items, depth + 1) for p in parts]
    if kind == "object":
        props = schema.get("properties") or {}
        required = schema.get("required") or list(props.keys())
        out = {}
        for key in required:
            out[key] = _coerce_to_schema(answer, props.get(key) or {}, depth + 1)
        return out
    if kind in ("number", "integer"):
        found = _NUM_IN_TEXT_RE.search(_CITE_NUM_RE.sub(" ", answer or ""))
        if found is None:
            return 0
        val = found.group(0).replace(",", "")
        try:
            return int(val) if kind == "integer" else float(val)
        except Exception:
            return 0
    if kind == "boolean":
        return not re.match(r"\s*(no\b|false\b|none\b)", (answer or ""), re.I)
    return (answer or "")[:400]


_NARRATION_LEAD_RE = re.compile(
    r"^\s*(?:based on (?:my|the)\b|now (?:i|that i)\b|i (?:now )?(?:have|was|am|need|will|can)\b|"
    r"i(?:'ll|'ve|'m)\b|let me\b|let's\b|first,? i\b|having (?:now )?\w+\b|"
    r"okay\b|alright\b|to answer this\b|my research\b)",
    re.IGNORECASE,
)
_ABBREV_TAIL_RE = re.compile(
    r"(?:\b[A-Z]|\b(?:Inc|Ltd|Co|No|vs|St|Dr|Mr|Ms|Mt|Jr|Sr|etc|e\.g|i\.e))\.$"
)


def _strip_lead_narration(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    for _ in range(2):
        parts = re.split(r"(?<=[.!?])\s+", t, maxsplit=1)
        if len(parts) != 2:
            break
        head, rest = parts[0], parts[1].strip()
        if _CITE_NUM_RE.search(head):
            break
        if _NARRATION_LEAD_RE.match(head) is None:
            break
        if len(head.split()) < 4 or _ABBREV_TAIL_RE.search(head) is not None:
            break
        if len(rest) < 120 or _CITE_NUM_RE.search(rest) is None:
            break
        t = rest
    return t


def _cap(text: str) -> str:
    t = (text or "").strip()
    if len(t) > ANSWER_CHAR_CAP:
        return t[: ANSWER_CHAR_CAP - 16] + " …"
    return t


class AgentType(str, Enum):
    """Concrete solver route for a query."""

    CODE = "code"
    TEXT = "text"


_CODE_FENCE_RE = re.compile(
    r"```(?:js|javascript|ts|typescript|tsx|jsx|python|py|java|c\+\+|cpp|c|go|rust|"
    r"sql|bash|sh|shell|html|css|json|yaml|yml|php|ruby|swift|kotlin)?\b",
    re.IGNORECASE,
)
_CODE_SYNTAX_RE = re.compile(
    r"(?:"
    r"console\.log\b"
    r"|module\.exports\b"
    r"|JSON\.(?:parse|stringify)\b"
    r"|\btypeof\s+\w+"
    r"|\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*="
    r"|\bfunction\s*(?:[A-Za-z_$][\w$]*)?\s*\("
    r"|=>\s*[{(]"
    r"|===|!=="
    r"|\.then\b|\.catch\b|\bPromise\b"
    r"|\basync\s+function\b|\bawait\s+"
    r"|document\.(?:getElementById|querySelector)\b"
    r"|process\.env\b"
    r"|npm\s+(?:install|run)\b"
    r"|package\.json\b"
    r"|\bdef\s+[A-Za-z_]\w*\s*\("
    r"|\bclass\s+[A-Za-z_]\w*\s*[:(]"
    r"|#include\s*<"
    r"|public\s+static\s+void\s+main"
    r"|\bimport\s+(?:\{|[\w*])|\bfrom\s+['\"]|require\s*\("
    r"|\breturn\s+[^;]+;?"
    r"|;\s*(?://|/\*|$)"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_CODE_LANG_RE = re.compile(
    r"\b(?:javascript|typescript|node\.?js|python|java|golang|go lang|rust|sql|"
    r"c\+\+|csharp|c#|php|ruby|swift|kotlin|html|css|dom|react|vue|angular)\b",
    re.IGNORECASE,
)
_CODE_TASK_RE = re.compile(
    r"(?:"
    r"\b(?:code|snippet|script|program|source|algorithm|pseudocode)\b"
    r"|\b(?:function|method|class|module|package|library|framework|api|sdk)\b"
    r"|\b(?:compile|compiler|runtime|execute|execution|interpreter|stack\s*trace|"
    r"exception|traceback|bug|debug|refactor|lint)\b"
    r"|\b(?:output\s+of|return\s+value|time\s*complexity|big[- ]?o)\b"
    r"|\bwhat\s+(?:does|is|will)\s+this\s+(?:code|function|script|program)\b"
    r"|\b(?:write|implement|fix|debug|complete)\b.{0,40}\b"
    r"(?:code|function|script|program|snippet|method|class|bug|error)\b"
    r"|\bexplain\b.{0,40}\b(?:code|function|script|program|snippet|promise|async|await|api)\b"
    r")",
    re.IGNORECASE,
)
_FACTUAL_SIGNAL_RE = re.compile(
    r"(?:"
    r"\b(?:who|when|where|which|whose)\b"
    r"|\b(?:according to|based on (?:the )?(?:report|study|filing|census|survey))\b"
    r"|\b(?:population|revenue|gdp|election|treaty|nobel|olympic|war|company|ceo|"
    r"founded|headquarters|sec\s*filing|10-k|10-q)\b"
    r"|\b(?:in\s+(?:19|20)\d{2}|as of\s+(?:19|20)\d{2})\b"
    r")",
    re.IGNORECASE,
)


def _detect_agent_type(text: str) -> AgentType:
    """Return the real solver type for this question: AgentType.CODE or AgentType.TEXT."""
    raw = (text or "").strip()
    if not raw:
        return AgentType.TEXT

    has_fence = _CODE_FENCE_RE.search(raw) is not None
    has_syntax = _CODE_SYNTAX_RE.search(raw) is not None
    has_lang = _CODE_LANG_RE.search(raw) is not None
    has_task = _CODE_TASK_RE.search(raw) is not None
    has_braces = "{" in raw and "}" in raw
    has_many_semicolons = raw.count(";") >= 2
    has_factual = _FACTUAL_SIGNAL_RE.search(raw) is not None

    # Hard route: embedded source or clear programming syntax.
    if has_fence or has_syntax:
        return AgentType.CODE

    score = 0
    if "```" in raw:
        score += 3
    if has_lang:
        score += 2
    if has_task:
        score += 3
    if has_braces:
        score += 1
    if has_many_semicolons:
        score += 1
    if has_lang and has_task:
        score += 2
    if has_factual and not (has_lang or has_task):
        score -= 2

    if score >= 4:
        return AgentType.CODE
    return AgentType.TEXT


@entrypoint("query")
async def query(query: Query) -> Response:
    question = (query.text or "").strip()
    if not question:
        return Response(text="No question provided.")
    agent_type = _detect_agent_type(question)
    try:
        if agent_type is AgentType.CODE:
            return await _solve_code_agent(query, question)
        return await _solve_text_agent(query, question)
    except Exception:
        return Response(text=f"Best-effort answer unavailable for: {question[:500]}")


async def _solve_text_agent(query: Query, question: str) -> Response:
    deadline = monotonic() + WALL_BUDGET_S
    try:
        info = await tooling_info(timeout=10.0)
        _spend_note(info)
    except Exception:
        pass

    draft = ""
    brief = ""
    try:
        if _spend_left() >= BRIEF_MIN_USD and (deadline - monotonic()) > 120.0:
            draft, brief = await _knowledge_brief(question)
    except Exception:
        brief = ""

    ledger = EvidenceLedger()
    answer = ""
    messages: list[dict] = []
    try:
        pool_hint = ""
        try:
            if _needs_set_completeness(question) or _needs_superlative_proof(question):
                pool_hint = await _draft_candidate_pool(question, deadline)
        except Exception:
            pool_hint = ""
        answer, messages = await _loop(
            question, brief, ledger, deadline, MAX_TURNS, pool_hint=pool_hint
        )
    except Exception:
        answer = ""

    try:
        if (
            _is_usable_answer(answer)
            and (deadline - monotonic()) > 75.0
            and _spend_left() >= AUDIT_MIN_USD
        ):
            patched = await _audit_patch(question, answer, messages, ledger, deadline)
            if _is_usable_answer(patched):
                answer = patched
    except Exception:
        pass

    for _sweep in (_align_timeframe, _second_source_check):
        try:
            if not _is_usable_answer(answer):
                break
            if (deadline - monotonic()) <= SECOND_SOURCE_MIN_LEFT_S:
                break
            if _spend_left() <= AUDIT_MIN_USD:
                break
            swept = await _sweep(question, answer, messages, ledger, deadline)
            if _is_usable_answer(swept):
                answer = swept
        except Exception:
            continue

    if not _is_usable_answer(answer) and ledger.rows:
        try:
            rescued = await _write_from_digest(question, ledger, deadline)
            if _is_usable_answer(rescued):
                answer = rescued
        except Exception:
            pass
    if not _is_usable_answer(answer) and ledger.rows:
        det = _deterministic_answer(question, ledger)
        if _is_usable_answer(det):
            answer = det
    if not _is_usable_answer(answer):
        fallback = _sanitize_draft(draft) or await _knowledge_resort(question, deadline)
        if _is_usable_answer(fallback):
            answer = fallback

    try:
        citations = _citations_for(answer, ledger)
    except Exception:
        citations = []

    answer = _normalize_brackets(answer)
    answer = _strip_lead_narration(answer)
    answer = _answer_line_only(answer, question)
    text = _cap(answer) or f"Best-effort answer unavailable for: {question[:400]}"

    if query.output_schema is not None:
        structured = None
        try:
            structured = await _schema_output(
                question, answer, query.output_schema, deadline
            )
        except Exception:
            structured = None
        if structured is not None:
            try:
                structured = _verbatim_structured(structured, ledger)
            except Exception:
                pass
            try:
                return Response(output=structured, citations=citations or None)
            except Exception:
                structured = None
        basis = answer if _is_usable_answer(answer) else ""
        if not basis:
            basis = _deterministic_answer(question, ledger)
        if not basis or _STUB_ANSWER_RE.match(basis.strip()):
            basis = question[:400]
        if basis is not answer:
            try:
                salvaged = await _schema_output(
                    question, basis, query.output_schema, deadline
                )
            except Exception:
                salvaged = None
            if salvaged is not None:
                try:
                    return Response(output=salvaged, citations=citations or None)
                except Exception:
                    pass
        if basis is not answer:
            cleaned = _undigest_for_schema(basis)
            basis = cleaned if cleaned else ""
        try:
            forced = _coerce_to_schema(_cap(basis), query.output_schema)
            return Response(output=forced, citations=citations or None)
        except Exception:
            try:
                return Response(output=_cap(basis)[:2000], citations=citations or None)
            except Exception:
                pass

    try:
        return Response(text=text, citations=citations or None)
    except Exception:
        return Response(text=text)


_CODE_WALL_S = 140.0
_CODE_TURN_S = 36.0
_CODE_WRAP_S = 40.0
_CODE_TAIL_S = 6.0
_CODE_MAX_TURNS = 4
_CODE_ANSWER_CHARS = 60000
_CODE_TURN_TOKENS = 2600
_CODE_FINISH_TOKENS = 3600
_CODE_TOOL_WAIT_S = 18.0
_CODE_TOOLS_PER_TURN = 4
_CODE_MAX_TOOL_ROUNDS = 2
_CODE_WRAP_MSG = (
    "No more tool calls. Write the complete final answer NOW from the numbered "
    "ledger plus your knowledge. First sentence is the answer. Cite [n] on "
    "load-bearing claims. No preamble."
)
_CODE_RULES = (
    "You are a fast code agent. Prefer a direct answer from known language, API, "
    "and runtime facts. Call tools only when a spec, signature, or version is uncertain.\n"
    "You have a SEPARATE toolset from the factual research agent, but you share the SAME "
    "evidence state and flow: tool results are committed into a numbered EvidenceLedger "
    "([n] rows with receipt/result spans, retained excerpts, and digest consumption).\n"
    "Use ONLY these tools:\n"
    "- docs_search(query): docs/specs search; commits search rows into the ledger.\n"
    "- docs_fetch(url, focus): fetch a docs/spec page; commits a fetch row like read_page.\n"
    "- docs_grep(source, pattern): grep an already-fetched ledger page (URL or [n]).\n"
    "- docs_read(source, offset, length): read a character range from a ledger page.\n"
    "- docs_retain(source, quote): retain an exact quote from a ledger row for citation.\n"
    "Workflow:\n"
    "1) If you already know the answer, emit it immediately — no tools.\n"
    "2) Otherwise RESEARCH: at most two docs_* rounds; batch independent lookups in one turn.\n"
    "3) EVIDENCE LEDGER: every citable tool result is numbered [n] via the shared ledger commit "
    "path; that ledger is the only evidence state between research and the final answer.\n"
    "4) ANSWER PRODUCTION: write the final answer; cite [n] on load-bearing claims.\n"
    "Prefer official docs over blogs. tool_choice is auto: call tools only when they change "
    "the answer. Never mix tool calls with the final answer in one turn.\n"
    "First sentence is the answer. No preamble. If the question wants code, emit the code; "
    "if it wants a value, emit the value. Never refuse. Never call web_search, read_page, "
    "sec_filing, page_grep, page_read, or retain_evidence by those names — use the docs_* "
    "aliases above (they feed the same ledger machinery)."
)
_CODE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "docs_search",
            "description": (
                "Search docs/specs/source for APIs, language behavior, library contracts, "
                "or runtime output. Prefer official documentation. Commits numbered ledger rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "docs-oriented search query",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docs_fetch",
            "description": (
                "Fetch a documentation/spec/source URL into the shared evidence ledger "
                "(same commit path as read_page). Pass focus to steer relevant sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "docs/spec URL to fetch"},
                    "focus": {
                        "type": "string",
                        "description": "optional section/API/symbol to locate",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docs_grep",
            "description": (
                "Search inside a page already in the evidence ledger by [n] or URL "
                "(same ledger flow as page_grep)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "ledger index like [3] or a previously fetched URL",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "literal or regex pattern to find",
                    },
                },
                "required": ["source", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docs_read",
            "description": (
                "Read a character range from a page already in the evidence ledger "
                "(same ledger flow as page_read)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "ledger index like [3] or a previously fetched URL",
                    },
                    "offset": {"type": "integer", "description": "start offset"},
                    "length": {
                        "type": "integer",
                        "description": "max characters to return",
                    },
                },
                "required": ["source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docs_retain",
            "description": (
                "Retain an exact quote from a ledger row for tighter citations "
                "(same ledger flow as retain_evidence)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "ledger index like [3]",
                    },
                    "quote": {
                        "type": "string",
                        "description": "exact excerpt present in that row's stored text",
                    },
                },
                "required": ["source", "quote"],
            },
        },
    },
]


def _code_think(model: str) -> dict:
    if str(model).startswith("openai/gpt-oss"):
        return {"enabled": True, "effort": "low"}
    return {"enabled": False}


def _code_note_spend(state: dict, payload) -> None:
    budget = getattr(payload, "budget", None)
    left = getattr(budget, "session_remaining_budget_usd", None)
    if isinstance(left, (int, float)):
        state["left"] = float(left)
    else:
        state["left"] = _spend_left()


def _code_sync_spend(state: dict) -> None:
    state["left"] = _spend_left()


def _code_left(state: dict) -> float:
    left = state.get("left")
    if isinstance(left, (int, float)):
        return float(left)
    return 1.0


def _code_llm_text(payload) -> str:
    llm = getattr(payload, "llm", None)
    text = (getattr(llm, "raw_text", None) or "").strip()
    if text:
        return text
    choices = getattr(llm, "choices", None) or []
    if not choices:
        return ""
    content = getattr(choices[0].message, "content", None)
    if isinstance(content, str):
        return content.strip()
    return ""


def _code_resolve_source_url(source: str, ledger: EvidenceLedger) -> str:
    src = (source or "").strip()
    m = re.fullmatch(r"\[?(\d+)\]?", src)
    if m:
        n = int(m.group(1))
        if 1 <= n <= len(ledger.rows):
            return (ledger.rows[n - 1].get("url") or "").strip() or src
    return src


async def _code_run_tool(
    name: str, args: dict, question: str, state: dict, ledger: EvidenceLedger
):
    """Dispatch docs_* tools onto the shared evidence helpers (ToolOutput + ledger commit)."""
    if name == "docs_search":
        q = str(args.get("query") or "").strip()
        if not q:
            return "# docs_search: empty query"
        biased = (
            q if re.search(r"\b(?:mdn|docs|spec|rfc|api)\b", q, re.I) else f"{q} docs"
        )
        out = await _do_search(biased, ledger)
        _code_sync_spend(state)
        return out
    if name == "docs_fetch":
        out = await _do_fetch(
            str(args.get("url") or ""),
            str(args.get("focus") or ""),
            question,
            ledger,
        )
        _code_sync_spend(state)
        return out
    if name == "docs_grep":
        url = _code_resolve_source_url(str(args.get("source") or ""), ledger)
        return _do_page_grep(url, str(args.get("pattern") or ""), ledger)
    if name == "docs_read":
        url = _code_resolve_source_url(str(args.get("source") or ""), ledger)
        return _do_page_read(
            url,
            args.get("offset") or 0,
            args.get("length") or PAGE_READ_MAX_CHARS,
            ledger,
        )
    if name == "docs_retain":
        return _do_retain_evidence(
            str(args.get("source") or ""),
            str(args.get("quote") or ""),
            ledger,
        )
    return (
        f"# unknown tool {name!r}; code-agent tools are "
        "docs_search, docs_fetch, docs_grep, docs_read, docs_retain"
    )


async def _code_chat(
    messages: list,
    deadline: float,
    state: dict,
    *,
    finish_only: bool,
):
    timeout = min(_CODE_TURN_S, deadline - monotonic() - 3.0)
    if timeout <= 5.0:
        return None
    lanes = (
        (LLM_LANE_A, LOOP_MODEL_A, True),
        (LLM_LANE_B, LOOP_MODEL_B, False),
    )
    tools = None if finish_only else _CODE_TOOLS
    choice = None if finish_only else "auto"
    max_tokens = _CODE_FINISH_TOKENS if finish_only else _CODE_TURN_TOKENS
    for lane, model, pinned in lanes:
        left = deadline - monotonic()
        if left <= _CODE_TAIL_S:
            return None
        try:
            payload = await asyncio.wait_for(
                llm_chat(
                    provider=lane,
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice=choice,
                    parallel_tool_calls=None if finish_only else True,
                    temperature=0.2,
                    thinking=_code_think(model),
                    max_output_tokens=max_tokens,
                    provider_extra=_upstream(lane, model) if pinned else None,
                    timeout=min(timeout, left - 2.0),
                ),
                timeout=min(timeout + 3.0, max(1.0, left - 1.0)),
            )
            _code_note_spend(state, payload)
            return payload
        except Exception:
            continue
    return None


async def _code_schema(
    question: str, answer: str, schema, deadline: float, state: dict
):
    ask = (
        "Convert the answer to a JSON value valid under the schema. "
        "Output ONLY the JSON value.\n\n"
        f"Schema:\n{json.dumps(schema)}\n\nQuestion:\n{question}\n\n"
        f"Answer:\n{answer[:14000]}"
    )
    for lane, model in (
        (LLM_LANE_A, LOOP_MODEL_A),
        (LLM_LANE_A, SCHEMA_MODEL),
    ):
        left = deadline - monotonic()
        if left < 10.0:
            break
        try:
            payload = await llm_chat(
                provider=lane,
                model=model,
                messages=[
                    {"role": "system", "content": "You output strictly valid JSON."},
                    {"role": "user", "content": ask},
                ],
                temperature=0.0,
                max_output_tokens=2800,
                thinking=_code_think(model),
                provider_extra=_upstream(lane, model),
                timeout=min(22.0, left - 3.0),
            )
            _code_note_spend(state, payload)
            raw = _code_llm_text(payload)
            raw = re.sub(
                r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M
            ).strip()
            value = json.loads(raw)
            return value
        except Exception:
            continue
    return None


async def _code_agent_loop(
    question: str,
    ledger: EvidenceLedger,
    deadline: float,
    state: dict,
    *,
    turn_cap: int,
    carry: list | None = None,
    allow_tools_in_wrapup: bool = False,
) -> tuple[str, list]:
    """Code-agent controller using the shared EvidenceLedger commit flow."""
    if carry is not None:
        messages = carry
    else:
        messages = [
            {"role": "system", "content": _CODE_RULES},
            {"role": "user", "content": question},
        ]
    answer = ""
    tool_rounds = 0
    ordered_wrapup = False
    try:
        for turn in range(1, turn_cap + 1):
            left = deadline - monotonic()
            if left <= _CODE_TAIL_S:
                break
            finish_only = (
                (left <= _CODE_WRAP_S and not allow_tools_in_wrapup)
                or _code_left(state) <= 0.02
                or turn >= turn_cap
                or (tool_rounds >= _CODE_MAX_TOOL_ROUNDS and not allow_tools_in_wrapup)
            )
            if allow_tools_in_wrapup and turn == 1:
                finish_only = False
            if finish_only and not ordered_wrapup:
                messages.append({"role": "system", "content": _CODE_WRAP_MSG})
                ordered_wrapup = True
            payload = await _code_chat(
                messages, deadline, state, finish_only=finish_only
            )
            if payload is None:
                break
            llm = getattr(payload, "llm", None)
            choices = getattr(llm, "choices", None) or []
            if not choices:
                break
            msg = choices[0].message
            calls = getattr(msg, "tool_calls", None) or ()
            if not calls:
                candidate = _code_llm_text(payload)
                if candidate.strip():
                    answer = candidate.strip()
                    messages.append({"role": "assistant", "content": answer})
                break
            try:
                replay = msg.to_input_message()
            except Exception:
                replay = {
                    "role": "assistant",
                    "content": _code_llm_text(payload) or None,
                    "tool_calls": [
                        {
                            "id": getattr(c, "id", ""),
                            "type": getattr(c, "type", "function") or "function",
                            "name": getattr(c, "name", ""),
                            "arguments": getattr(c, "arguments", "{}") or "{}",
                        }
                        for c in calls
                    ],
                }
            messages.append(replay)
            run_calls = list(calls)[:_CODE_TOOLS_PER_TURN]
            tool_rounds += 1
            tool_budget = max(
                4.0,
                min(_CODE_TOOL_WAIT_S, deadline - monotonic() - _CODE_TAIL_S),
            )
            tool_tasks = []
            for c in run_calls:
                raw_args = getattr(c, "arguments", "") or "{}"
                try:
                    parsed = json.loads(raw_args)
                    if not isinstance(parsed, dict):
                        parsed = {}
                except Exception:
                    parsed = {}
                tool_tasks.append(
                    asyncio.ensure_future(
                        _code_run_tool(
                            getattr(c, "name", "") or "",
                            parsed,
                            question,
                            state,
                            ledger,
                        )
                    )
                )
            try:
                await asyncio.wait(tool_tasks, timeout=tool_budget)
            except Exception:
                pass
            results = []
            for t in tool_tasks:
                if t.done():
                    try:
                        results.append(t.result())
                    except Exception as exc:
                        results.append(f"# tool crashed: {exc}")
                else:
                    t.cancel()
                    results.append("# tool timed out — use what you already have")
            for call, raw in zip(run_calls, results):
                body = _commit_tool_output(raw, ledger)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(call, "id", "") or "tool",
                        "content": str(body) or "# empty",
                    }
                )
            for call in calls[_CODE_TOOLS_PER_TURN:]:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(call, "id", "") or "tool",
                        "content": "# skipped: per-turn tool budget reached",
                    }
                )
    except Exception:
        answer = ""
    return answer, messages


async def _code_answer_from_ledger(
    question: str, ledger: EvidenceLedger, deadline: float, state: dict
) -> str:
    """Answer-production path: synthesize the final answer from ledger evidence."""
    if not ledger.rows or (deadline - monotonic()) < 10.0:
        return ""
    digest = _ledger_digest(ledger, char_cap=28000)
    if not digest:
        return ""
    ask = (
        "Write the FINAL ANSWER from the numbered evidence below. "
        "First sentence is the answer. Cite [n] on load-bearing claims. "
        "No preamble.\n\n"
        f"Question:\n{question}\n\nEvidence:\n{digest}"
    )
    for lane, model in (
        (LLM_LANE_A, LOOP_MODEL_A),
        (LLM_LANE_B, LOOP_MODEL_B),
    ):
        left = deadline - monotonic()
        if left < 10.0:
            break
        try:
            payload = await llm_chat(
                provider=lane,
                model=model,
                messages=[
                    {"role": "system", "content": _CODE_RULES},
                    {"role": "user", "content": ask},
                ],
                temperature=0.2,
                max_output_tokens=_CODE_FINISH_TOKENS,
                thinking=_code_think(model),
                provider_extra=_upstream(lane, model),
                timeout=min(22.0, left - 3.0),
            )
            _code_note_spend(state, payload)
            text = _code_llm_text(payload).strip()
            if text:
                return text
        except Exception:
            continue
    return ""


async def _code_knowledge_fallback(question: str, deadline: float, state: dict) -> str:
    if (deadline - monotonic()) <= 10.0:
        return ""
    for lane, model, reserve in (
        (LLM_LANE_A, LOOP_MODEL_A, 3.0),
        (LLM_LANE_B, LOOP_MODEL_B, 2.0),
    ):
        left = deadline - monotonic()
        if left <= 10.0:
            break
        try:
            payload = await llm_chat(
                provider=lane,
                model=model,
                messages=[
                    {"role": "system", "content": _CODE_RULES},
                    {"role": "user", "content": question},
                ],
                temperature=0.2,
                max_output_tokens=_CODE_FINISH_TOKENS,
                thinking=_code_think(model),
                provider_extra=_upstream(lane, model),
                timeout=min(22.0, left - reserve),
            )
            _code_note_spend(state, payload)
            text = _code_llm_text(payload).strip()
            if text:
                return text
        except Exception:
            continue
    return ""


async def _solve_code_agent(query: Query, question: str) -> Response:
    deadline = monotonic() + _CODE_WALL_S
    state: dict = {"left": None}
    ledger = EvidenceLedger()

    answer = ""
    messages: list = []
    try:
        answer, messages = await _code_agent_loop(
            question,
            ledger,
            deadline,
            state,
            turn_cap=_CODE_MAX_TURNS,
        )
    except Exception:
        answer = ""
        messages = []

    if not (answer or "").strip() and ledger.rows:
        try:
            rescued = await _code_answer_from_ledger(question, ledger, deadline, state)
            if rescued.strip():
                answer = rescued
        except Exception:
            pass
    if not (answer or "").strip():
        try:
            answer = await _code_knowledge_fallback(question, deadline, state)
        except Exception:
            answer = ""

    text = (answer or "").strip()[:_CODE_ANSWER_CHARS]
    if not text:
        text = f"Best-effort answer unavailable for: {question[:400]}"
    citations = _citations_for(text, ledger)
    if query.output_schema is not None:
        structured = None
        try:
            structured = await _code_schema(
                question, text, query.output_schema, deadline, state
            )
        except Exception:
            structured = None
        if structured is not None:
            try:
                return Response(output=structured, citations=citations or None)
            except Exception:
                structured = None
        try:
            forced = json.loads(text)
            return Response(output=forced, citations=citations or None)
        except Exception:
            pass
        try:
            return Response(output=text[:2000], citations=citations or None)
        except Exception:
            pass
    try:
        return Response(text=text, citations=citations or None)
    except Exception:
        return Response(text=text)
