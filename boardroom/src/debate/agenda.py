"""The VC's agenda: everything a partner needs to know before deciding, each topic opened and then probed.

There is no question budget: the VC keeps asking until every topic is covered and it says it is ready to decide.
Every main question gets at least one follow-up ("how did you get there?"), so a number is never accepted on first
answer, and the VC may keep following up while gaps remain. Market size comes first; the TAM answer may carry up to
three independent sizing methods, each with its maths. The VC never addresses an executive: the moderator decides
who answers. `owner` only records who usually knows the answer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    owner: str        # executive who usually knows the answer (documentation and dry runs; the moderator decides)
    ask: str          # what the VC's main question should cover
    answer: str       # extra instruction for the executive answering the main question
    follow_up: str    # what the VC's follow-up must probe
    corpus_topics: tuple[str, ...] = ()  # corpus topics (corpus/<company>/manifest.yaml) whose evidence this topic needs
    evidence_query: str = ""             # what that evidence is about, used to fill the topic's reserved slots


AGENDA: dict[str, Topic] = {t.key: t for t in (
    Topic("market_size", "Market size (TAM)", "cfo",
          ask="Ask how big the opportunity is, in dollars, and for the maths behind it. Invite more than one "
              "sizing method.",
          answer="Give up to THREE independent sizing approaches in `estimates`, for example top-down from "
                 "customer budgets, a share of an existing market, and bottom-up from units x frequency x price. "
                 "Each estimate needs a short `method` name, a step-by-step `calculation` that ends in its result, "
                 "and its `assumptions`. In `point`, give the range and say which estimate you would put in a "
                 "seed deck and why.",
          follow_up="Ask how they reached those numbers: where each input comes from, which single assumption "
                    "moves the result most, why the estimates differ (they can be 10x apart), and which one "
                    "belongs in a seed deck.",
          corpus_topics=('market', 'financials'),
          evidence_query='market size municipal budgets public health share cities population overdose deaths opioid use disorder water treatment market billion forecast sizing inputs'),
    Topic("customers", "Customers and who pays", "ceo",
          ask="Ask who the first paying customer is, who signs the cheque, and why they would pay now.",
          answer="Name the buyer, the budget it comes from and the trigger to buy; cite evidence for each.",
          follow_up="Ask what evidence shows they will actually pay (a pilot, a budget line, a letter of intent) "
                    "and how long the sales cycle is.",
          corpus_topics=('customers_go_to_market', 'market', 'company_history'),
          evidence_query='buyer customer who pays city mayor public health department lead customer budget sales'),
    Topic("unit_economics", "Pricing and unit economics", "cfo",
          ask="Ask for the price per unit (sample, site or city), the cost to serve it and the gross margin.",
          answer="Show price, cost and margin per unit as a step-by-step `calculation` and list the "
                 "`assumptions` behind each input.",
          follow_up="Ask which price or cost assumption breaks the model, and what the break-even volume is.",
          corpus_topics=('financials', 'market'),
          evidence_query='price cost budget spend per sample per city revenue funding grant recovery services'),
    Topic("technology_moat", "Technology and defensibility", "cto",
          ask="Ask what stops a competitor, a university lab or a city's own utility from copying this.",
          answer="Separate what is proprietary today from what is planned; be candid about gaps.",
          follow_up="Ask for proof of the advantage today and how long and how much it would take to replicate.",
          corpus_topics=('technology_ip', 'competition_and_precedents'),
          evidence_query='intellectual property patent hardware robot sampling lab analytics proprietary competitors copy'),
    Topic("go_to_market", "Go-to-market and scaling", "ceo",
          ask="Ask how they win the first cities and how they scale beyond them through procurement.",
          answer="Give the sequence of first customers and the path through procurement, with timelines.",
          follow_up="Ask for the timeline and the people or money each step needs, and what happens if the first "
                    "city says no.",
          corpus_topics=('customers_go_to_market', 'risks_and_objections', 'company_history'),
          evidence_query='cities procurement accelerator pilot mayor government access sales cycle first customers'),
    Topic("icp_beachhead", "Ideal customer profile and beachhead", "ceo",
          ask="Ask who exactly the ideal customer is (type of buyer, size, the pain they feel) and which narrow "
              "beachhead the company wins first, and why that one.",
          answer="Describe the ideal customer precisely and name the beachhead segment and why it comes first; cite "
                 "evidence for each.",
          follow_up="Ask what evidence shows this beachhead buys first, and how many such customers exist.",
          corpus_topics=('customers_go_to_market', 'market'),
          evidence_query='ideal customer profile beachhead segment cities public health departments first customers pain'),
    Topic("competition", "Competition and alternatives", "ceo",
          ask="Ask who else solves this problem today (companies, labs, in-house programmes) and why a buyer would "
              "choose this company instead.",
          answer="Name the competitors and alternatives from the evidence and the concrete reason a buyer picks you.",
          follow_up="Ask where they lose deals today and to whom.",
          corpus_topics=('competition_and_precedents', 'customers_go_to_market'),
          evidence_query='competitors alternatives Verily WastewaterSCAN university labs in-house public health substitute'),
    Topic("founders_team", "Founders' background and journey", "ceo",
          ask="Ask about the founders' backgrounds, how they met, how the company started and the key moments of "
              "the journey so far.",
          answer="Tell the founding story and each founder's background, with dates from the evidence.",
          follow_up="Ask what gaps the team has today and who they need to hire next.",
          corpus_topics=('founders_team', 'origin_research', 'company_history'),
          evidence_query='founders background MIT Underworlds Matus Ghaeli journey started team hires'),
    Topic("vision", "Vision and long-term ambition", "ceo",
          ask="Ask where the company will be in ten years if it works and what the end-state product is.",
          answer="State the long-term vision concretely and the steps from today's product to it.",
          follow_up="Ask which step on that path is the riskiest and what proves it is possible.",
          corpus_topics=('decision_context', 'company_history', 'market'),
          evidence_query='vision long-term platform health data every city expansion future'),
    Topic("fundraise", "The raise: amount, terms and investors", "cfo",
          ask="Ask how much they are raising now, on what terms or valuation, from whom, and how long it lasts.",
          answer="Give the amount, the instrument and terms if known, current investors and the runway it buys; "
                 "say plainly what the evidence does not show.",
          follow_up="Ask how they arrived at that amount and what happens if they raise less.",
          corpus_topics=('financials', 'company_history'),
          evidence_query='raising seed series funding amount investors valuation runway grant'),
    Topic("use_of_funds", "Use of funds and milestones", "cfo",
          ask="Ask what exactly the raised capital will be spent on and which milestones it must reach before the "
              "next round.",
          answer="Break the raise into uses with amounts and the milestones each buys, with the maths in "
                 "`calculation`.",
          follow_up="Ask which milestone the next investor will judge them on and how likely it is to be hit.",
          corpus_topics=('financials', 'customers_go_to_market'),
          evidence_query='use of funds hiring lab sampling sales milestones spend budget next round'),
    Topic("risks", "Key risks", "ceo",
          ask="Ask for the single biggest risk to the business (access, privacy, regulation, team or funding).",
          answer="Name the biggest risk plainly and the concrete mitigation.",
          follow_up="Ask what the mitigation costs, who owns it, and what early signal would show it is failing.",
          corpus_topics=('risks_and_objections', 'competition_and_precedents'),
          evidence_query='risk objection privacy ethics procurement access visa advisors skeptical who will pay'),
)}

OPENING_TOPIC = "market_size"  # the first question after the pitch

FOLLOW_UP_ANSWER = (
    "This is a FOLLOW-UP: explain HOW you reached your previous answer. Say where each input came from (cite "
    "the evidence), which assumption matters most and why. If numbers are involved, show the maths again in "
    "`calculation` (or `estimates` if you compare methods) and list the `assumptions`. Correct anything the "
    "fact-check or the Devil's Advocate showed was wrong instead of defending it."
)
