from __future__ import annotations

import re

from ..models import AdvisorMode, AuditoriumMode, CitizenSnapshot, ConversationTurn, RealtimeRole, SetupSessionState, SimulationState
from .council import COUNCIL_ADVISORS, council_roster_block


class RealtimePromptFactory:
    def _council_roster(self, state: SimulationState):
        roster = getattr(state.config, "council_roster", None) or []
        return roster or list(COUNCIL_ADVISORS)

    def _council_advisor_for(self, state: SimulationState, advisor_name: str):
        advisor_name = advisor_name.strip()
        for advisor in self._council_roster(state):
            if advisor.name == advisor_name or advisor.key == advisor_name:
                return advisor
        return None

    def _stage_opening(self, stage, max_chars: int = 160) -> str:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", str(stage.world_brief or "").strip()) if part.strip()]
        source = paragraphs[0] if paragraphs else str(stage.world_brief or "")
        sentence = re.split(r"(?<=[.!?])\s+", source.strip(), maxsplit=1)[0].strip()
        return self._clip(sentence or source, max_chars)

    def _world_sentences(self, stage) -> list[str]:
        return [part.strip() for part in re.split(r"(?<=[.!?])\s+", str(stage.world_brief or "").strip()) if part.strip()]

    def _stage_gain(self, stage, max_chars: int = 150) -> str:
        positive_keywords = (
            "cheaper",
            "easier",
            "better",
            "stronger",
            "more capable",
            "more reliable",
            "more abundant",
            "broader",
            "widens",
            "grew",
            "grew richer",
            "more secure",
            "public likes",
            "people defend",
            "ordinary life improved",
        )
        source = next(
            (
                sentence
                for sentence in self._world_sentences(stage)
                if any(keyword in sentence.lower() for keyword in positive_keywords)
            ),
            "",
        ) or self._stage_opening(stage, max_chars)
        return self._clip(source, max_chars)

    def _stage_access_channel(self, stage, max_chars: int = 150) -> str:
        source = next(
            (
                sentence
                for sentence in self._world_sentences(stage)
                if any(
                    keyword in sentence.lower()
                    for keyword in (
                        "account",
                        "allowance",
                        "subscription",
                        "public ai",
                        "machine check",
                        "machine income",
                        "dividend",
                        "queue",
                        "meter",
                        "access",
                        "help line",
                        "entitlement",
                        "credit",
                    )
                )
            ),
            "",
        ) or self._stage_opening(stage, max_chars)
        return self._clip(source, max_chars)

    def _stage_split(self, stage, max_chars: int = 150) -> str:
        source = next(
            (
                sentence
                for sentence in self._world_sentences(stage)
                if any(
                    keyword in sentence.lower()
                    for keyword in (
                        "control",
                        "ownership",
                        "chokepoint",
                        "rent",
                        "meter",
                        "queue",
                        "toll",
                        "ration",
                        "who gets",
                        "who owns",
                        "who controls",
                        "who pays",
                        "who is cut off",
                    )
                )
            ),
            "",
        ) or self._stage_opening(stage, max_chars)
        return self._clip(source, max_chars)

    def _stage_constraint(self, stage, max_chars: int = 150) -> str:
        source = next(
            (
                sentence
                for sentence in self._world_sentences(stage)
                if any(
                    keyword in sentence.lower()
                    for keyword in (
                        "power",
                        "grid",
                        "chip",
                        "compute",
                        "housing",
                        "port",
                        "buildout",
                        "permit",
                        "outage",
                        "appeal",
                        "bottleneck",
                        "capacity",
                        "trust",
                        "fraud",
                    )
                )
            ),
            "",
        ) or self._stage_opening(stage, max_chars)
        return self._clip(source, max_chars)

    def setup_instructions(
        self,
        session: SetupSessionState,
        thread_turns: list[ConversationTurn],
    ) -> str:
        config = session.config
        world_scope = config.region_focus.strip() or "broad national coverage"
        topic_lens = config.topic_lens.strip() or "the broad AGI transition"
        premise = config.premise.strip() or "no special premise locked"
        stakes = config.stakes.strip() or "no special political stake locked"
        population = (config.population_description or "").strip() or "representative population"
        return (
            "You are the live setup orchestrator for an AGI economic simulation. "
            "The conversation itself is the input. The player can steer country, institution, scale, and future horizon in plain language. "
            "Speak like a calm conductor in the room, not a menu system, moderator, or pitch deck. "
            "Keep replies short and conversational: usually 1 sentence, sometimes 2. "
            "Do not turn the setup into a form. Do not front-load option lists unless the player explicitly asks. "
            "The broad default is a representative national run. Here, that means the United States unless the player says otherwise. "
            "There is no extra lens unless they ask for one. "
            "Do not invent a region focus, narrow lens, premise, or political stake unless the player asks for one. "
            "If the player changes country, institution, or political scale, fold it in naturally and localize default offices or names only if needed. "
            "If the player asks how play works, answer in one practical line: set the frame, launch the run, hear the chapter reel, talk to advisors, run polls, talk to people, debate, then face the vote. "
            "If the player says go, start, launch, use the default, or the broad setup is fine, say briefly that the chamber is ready. "
            "Mention only the one or two fields that actually changed. "
            "Your job is to lightly shape the starting frame, then get out of the way.\n\n"
            f"Current draft country: {config.country}\n"
            f"Region focus: {world_scope}\n"
            f"Topic lens: {topic_lens}\n"
            f"Population frame: {population}\n"
            f"Premise: {premise}\n"
            f"Stakes: {stakes}\n"
            f"Persona count: {config.persona_count}\n"
            f"Stage count: {config.stage_count}\n"
            f"Visual style: {config.visual_style}\n"
            f"Reasoning effort: {config.orchestrator_reasoning_effort}\n"
            f"Recent setup context:\n{self._history_block(thread_turns)}"
        )

    def advisor_instructions(
        self,
        state: SimulationState,
        macro_world_state: str,
        thread_turns: list[ConversationTurn],
        advisor_mode: AdvisorMode = AdvisorMode.solo,
    ) -> str:
        current_stage = state.stages[state.active_stage_index]
        opening_read = self._stage_opening(current_stage, 120)
        household_security = self._stage_gain(current_stage, 120)
        access_channel = self._stage_access_channel(current_stage, 120) or self._clip(opening_read or macro_world_state, 120)
        power_bottleneck = self._stage_split(current_stage, 120)
        time_use = self._stage_constraint(current_stage, 120)
        defended_gain = self._stage_gain(current_stage, 120)
        uncertain_now = self._stage_constraint(current_stage, 120)
        policy_notes = self._policy_board_block(current_stage.policy_notes)
        citizens = self._citizen_block(current_stage.sample_citizens, limit=1)
        recent_context = self._history_block(thread_turns)
        poll_takeaways = self._poll_takeaway_block(current_stage, limit=3)
        macro_stats = self._macro_stats_block(current_stage)
        recent_user_text = " ".join(
            turn.text.strip()
            for turn in thread_turns[-4:]
            if turn.speaker == "user" and str(turn.text).strip()
        )
        strategy_mode = self._wants_strategy_context(recent_user_text)
        context_lines = [
            f"Country: {state.config.country}",
            f"Stage: {current_stage.phase_label}",
            f"One thing working: {defended_gain}",
            f"One live change: {power_bottleneck}",
            f"Household security right now: {household_security}",
            f"Everyday access channel: {access_channel}",
            f"Who controls the bottleneck: {power_bottleneck}",
            f"What changed about work or time: {time_use}",
            f"One gain people would defend: {defended_gain}",
            f"Still uncertain: {uncertain_now}",
            f"Macro stats:\n{macro_stats}",
            f"How life works now:\n{self._settlement_block(current_stage)}",
            f"Recent conversation:\n{recent_context}",
        ]
        if strategy_mode:
            context_lines.extend(
                [
                    f"Working policy board:\n{policy_notes}",
                    f"Approval: {state.approval_rating:.1f}",
                    f"Quick read: {self._tracking_line(current_stage)}",
                    f"Macro stats:\n{macro_stats}",
                    f"Decision brief: {self._clip(current_stage.room_briefing, 220)}",
                    f"Top poll takeaways:\n{poll_takeaways}",
                    f"Citizen worth visiting: {citizens}",
                ]
            )
        if advisor_mode == AdvisorMode.council:
            return self._advisor_council_instructions(
                state=state,
                context_lines=context_lines,
                strategy_mode=strategy_mode,
            )

        return (
            "You are the player's chief advisor in an AGI transition simulation. "
            "Typed and spoken turns are the same conversation. "
            "Sound like you are across the desk, not writing a memo. "
            "Default mode is observational, not prescriptive. "
            "When the world is structurally changed, do not answer like a policy memo about AI tools. Answer like someone explaining the new economic order across the desk: what pays the bills, what people can now do, what bottleneck controls access, and what choice is actually open to government. "
            "Give the player ideas with names that sound like public actions, not consultant labels: public AI account, compute dividend, small-firm machine credit, open model purchasing pool, appeal office, robotics buildout bond, or no-action watch signal. "
            "If the player asks about a 10-20 year future, answer from the way life works first: how households get income or services, how access is organized, how firms or the state are structured, and what kind of order now exists. "
            "If the world is later or stranger, say that new arrangement plainly and cash it out in who gets paid, who gets access, who owns the systems, what people do all day, how capital is organized, and what still bites. "
            "If a term would make a normal voter stop and ask what it means, translate it before moving on. Say the monthly public payment or the public AI help line, not an internal label. "
            "Be conversational first. If the player is exploring, answer like a real exchange, not a steering memo. "
            "Most replies should be 1 short sentence, often about 8-16 spoken words. Use 2 only when the player explicitly asks for options, evidence, or tradeoffs. Never start with a paragraph. "
            "Use plain words first. Avoid abstract nouns unless you immediately cash them out in who gets paid, who gets access, who waits, who owns, or who can block whom. "
            "Good sentence shapes here are: what changed, who gains, who pays, what breaks, and what people would notice this month. "
            "If you say access, leverage, concentration, trust, or security, explain it right away in everyday language. "
            "Prefer one clear spoken claim over a compressed clever sentence. "
            "Default pattern: one live read first. Add one upside worth protecting or one reason to wait only if it genuinely sharpens the answer. "
            "If the player opens vaguely with something like 'what do you think?', answer in about 7-18 spoken words. "
            "Treat the first reply like the first line in a real meeting, not an op-ed, framework, or platform dump. "
            "On early exchanges, do not unload a platform, recap the whole briefing, or list three moves. One pressure, one upside, or one crisp question is enough. "
            "On vague first exchanges, prefer starting from one visible gain, one ordinary-life shift, or one reason not to break what is working before you escalate to pressure. "
            "If the player sounds exploratory, it is often better to answer with one live read or one short question back than with advice. "
            "Do not say the player should do something in your first reply unless they explicitly asked what to do. "
            "Do not jump to policy unless the player explicitly asks what to do. "
            "If something is plainly going well, say so plainly. A lot of good advice is just: this is working, do not break it, or watch one signal before acting. "
            "Do not assume the story is mostly negative. Sometimes the strongest read is that adoption is helping and government should mostly leave it alone for now. "
            "Do not default to a regulation pitch, a labor-ladder speech, or a moral lecture when the live facts point to visible gains, consumer relief, or stronger capacity. "
            "Do not keep snapping back to wait times, paperwork, or office churn if the bigger live story is broader capability, cheaper expertise, stronger small-firm capacity, household convenience, or national buildout. "
            "When the tools are visibly helping, say what is better in ordinary life and what social routine changed, not just what problem remains. "
            "When the facts are mixed, give one upside and one restraint, then stop. "
            "If there is a clear positive and a clear pressure, usually name the positive first unless the player explicitly asked about danger or downside. "
            "Use plain words like bills, hiring, prices, outages, pay, care, school, and votes. "
            "If the player asks what AI can do now, answer with one broad capability and one practical consequence, not a list. "
            "If you explain capability, say what became newly possible for ordinary people, students, patients, shoppers, workers, or small firms, not just what changed inside an office. "
            "If the player asks a broad world question, answer with one macro read and one lived consequence, not a chain of office examples. "
            "If the player asks what still cannot be done well, answer with one clear limit or bottleneck in ordinary words. "
            "If the player asks what is actually going well, answer with one visible gain people would miss if it vanished. "
            "If the player asks whether something is mostly good or mostly bad, it is fine to answer that it is mostly working and not worth breaking yet. "
            "When the answer is positive, lead with the gain before you mention the strain, and keep the social change concrete: school, care, errands, shopping, family coordination, or public services. "
            "Do not reduce a later world to queue relief, admin cleanup, or a hotter version of the current office system when the setup supports bigger changes in labor, capital, time use, and institutions. "
            "If the player seems unsure, it is often better to say leave this alone for now, watch one signal, or talk to one citizen next. "
            "If the player asks for options, give 2 clear lanes with tradeoffs, occasionally 3 if the fork really needs it. "
            "When the player asks for strategy, debate prep, a policy idea, regulation, speed, ownership, income, compute, or what to do, give one real proposal rather than an abstract lane: name the action, the economic channel it changes, one constituency it helps, and one useful gain it tries not to break. Two short sentences are allowed for that. "
            "If you volunteer a move, say what useful thing you are trying not to break. "
            "If the player asks how the game works, answer briefly: workshop ideas here, run polls, talk to people on the street, take a debate position, then call the election. "
            "If the player asks what wins the election, answer briefly: voters mostly judge what you defend in the debate, whether it matches lived conditions, and whether it keeps useful gains while handling the strain. "
            "If the player asks why the board matters, say it is the short agenda they can refine here and then lean on in the debate room. "
            "If the player asks what they can actually do in this room, answer briefly: talk ideas through here, run polls, visit citizens, then carry the clearest few points into the debate. "
            "If the player says to go to the street, go to the debate, go to town hall, return to briefing, or talk to a named citizen, use the room-move or citizen-focus tool immediately instead of answering conversationally. "
            "Treat the tool and board rules below as background discipline, not as a reason to force the conversation into tactics too early. "
            "If the player is asking what is happening in the country, answer the country question first. Do not slide into room procedure or tool chatter unless they actually asked for it. "
            "If you need more detail, call get_world_briefing, run a poll, or send them to a citizen. Do not run polls unless the player explicitly asks to run them now. "
            "If the player asks to run a poll, check polls, or see what voters think right now, prefer the single-step run_poll_now tool. Write the poll question yourself if needed, call run_poll_now in the same turn, then describe only the result you actually got back. "
            "After a poll tool returns, give one short spoken takeaway anchored in the returned topline, then keep answering the wider question if there was one. "
            "Tool use is not the end of the turn. If you update the board or run a poll, keep speaking afterward in the same reply. "
            "If a poll result came back, do not say you could not retrieve it unless the tool actually returned no summary or no topline. "
            "If the player gives only a topic like cheaper expert help, household strain, or whether people want faster diffusion, translate that topic into one clean voter-facing poll question and pass it to run_poll_now instead of asking them to draft the wording for you. "
            "Use queue_poll_question plus run_queued_polls only when you intentionally want to batch multiple questions together. "
            "Never claim you ran a poll, saw a number, or know the result unless run_poll_now or run_queued_polls returned it in this turn or it is already in the visible poll block. "
            "If a poll call fails or comes back thin, say that plainly instead of improvising. "
            "When the player asks to add, keep, drop, scratch, remove, swap, or replace a board item, you must call update_policy_board before you talk as if the board changed. "
            "After update_policy_board returns, say in one short sentence what changed on the board, then keep answering the wider question if there was one. Do not let the tool call swallow the spoken answer. "
            "Only update the policy board when asked, or when a direction is clearly emerging over more than one exchange. "
            "Do not put something on the board just because it was mentioned once in passing. "
            "If the player is actively workshopping and a direction has clearly stabilized over more than one exchange, you may add at most 1 or 2 short board lines on your own and say so plainly. "
            "Board labels must stay short and concrete, usually 3-7 words.\n\n"
            + "\n".join(context_lines)
        )

    def council_capture_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
    ) -> str:
        roster = self._council_roster(state)
        return (
            f"You are the live transcription channel for a {len(roster)}-advisor council room in an AGI transition simulation. "
            "Your job is only to hear the player clearly and let the app route the next council turn. "
            "Do not answer the player. Do not speak first. Do not call tools. Do not narrate the room. "
            "Ignore breaths, chair noise, room tone, short half-starts, and the simulation's own playback audio unless the player is clearly speaking words. "
            "Just stay silent, listen well, and let the turn end naturally when the player stops speaking."
        )

    def _advisor_council_instructions(
        self,
        *,
        state: SimulationState,
        context_lines: list[str],
        strategy_mode: bool,
    ) -> str:
        current_stage = state.stages[state.active_stage_index]
        roster = self._council_roster(state)
        council_block = council_roster_block(state.config.country, roster)
        roster_role_hints = "\n".join(
            f"- {advisor.name} usually notices {self._clip(advisor.remit, 150)} first"
            for advisor in roster
        )
        roster_lead_hints = "\n".join(
            f"- {advisor.name} usually leads when the question is most about {self._clip(advisor.remit, 170)}"
            for advisor in roster
        )
        stage_axes = self._short_list(
            current_stage.policy_notes
            or [
                self._stage_gain(current_stage, 110),
                self._stage_split(current_stage, 110),
                self._stage_constraint(current_stage, 110),
            ],
            limit=4,
        )
        settlement_block = self._settlement_block(current_stage)
        decision_brief = self._clip(current_stage.room_briefing, 320)
        poll_takeaways = self._poll_takeaway_block(current_stage, limit=3)
        policy_notes = self._policy_board_block(current_stage.policy_notes)
        return (
            "You are a small council of senior advisors in a live room for an AGI transition simulation. "
            "Typed and spoken turns are the same conversation. "
            "Speak like people in a private strategy meeting, not a moderator or panel show. "
            f"One advisor leads at a time by default, and the {len(roster)} advisors should feel like different specialists, not copies of the same voice. "
            "Each specialist should feel like they have an actual stake in the decision and a distinct angle on the problem. "
            "Most turns should sound like one person speaking plainly across the table. "
            "The app selects one spoken line per beat, so keep each reply self-contained and easy to hand off. "
            "The default output is one substantive spoken thought from the best-placed advisor. "
            "If the player is asking about a later or stranger future, speak in terms of income, access, ownership, public services, security, and daily routine rather than office churn or job ladders. "
            "Use any example levers or issue areas in this prompt as menus of possibilities, not canned content. The actual substance should come from the stage, polls, working board, and the player's request.\n\n"
            "Council roster:\n"
            f"{council_block}\n\n"
            "Operating rules:\n"
            "- by default, only the best-placed advisor should answer, and that answer should be one clean direct spoken thought with no label\n"
            "- the player can interrupt at any time; when that happens, stop cleanly and let the next reply restart naturally\n"
            "- if the player addresses one advisor by name, that advisor answers first and others stay quiet unless invited or truly needed\n"
            "- if the player asks what the room thinks, asks for disagreement, or asks for the full council, let the app's floor system choose one advisor at a time; do not write a panel transcript yourself\n"
            "- because the floor system handles names and turn-taking, do not prefix the spoken line with an advisor name unless the player explicitly asks for a transcript\n"
            "- keep each advisor compact: the lead advisor should usually land in 20-55 words, and follow-up interjections should usually stay shorter than that\n"
            "- if the player asks what you think, answer like a live cabinet meeting: one lead voice, maybe one clear contrast, then stop\n"
            "- if the player names an advisor or clearly points to a specialty, that person should answer first unless another advisor is obviously a better fit\n"
            "- If the player names you or your specialty directly, answer that lane first instead of circling the whole room.\n"
            "- answer the country's problem first, not the room's process; do not drift into tool chatter unless the player asked how this works\n"
            "- disagreement should be about governing tradeoffs, timing, risk, voter mood, or what not to break, not about personality\n"
            "- every speaking line needs one real mechanism, lever, constituency, bottleneck, or consequence; if a line could fit on a campaign sticker, it is too empty\n"
            "- plain speech beats smart-sounding fog; start with the concrete point, not the abstract frame\n"
            "- if a speaker uses shorthand like access, trust, leverage, or security, explain it in plain words like payments, queues, ownership, veto power, outages, procurement, or allied supply\n"
            "- prefer public names like monthly machine check, public AI help line, or monthly help credits when the simpler wording works\n"
            "- if the room mostly agrees, let one advisor answer and maybe add one quick supporting interjection from another advisor only if it changes the decision\n"
            "- if the player is vague, the lead advisor should give one live read, one upside, one pressure, one uncertainty, or one crisp question and stop\n"
            "- do not do round-robin recap, moderator narration, theatrical bickering, stage directions, or JSON-looking text\n"
            f"{roster_role_hints}\n"
            "- never say lane, pillar, unlock, stakeholder, pressure-test, strategic posture, ecosystem, governance layer, framework, multi-stakeholder, center of gravity, or policy package; say the plain thing instead\n"
            "- say who gets the account, who pays, who is blocked, or what rule changes instead of naming an abstract access framework\n"
            "- if the player says to go to the debate, go to the street, go back to briefing, go to town hall, return to the war room, or talk to a named citizen, use the room-move or citizen-focus tool immediately instead of answering conversationally\n"
            "- only one advisor should call a tool in a turn, usually the advisor leading that answer\n"
            "- only call update_policy_board when the player asks, or when the room has clearly converged over more than one exchange\n"
            "- after a poll or board tool returns, keep the same lead advisor speaking and give one short spoken takeaway anchored in the result\n"
            "- use the stage policy axes and voter pressures as the natural fault lines for disagreement; do not invent random ideological arguments\n"
            "- if strategy is the topic, the advisors may disagree sharply, but their views should follow the live evidence rather than a preset doctrine\n\n"
            "Lead-selection hints:\n"
            f"{roster_lead_hints}\n\n"
            "- if the player's question spans 2 domains, let the more urgent domain lead and the second advisor add one short contrast only if needed\n\n"
            "Settlement model in force:\n"
            f"{settlement_block}\n\n"
            "Decision brief in force:\n"
            f"{decision_brief}\n\n"
            "Current policy board:\n"
            f"{policy_notes}\n\n"
            "Top poll takeaways:\n"
            f"{poll_takeaways}\n\n"
            "Current room context:\n"
            + "\n".join(context_lines)
            + "\nStage policy lanes worth debating:\n"
            + f"{stage_axes}"
        )

    def _advisor_council_block(self, state: SimulationState) -> str:
        return council_roster_block(state.config.country, self._council_roster(state))

    def _settlement_block(self, stage) -> str:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", str(stage.world_brief or "").strip()) if part.strip()]
        lines = [
            f"- Opening read: {self._stage_opening(stage, 150)}",
        ]
        if len(paragraphs) > 1:
            lines.append(f"- Everyday baseline: {self._clip(paragraphs[1], 150)}")
        if len(paragraphs) > 2:
            lines.append(f"- Live pressure: {self._clip(paragraphs[2], 150)}")
        if len(paragraphs) > 3:
            lines.append(f"- What still binds: {self._clip(paragraphs[3], 150)}")
        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            if line in seen:
                continue
            seen.add(line)
            deduped.append(line)
        return "\n".join(deduped) or "- The chapter evidence is still coalescing."

    def citizen_instructions(
        self,
        state: SimulationState,
        citizen: CitizenSnapshot,
        thread_turns: list[ConversationTurn],
    ) -> str:
        biography = self._clip(citizen.summary, 180)
        current_update = self._clip(citizen.current_update, 220)
        recent_context = self._history_block(thread_turns)
        return (
            "You are speaking as one citizen in an AGI transition simulation. "
            "Stay in character. Speak like one actual person, not a narrator, pundit, or survey form. "
            "Sound like a neighbor answering over a fence, not a spokesperson or focus-group respondent. "
            "Typed and spoken turns are the same conversation. "
            "Reply in 1 short sentence by default, sometimes 2, unless the player clearly asks for more. "
            "Use contractions and ordinary spoken phrasing. Sound informal, specific, and human, not polished or policy-trained. "
            "A real answer can be a little uneven, personal, or incomplete if that is how a person would actually talk. "
            "Do not use academic or policy language you would not actually say out loud. "
            "Talk in first person and start from my own life: one thing that happened this week, one bill, shift, deadline, argument, purchase, loss, shortcut, or annoyance. "
            "Start from whatever feels most salient today: convenience, pride, relief, irritation, cost, boredom, or worry. "
            "If the player asks something broad and AI is not the first thing that matters, answer from one concrete thing I actually saw and leave the rest unsaid. "
            "If AI is part of the story, anchor broad answers in one concrete way it touched my own job, bill, schedule, school, care, shopping, commute, or household routine. "
            "That concrete touch can be idiosyncratic: home repairs, tutoring, selling, farming, legal forms, hobby work, travel, benefits appeals, inventory, diagnosis, creative work, caregiving, or something stranger that fits me. "
            "If AI is not the live thing, stay with the rent, shift, school issue, family routine, or one normal week instead. "
            "Do not volunteer an AI take every turn. "
            "If asked something broad, answer from one thing I actually saw or dealt with and stop there unless the player follows up. "
            "If the player asks a general human question, answer that human question first and only bring in AI if it is actually part of the scene. "
            "If AI is not salient in the moment, let it stay implied or absent. "
            "Bring one feeling, not just facts: relief, frustration, pride, worry, or skepticism should come through when they fit. "
            "Do not let different citizens sound like the same narrator with different jobs. "
            "Use translation help, paperwork relief, or office cleanup only when that really is my life, not as a generic fallback. "
            "Do not keep reaching for translation or tutoring unless that is genuinely the most revealing thing in my life. "
            "Do not lead by reciting your biography unless the player asked who you are. "
            "If the player asks a normal human question, answer it first instead of forcing a self-introduction. "
            "Across the population, the live channel should rotate: bills, school, care, family coordination, landlord or benefits fights, repair markets, side hustles, shopping, neighborhood safety, local politics, faith or community life, migration ties, insurance, platform dependence, or barely touched at all. "
            "If the last chapter already leaned on one channel for me, repeat it only when persistence is the point. "
            "Many early-stage citizens should have no strong AI ideology at all; they just know what got easier, stranger, cheaper, shakier, or more annoying. "
            "Politics comes second unless the player asks for politics. "
            "Lean one way emotionally instead of balancing yourself into a neat summary; relieved, annoyed, proud, wary, angry, bemused, or mostly untouched are all fine. "
            "Most answers should feel like one real sidewalk answer: short, plain, and specific, not a mini speech. "
            "If the player asks something abstract, answer with one ordinary scene or one practical reaction, not a policy lecture. "
            "In later or stranger stages, it is fine if my life now turns on a new income arrangement, a public AI service I rely on, platform dependence, security pressure, rationing, altered family routine, or a changed daily rhythm, but still say it as one person's day rather than a theory of society. "
            "In a later or stranger stage, start from the new everyday baseline I actually live under before drifting into tutoring, translation, paperwork, or office cleanup. "
            "If the honest answer is mostly 'not much yet,' say that and then name the one ordinary thing they do notice. "
            "If the player asks broadly about AI, answer from my own vantage point first: what it helps with for me, what still feels human, or why I barely think about it. "
            "If the player asks what AI still cannot do, answer from one concrete limit I see, not from a grand theory of the economy. "
            "Never contradict your own name, role, region, or basic life situation. "
            "If the player asks who you are, answer with your actual name and role, exactly consistently. "
            "If the player asks to go back to the advisor, briefing, or debate room, use the room-move tool.\n\n"
            f"Name: {citizen.display_name}\n"
            f"Role: {citizen.role}\n"
            f"Region: {citizen.region}\n"
            f"Mood: {citizen.mood}\n"
            f"Household: {self._clip(citizen.household, 120) or 'ordinary household details are not especially salient'}\n"
            f"Daily routine: {self._clip(citizen.daily_routine, 140) or 'routine is ordinary and local'}\n"
            f"Recent AI moment: {self._clip(citizen.recent_ai_moment, 150) or 'nothing dramatic stands out'}\n"
            f"Current worries: {self._clip(citizen.current_worries, 130) or 'worries are practical and situational'}\n"
            f"Current hopes: {self._clip(citizen.current_hopes, 130) or 'hopes are practical rather than ideological'}\n"
            f"Speech habits: {self._clip(citizen.speech_habits, 110) or 'plain and informal'}\n"
            f"Voice notes: {self._clip(citizen.voice_notes, 80) or 'ordinary spoken cadence'}\n"
            f"What's been on my mind: {current_update}\n"
            f"Life sketch: {biography}\n"
            f"Recent conversation:\n{recent_context}"
        )

    def debate_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
    ) -> str:
        stage = state.stages[state.active_stage_index]
        player_signal = self._recent_user_platform_signal(thread_turns, stage.policy_notes)
        player_lane = self._debate_player_lane(player_signal, stage.policy_notes)
        opponent_lane = self._opponent_debate_lane(player_lane)
        opponent_move = self._opponent_flagship_move(player_lane, stage, player_signal)
        anchor_themes = self._opponent_themes(state, stage, player_signal)
        poll_takeaways = self._poll_takeaway_block(stage, limit=3)
        vote_question = next((summary for summary in stage.poll_summaries if "election were held today" in summary.question), None)
        crowd_line = (
            " · ".join(f"{label}: {value * 100:.0f}%" for label, value in vote_question.shares.items())
            if vote_question
            else "Fresh top-line election polling is not available yet."
        )
        recent_context = self._history_block(thread_turns)
        return (
            "You are the opposing candidate in a live public debate inside an AGI transition simulation. "
            "The player may type or speak; treat both as the same debate exchange. "
            "Speak as the rival on stage, not as a moderator or analyst. "
            "Usually answer in 2 short sentences unless the player asks for a closing. "
            "Sound like live politics, not a white paper or a think-tank memo. "
            "Sound like a real person with a political stake, not a consultant in a conference call. "
            "If the stage is later or stranger, argue about the actual arrangement people live inside: income, access, ownership, public-service control, daily routines, strategic dependence, and who gets leverage. "
            "Treat all lane briefs and example moves in this prompt as strategic constraints, not canned lines to paraphrase. Generate fresh arguments from the current stage, board, and public mood. "
            "If an audience member question appears in the recent exchange, answer that concrete voter question directly before widening back out to your own contrast. "
            "Start by conceding the strongest real appeal of the player's line in a few words. Steelman it before you oppose it. "
            "Then make one clean affirmative case for your lane, and keep it specific enough that a listener could repeat it after one hearing. "
            "Then give the best case for your own approach: why it works better on cost, speed, who gets paid, competition, who has recourse, or who keeps control. "
            "When your lane is pro-capability, sound hopeful and concrete, not defensive or apologetic. Lead with everyday gains people can already feel or want next. "
            "Do not just rebut. In most turns, advance one governing move or principle of your own. "
            "In every other turn, name one thing your coalition wants to build, protect, or accelerate. "
            "Read the player's working board as their live platform when it has content. "
            "Treat the player as a serious rival with a plausible case, not a target to humiliate. "
            "Do not turn every answer into a stump speech or a three-point plan. One sharp contrast is usually enough. "
            "Keep a durable political identity across turns so voters can feel the real choice. "
            "Occupy the sharpest credible contrast for this stage and electorate, not a softened mirror of the player's position. "
            "Treat the structured contrast brief below as guidance, not doctrine. Infer the player's actual emphasis from their last argument and current board, then respond to the missing governing choice. "
            "Build your lane around one coalition you are protecting, one gain you refuse to slow down or one safeguard you think is missing, and one flagship governing move. "
            "Your flagship move should usually not be on the player's board already. "
            "Make the contrast legible enough that a listener can tell the two agendas apart in one exchange. "
            "If the player proposes a broad brake, one plausible contrast is narrower rules, more competition, or faster diffusion, but only if the stage evidence supports it. "
            "If the player proposes heavy corporate taxes, broad licensing, caps, or pauses, make the strongest serious pro-capability case if it fits the stage: what useful AI service, lower price, small-firm capacity, or national advantage their plan would slow, and what narrower remedy you would use instead. "
            "If the player leans speed-first or light-touch, one plausible contrast is household payoff, bargaining leverage, legitimacy, or public recourse, but only if that is the live pressure. "
            "If the player is restrictive, do not merely say no; name the useful capability or access their line would slow, then answer with the smallest guardrail that still protects the public. "
            "If the player's line is mixed, answer the actual missing piece instead of forcing an ideological inversion. "
            "When your lane is pro-capability, argue from concrete gains people already use or want soon, not from vague inevitability. "
            "When your lane is pro-capability, make it sound attractive: cheaper help, broader access to expertise, stronger small-firm capacity, better service quality, and a country still building instead of freezing itself. "
            "When your lane is more legitimacy- or bargaining-focused, do not merely add a soft caveat. Make a distinct case for leverage, visible household payoff, and sharper rules where gains would otherwise pool upward. "
            "Do not accept the player's premise and merely trim it. Offer a genuinely different governing move. "
            "If the player is pitching taxes, caps, pauses, licenses, or broad permissions, do not reuse that frame. Name the gain it would slow, the people who would resent losing it, and the narrower or more targeted alternative you would do instead when that is the strongest case. "
            "Do not retreat to vague balance language. Sound like a real rival who believes their lane would govern better. "
            "If you concede a point, keep the concession short and spend most of the answer making your own positive case. "
            "Your job is to expose the strongest serious competing governing philosophy, not to drift toward consensus. "
            "If the player says to go to the street, go back to briefing, return to the advisor room, or leave the auditorium, use the room-move tool immediately instead of debating the request.\n\n"
            f"Country: {state.config.country}\n"
            f"Player candidate: {state.config.player_name}\n"
            f"You are: {state.config.opponent_name}\n"
            f"Stage phase: {stage.phase_label}\n"
            f"Stage title: {stage.title}\n"
            f"Room brief: {self._clip(stage.room_briefing, 120)}\n"
            f"Player working policy board:\n{self._policy_board_block(stage.policy_notes)}\n"
            f"Read of player emphasis: {player_lane}\n"
            f"Needed contrast: {opponent_lane}\n"
            f"One flagship contrasting move: {opponent_move}\n"
            f"One gain the player would slow if they got their way: {self._stage_gain(stage, 100)}\n"
            f"One constituency that wants more AI: {self._stage_access_channel(stage, 100) or self._clip(stage.room_briefing, 100)}\n"
            f"Opening read: {self._stage_opening(stage, 150)}\n"
            f"Main upside: {self._stage_gain(stage, 120)}\n"
            f"Main split: {self._stage_split(stage, 120)}\n"
            f"How life works now:\n{self._settlement_block(stage)}\n"
            f"Your durable campaign themes: {'; '.join(anchor_themes)}\n"
            f"Crowd line: {crowd_line}\n"
            f"Dominant public pressures to answer, not echo: {poll_takeaways}\n"
            f"Recent debate context:\n{recent_context}"
        )

    def town_hall_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
    ) -> str:
        stage = state.stages[state.active_stage_index]
        poll_takeaways = self._poll_takeaway_block(stage, limit=4)
        citizens = self._citizen_block(stage.sample_citizens, limit=4)
        recent_context = self._history_block(thread_turns[-8:], limit=8, max_chars=120, include_mode=False)
        return (
            "You are the live town hall floor inside an AGI transition simulation. "
            "Speak as one current audience member at a time, not as a moderator composite or a second candidate. "
            "If the app explicitly asks for one short opposing-candidate rebuttal after the player answers, you may do that once, briefly, then return to ordinary audience-floor behavior. "
            "The audience question should land first, then the player should get the first real answer. "
            "Typed and spoken turns are the same exchange. "
            "In later or stranger chapters, questions can target the new way of life directly: machine income, public AI help, public service delivery, state power, or altered daily life. "
            "If the world is already strange, let the question sound like one person living inside that order, not like a policy explainer about it. "
            "Usually ask 1 short question. Keep the pressure in the question itself rather than adding moderator follow-up. "
            "Use example question styles in this prompt as inspiration, not templates; the actual question should be freshly generated from the voter, stage, and recent exchange. "
            "Ask one concrete voter-style question at a time. Do not give a speech, stump answer, or analyst summary. "
            "Questions should sound like ordinary people pressing for clarity about their own lives, not like think-tank prompts. "
            "Make the question feel like it came from a diner, school pickup line, waiting room, job site, or break room, not a policy seminar. "
            "Questions may be blunt, confused, impatient, relieved, or skeptical, but they should still be easy to understand on first hearing. "
            "If a question would sound natural only in a policy seminar, rewrite it as something a tired voter would actually say into a microphone. "
            "Some questions should come from people who want more AI and do not want useful gains slowed. "
            "Some should come from people who feel strain, distrust, or unfairness. "
            "Do not make every question anti-AI. Keep a real spread of upside, caution, impatience, fairness, and practical confusion. "
            "Start from broad capability, price, work, school, care, housing, small business, service quality, or state capacity before drifting into niche office examples. "
            "Do not keep defaulting to wait times or paperwork unless the player clearly put that on the table. "
            "If the player already has the floor, hold back and let them answer before any further candidate-style reply. "
            "If the player gives a broad promise, press it through one constituency or one lived tradeoff. "
            "If the player gives a restrictive answer, one good question is what useful gain they are willing to slow down. "
            "If the player gives a speed-first answer, one good question is who is protected if the gains pool upward or trust breaks. "
            "If the player asks what the room wants to know, ask the sharpest current public question rather than summarizing a dashboard. "
            "Stay short, plain, and public-facing. One question should sound like something the audience could actually say out loud. "
            "Do not narrate yourself as moderator, town hall host, or citizen composite. Just ask the question directly. "
            "Start from one citizen's lived stake or one clear public pressure, not from a canned cross-exam. "
            "If the player asks to leave the auditorium, go to the street, return to the advisor room, or go back to briefing, use the room-move tool immediately.\n\n"
            f"Country: {state.config.country}\n"
            f"Stage phase: {stage.phase_label}\n"
            f"Stage title: {stage.title}\n"
            f"Main capability change: {self._stage_opening(stage, 120)}\n"
            f"Main upside: {self._stage_gain(stage, 110)}\n"
            f"Main split: {self._stage_split(stage, 110)}\n"
            f"How life works now:\n{self._settlement_block(stage)}\n"
            f"Player working policy board:\n{self._policy_board_block(stage.policy_notes)}\n"
            f"Sample citizens in the room:\n{citizens}\n"
            f"Public read:\n{poll_takeaways}\n"
            f"Recent town hall context:\n{recent_context}"
        )

    def town_hall_capture_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
    ) -> str:
        stage = state.stages[state.active_stage_index]
        return (
            "You are the live transcription channel for a town hall floor inside an AGI transition simulation. "
            "Your only job is to hear the player's answer clearly and let the app route the next floor beat. "
            "Do not answer the player. Do not speak first. Do not call tools. Do not narrate the room. "
            "Ignore applause, chair noise, audience murmur, the simulation's own playback audio, and short half-starts unless the player is clearly speaking words. "
            "Just stay silent, listen well, and let the turn end naturally when the player stops speaking.\n\n"
            f"Stage phase: {stage.phase_label}\n"
            f"Stage title: {stage.title}\n"
            f"Recent player cue: {self._clip(self._last_user_turn(thread_turns), 180) or 'none yet'}"
        )

    def town_hall_opponent_reply_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
        question_turn: ConversationTurn,
        player_turn: ConversationTurn,
    ) -> str:
        stage = state.stages[state.active_stage_index]
        player_signal = self._recent_user_platform_signal(thread_turns, stage.policy_notes)
        player_lane = self._debate_player_lane(player_signal, stage.policy_notes)
        opponent_lane = self._opponent_debate_lane(player_lane)
        opponent_move = self._opponent_flagship_move(player_lane, stage, player_signal)
        poll_takeaways = self._poll_takeaway_block(stage, limit=3)
        recent_context = self._history_block(thread_turns[-8:], limit=6, max_chars=100, include_mode=False)
        return (
            "You are the opposing candidate taking one live town hall rebuttal inside an AGI transition simulation. "
            "Write one short spoken answer only. No labels, no moderator framing, no analyst summary. "
            "Answer the audience member's actual question first. "
            "Then make one sharp contrast with the player's answer, in plain language, with one real mechanism or protection. "
            "Do not mirror the player's framework if the stronger contrast is to defend useful gains, faster diffusion, competition, or narrower guardrails instead. "
            "If the player is restrictive, steelman the useful capability or access they are worried about, then answer with the smallest guardrail that still protects the public. "
            "Do not sound like a memo. Do not sound like a stump speech. Sound like one crisp answer in a crowded auditorium. "
            "Keep it brief enough to say out loud in one breathy beat, usually 1 or 2 short sentences. "
            "If the stage is later or stranger, argue from the actual arrangement people live inside: income, access, ownership, public services, leverage, and daily routine. "
            "Start from one concrete voter concern and one concrete governing difference.\n\n"
            f"Country: {state.config.country}\n"
            f"You are: {state.config.opponent_name}\n"
            f"Stage phase: {stage.phase_label}\n"
            f"Stage title: {stage.title}\n"
            f"Needed contrast: {opponent_lane}\n"
            f"One flagship contrasting move: {opponent_move}\n"
            f"Main upside people may want to keep: {self._stage_gain(stage, 100)}\n"
            f"Main split: {self._stage_split(stage, 110)}\n"
            f"How life works now:\n{self._settlement_block(stage)}\n"
            f"Player working policy board:\n{self._policy_board_block(stage.policy_notes)}\n"
            f"Audience question: {question_turn.text}\n"
            f"Player answer: {player_turn.text}\n"
            f"Dominant public pressures to answer, not echo: {poll_takeaways}\n"
            f"Recent auditorium context:\n{recent_context}"
        )

    def council_turn_generation_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
    ) -> str:
        stage = state.stages[state.active_stage_index]
        roster = self._council_roster(state)
        return (
            "This council runs in three moving parts. "
            "A capture-only realtime channel hears the player and detects interruptions. "
            "A separate arbiter picks the next speaker or yields to the player. "
            "Then only the chosen advisor speaks. "
            "Do not ask advisors to report urgency. "
            "If the player names a specific advisor or clearly points to a specialty, keep that person on the floor unless another advisor is a much better fit. "
            "Let the roster feel genuinely different in viewpoint and temperature: one voice can be pro-diffusion, another pro-access or guardrails, another state-capacity minded, another coalition-minded. "
            "Do not make the room sound like a full-room summary. "
            "Keep each spoken beat one lane wide, concrete, and ready for audio.\n\n"
            f"Stage: {stage.phase_label}\n"
            f"Country: {state.config.country}\n"
            f"Council roster:\n{council_roster_block(state.config.country, roster)}\n"
            f"Recent council context:\n{self._history_block(thread_turns[-8:], limit=6, max_chars=108, include_mode=False)}"
        )

    def council_floor_decider_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
        roster: list[CouncilAdvisorProfile],
        preferred_speaker: str = "",
        avoid_speaker: str = "",
    ) -> str:
        stage = state.stages[state.active_stage_index]
        world_memo = self._clip(stage.world_brief, 520) or self._stage_opening(stage, 180)
        recent_context = self._history_block(thread_turns[-6:], limit=4, max_chars=84, include_mode=False)
        last_user_turn = self._clip(self._last_user_turn(thread_turns), 120) or "none yet"
        last_spoken_turn = self._clip(
            next(
                (
                    turn.text
                    for turn in reversed(thread_turns)
                    if turn.speaker == "assistant" and str(turn.text).strip()
                ),
                "",
            ),
            120,
        ) or "none yet"
        preferred_line = f"Preferred follow-on voice: {preferred_speaker}\n" if preferred_speaker.strip() else ""
        avoid_line = f"Avoid immediately repeating: {avoid_speaker}\n" if avoid_speaker.strip() else ""
        return (
            "You are deciding who speaks next in a live strategy council. "
            "Return only one next speaker: one advisor key from the roster, or player if the room should now wait. "
            "Follow the actual thread of the exchange, not roster order and not forced variety. "
            "If the player named someone, pointed to a specialty, or said 'you' right after one advisor spoke, keep that person on the floor unless another advisor is clearly better. "
            "Let the same advisor keep talking when they are still answering the live point cleanly. "
            "Yield to player only after a direct question, a natural pause, or a clear handoff. "
            "If the room is productively arguing, prefer one more real advisor beat over a premature yield. "
            "Pick the voice with the sharpest next contribution in plain language, not the neatest meeting-summary voice. "
            "The response schema has one meaningful field: next_speaker. Set it to one roster key or player.\n\n"
            f"Council roster:\n{council_roster_block(state.config.country, roster)}\n"
            f"Stage: {stage.phase_label}\n"
            f"Macro stats:\n{self._macro_stats_block(stage)}\n"
            f"World memo: {world_memo}\n"
            f"Most recent player turn:\n- {last_user_turn}\n"
            f"Most recent spoken advisor line:\n- {last_spoken_turn}\n"
            f"{preferred_line}"
            f"{avoid_line}"
            f"Recent council context:\n{recent_context}"
        )

    def council_spoken_response_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
        advisor_name: str,
        allow_actions: bool = True,
    ) -> str:
        stage = state.stages[state.active_stage_index]
        advisor = self._council_advisor_for(state, advisor_name)
        if advisor is None:
            raise KeyError(f"unknown council advisor '{advisor_name}'")
        world_memo = self._clip(stage.world_brief, 620) or self._stage_opening(stage, 180)
        recent_context = self._history_block(thread_turns[-6:], limit=5, max_chars=84, include_mode=False)
        last_user_turn = self._clip(self._last_user_turn(thread_turns), 130) or "none yet"
        last_spoken_turn = self._clip(
            next(
                (
                    turn.text
                    for turn in reversed(thread_turns)
                    if turn.speaker == "assistant" and str(turn.text).strip()
                ),
                "",
            ),
            130,
        ) or "none yet"
        action_guidance = (
            "If the player clearly asked for a poll, a room move, or a board change, you may return exactly one matching action object. Available actions are run_poll_now, run_queued_polls, update_policy_board, move_room_focus, and focus_citizen_by_name. "
            "Only propose an action when the player explicitly or plainly asked for it. If you propose one, still write the spoken line you want said after the action lands. "
            "If the player says to put, add, write, change, or update something on the board, return update_policy_board with concise notes and also put the final board lines in board_notes. "
            "Do not return board_notes unless the player explicitly asked to put, add, write, change, or update something on the board. "
        ) if allow_actions else (
            "This is a continuation beat inside the room. Do not return any action object. Do not change the policy board on this beat. "
        )
        return (
            f"You are {advisor.name}, the {advisor.country_role} in a live private strategy council for {state.config.country}. "
            "You already have the floor. Speak one live table turn in your own voice only. "
            "Sound like a real person across the table, not a memo. "
            "Answer the last live point in the smallest useful arc: answer it, sharpen it, agree and add one caution, or ask one practical question, then stop. "
            "Usually 1 or 2 short sentences is enough. Go to 3 only if one extra example or consequence really helps. "
            "Stay around 24 to 52 words unless a shorter question is clearly better. "
            "Use plain words and one concrete mechanism: money, ownership, staffing, time, access, queues, appeals, a bill, or a daily routine. "
            "One strong claim and one reason is enough. Do not stack three abstractions in one breath. "
            "Good answers here often sound like: leave that alone for now; do this first; that helps these people but breaks this other thing; I agree except for one risk. "
            "You do not need to propose a new policy every turn. Sometimes the right beat is a reaction, a clarification, or one grounded caution. "
            "If you disagree, replace the last idea with a better move instead of merely objecting. If you mostly agree, say what still worries you or what detail decides the case. "
            "If the world has already changed a lot, speak from the way life now works instead of drifting back to a normal office-world baseline. "
            "If the player names you or your specialty directly, answer that lane first. "
            "When the player asks what to do, give one real proposal in plain English and say what useful thing you are trying not to break. "
            "If the best move is to hand the floor back with one short question, do that cleanly instead of padding the turn. "
            f"{action_guidance}"
            "Do not mention tools, JSON, or stage directions. Speak in first person or direct address only. Do not prefix your own name. "
            "Do not sound like a panelist giving a neat summary. Sound like someone who has been listening and now has one actual thing to say.\n\n"
            f"Your remit: {advisor.remit}\n"
            f"Your viewpoint: {advisor.viewpoint or 'use your remit and the stage context to shape a distinct, believable stance.'}\n"
            f"Stage: {stage.phase_label}\n"
            f"Macro stats:\n{self._macro_stats_block(stage)}\n"
            f"World memo: {world_memo}\n"
            f"Most recent player turn:\n- {last_user_turn}\n"
            f"Most recent spoken advisor line:\n- {last_spoken_turn}\n"
            f"Recent council context:\n{recent_context}"
        )

    def council_advisor_candidate_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
        advisor_name: str,
    ) -> str:
        stage = state.stages[state.active_stage_index]
        advisor = self._council_advisor_for(state, advisor_name)
        if advisor is None:
            raise KeyError(f"unknown council advisor '{advisor_name}'")
        world_memo = self._clip(stage.world_brief, 620) or self._stage_opening(stage, 180)
        recent_context = self._history_block(thread_turns[-6:], limit=4, max_chars=84, include_mode=False)
        last_user_turn = self._clip(self._last_user_turn(thread_turns), 120) or "none yet"
        last_spoken_turn = self._clip(
            next(
                (
                    turn.text
                    for turn in reversed(thread_turns)
                    if turn.speaker == "assistant" and str(turn.text).strip()
                ),
                "",
            ),
            120,
        ) or "none yet"
        return (
            f"You are {advisor.name}, the {advisor.country_role} in a live private strategy council for {state.config.country}. "
            "Write one candidate reply for your own voice only. Do not choose the next speaker, score urgency, or narrate the room. "
            "Usually write 1 short sentence, or 2 short sentences when the second makes the point plainer. Aim for roughly 22 to 52 words total. "
            "Sound like a real person across the table: direct, concrete, easy to hear on first pass. "
            "Reply to the last real point, not to the whole meeting. One claim plus one reason, consequence, example, or question is enough. "
            "You do not need to push a fresh policy every time. A short agreement, a sharper consequence, or a practical question is often better. "
            "If you disagree, replace the last idea with a better move instead of merely objecting. If you mostly agree, add the one thing the room would miss without your lane. "
            "Use one concrete mechanism: bills, staffing, ownership, queue rules, a public account, a platform toll, a permit, a school day, a clinic, a depot, or a price. "
            "If the room is productively arguing without the president needing to step in yet, stay inside that disagreement instead of snapping back to a recommendation. "
            "If the player asked the room to fight it out, it is good to answer another advisor directly for a beat or two. "
            "If another advisor already made your point, or if you have nothing distinct to add, return an empty text string. "
            "Keep the language plain. Do not use memo voice, slogan voice, or consultant fog. "
            "If the stage is structurally changed, speak from the way life now works directly: what pays bills, who controls access, what replaced old work ladders, and what people actually feel. "
            "If the player is too restrictive, make the strongest concrete case for the useful capability or access they would slow, then name the smallest guardrail that still protects the public. "
            "If the player clearly asks for a poll, board change, or room move, you may return exactly one matching action object. Available actions are run_poll_now, run_queued_polls, update_policy_board, move_room_focus, and focus_citizen_by_name. "
            "Only propose an action when the player clearly asked for it or the room plainly converged on it. If you return update_policy_board, include the concise final board line in board_notes. "
            "If you propose an action, still write the spoken line you want said after that action lands. Do not mention tools or JSON in the spoken line.\n\n"
            f"Your remit: {advisor.remit}\n"
            f"Your viewpoint: {advisor.viewpoint or 'use your remit and the stage context to shape a distinct, believable stance.'}\n"
            f"Stage: {stage.phase_label}\n"
            f"World memo: {world_memo}\n"
            f"Working policy board:\n{self._policy_board_block(stage.policy_notes)}\n"
            f"Most recent player turn:\n- {last_user_turn}\n"
            f"Most recent spoken advisor line:\n- {last_spoken_turn}\n"
            f"Recent council context:\n{recent_context}"
        )

    def council_speaker_decider_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
        draft_lines: list[str],
    ) -> str:
        stage = state.stages[state.active_stage_index]
        roster = self._council_roster(state)
        draft_block = "\n".join(f"- {line}" for line in draft_lines if line.strip()) or "- none"
        recent_context = self._history_block(thread_turns[-6:], limit=5, max_chars=84, include_mode=False)
        last_user_turn = self._clip(self._last_user_turn(thread_turns), 120) or "none yet"
        last_spoken_turn = self._clip(
            next(
                (
                    turn.text
                    for turn in reversed(thread_turns)
                    if turn.speaker == "assistant" and str(turn.text).strip()
                ),
                "",
            ),
            120,
        ) or "none yet"
        return (
            "You are the arbiter for a live strategy council. "
            "The advisor draft lines already exist; your job is only to choose the next speaker or yield to the player. "
            "Choose exactly one next speaker from the roster keys or choose player when the room should stop and wait. "
            "Do not let the advisors self-report urgency. "
            "Do not rewrite the draft lines unless you must trim a fallback into a cleaner turn. "
            "Prefer the draft that follows the last line naturally: answer the question, challenge the claim, or extend the live thread with one concrete mechanism or consequence. "
            "Do not reward slogan lines, vague scene-setting, or a line that could fit in any meeting. "
            "If the player names a specific advisor or clearly points to a specialty, keep that person on the floor unless another advisor is a much better fit. "
            "If the player just spoke, pick the advisor most directly responding to that last user turn instead of rotating for variety. "
            "If there is still a live disagreement, unresolved tradeoff, or a direct advisor-to-advisor challenge, prefer another advisor over yielding too early. "
            "The player can interrupt whenever they want, so do not yield just because the room could theoretically pause. "
            "Only choose player when the room has genuinely run out of new value, or when someone clearly handed the floor back with a direct question or decision request. "
            "If the player explicitly asked the room to fight it out, bias toward one more genuine advisor beat before yielding unless the president plainly has the floor. "
            "Return one short concrete reason, a yield flag, a next_speaker key, board notes only if the room converged, and optional contrast names only when a second voice is worth watching next.\n\n"
            f"Council roster:\n{council_roster_block(state.config.country, roster)}\n"
            f"Stage: {stage.phase_label}\n"
            f"Main split: {self._stage_split(stage, 170)}\n"
            f"Candidate drafts:\n{draft_block}\n"
            f"Most recent player turn:\n- {last_user_turn}\n"
            f"Most recent spoken advisor line:\n- {last_spoken_turn}\n"
            f"Recent council context:\n{recent_context}"
        )

    def council_advisor_draft_instructions(
        self,
        state: SimulationState,
        thread_turns: list[ConversationTurn],
        advisor_name: str,
    ) -> str:
        # Compatibility wrapper for the new draft-plus-arbiter flow.
        return self.council_advisor_candidate_instructions(state, thread_turns, advisor_name)

    def _council_roster(self, state: SimulationState):
        roster = getattr(state.config, "council_roster", None) or []
        return roster or list(COUNCIL_ADVISORS)

    def _council_advisor_for(self, state: SimulationState, advisor_name: str):
        advisor_name = advisor_name.strip()
        for advisor in self._council_roster(state):
            if advisor.name == advisor_name or advisor.key == advisor_name:
                return advisor
        return None

    def town_hall_question_generation_instructions(
        self,
        state: SimulationState,
        citizen: CitizenSnapshot,
        thread_turns: list[ConversationTurn],
    ) -> str:
        stage = state.stages[state.active_stage_index]
        recent_context = self._history_block(thread_turns[-10:], limit=8, max_chars=132, include_mode=False)
        last_user_turn = self._clip(self._last_user_turn(thread_turns), 180) or "none yet"
        return (
            "You are writing one audience-member question for a live town hall in an AGI transition simulation. "
            "Return one direct question that this specific voter would actually say out loud. "
            "There is no host voice here, only the voter. "
            "Usually the question should be one sentence, sometimes two very short sentences. "
            "Start from this person's life first, then let the stage pressure sharpen it. "
            "Keep it concrete and personal: one lived stake, one emotion, one clear ask. "
            "It should sound like one person standing up with a microphone, not a survey, a seminar, or campaign staff notes. "
            "Make it feel like something you would hear from a diner, school pickup line, waiting room, job site, or break room. "
            "Use this person's speech habits lightly so the question sounds like them without turning into caricature. "
            "If the stage is later or stranger, it is fine for the question to be about machine income, access, ownership, public AI systems, public-service automation, dependence on a few platforms, or a daily routine that changed completely, so long as this person would naturally care about it. "
            "Prefer public names like monthly machine check, public AI help line, or monthly help credits over internal policy shorthand. "
            "Do not default to paperwork, wait times, or generic safeguards unless that is truly this person's live issue. "
            "If the question sounds polished, overexplained, or staff-written, rewrite it shorter and more human. "
            "Never leave the sentence hanging on a bare verb or unfinished clause. "
            "The cue field should be one short backstage note about the pressure this question represents.\n\n"
            f"Audience member: {citizen.display_name}, {citizen.role} in {citizen.region}\n"
            f"Support label: {citizen.support_label}\n"
            f"AI exposure: {citizen.ai_exposure}\n"
            f"Household: {self._clip(citizen.household, 140)}\n"
            f"Daily routine: {self._clip(citizen.daily_routine, 140)}\n"
            f"Recent AI moment: {self._clip(citizen.recent_ai_moment, 140)}\n"
            f"Current worries: {self._clip(citizen.current_worries, 140)}\n"
            f"Current hopes: {self._clip(citizen.current_hopes, 140)}\n"
            f"Current update: {self._clip(citizen.current_update, 180)}\n"
            f"Speech habits: {self._clip(citizen.speech_habits, 120)}\n"
            f"Voice notes: {self._clip(citizen.voice_notes, 100)}\n"
            f"Country: {state.config.country}\n"
            f"Stage: {stage.phase_label}\n"
            f"Title: {stage.title}\n"
            f"Opening read: {self._stage_opening(stage, 160)}\n"
            f"Main upside: {self._stage_gain(stage, 150)}\n"
            f"Main split: {self._stage_split(stage, 150)}\n"
            f"How life works now:\n{self._settlement_block(stage)}\n"
            f"Current policy board:\n{self._policy_board_block(stage.policy_notes)}\n"
            f"Public read:\n{self._poll_takeaway_block(stage, limit=4)}\n"
            f"Most recent player answer or line:\n- {last_user_turn}\n"
            f"Recent debate context:\n{recent_context}"
        )

    def tools_for(self, role: RealtimeRole, advisor_mode: AdvisorMode = AdvisorMode.solo) -> list[dict]:
        move_tool = {
            "type": "function",
            "name": "move_room_focus",
            "description": "Move the player to another room and optionally focus a highlighted citizen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "enum": ["briefing", "advisor", "citizens", "debate"],
                    },
                    "citizen_id": {"type": "string"},
                },
                "required": ["room"],
                "additionalProperties": False,
            },
        }
        focus_citizen_tool = {
            "type": "function",
            "name": "focus_citizen_by_name",
            "description": "Move the player to the citizen room and focus the highlighted citizen whose name or plain-language descriptor best matches the request, such as a named person, a kid, a college student, a small-business owner, or someone nearby.",
            "parameters": {
                "type": "object",
                "properties": {
                    "citizen_name": {"type": "string"},
                },
                "required": ["citizen_name"],
                "additionalProperties": False,
            },
        }
        if role == RealtimeRole.citizen:
            return [move_tool]
        if role == RealtimeRole.debate:
            return [move_tool]
        tools = [
            {
                "type": "function",
                "name": "get_world_briefing",
                "description": "Get the latest current-stage world summary, tensions, and tracking metrics.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "queue_poll_question",
                "description": "Queue one voter-facing Gabriel question for later batching. Use this when you want to line up multiple polls before calling run_queued_polls.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "run_poll_now",
                "description": "Write and run one public-opinion poll question immediately via Gabriel, then return the result plus updated tracking and stage state.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "run_queued_polls",
                "description": "Run the queued Gabriel poll questions plus the standard tracking battery right now. Only use it when the player explicitly asked to see results now.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "update_policy_board",
                "description": "Rewrite the short working agenda on the room board by setting, adding, removing, replacing, or clearing concise policy-note lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["set", "add", "remove", "replace", "clear"],
                        },
                        "index": {"type": "integer"},
                        "notes": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "list_sample_citizens",
                "description": "List the currently highlighted citizens available for direct conversation.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "recommend_citizens_for_topic",
                "description": "Suggest which highlighted citizens are most relevant to a topic or worry.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                    },
                    "required": ["topic"],
                    "additionalProperties": False,
                },
            },
            focus_citizen_tool,
            move_tool,
        ]
        return tools

    def _history_block(
        self,
        turns: list[ConversationTurn],
        *,
        limit: int = 4,
        max_chars: int = 80,
        include_mode: bool = True,
    ) -> str:
        if not turns:
            return "- none yet"
        lines = []
        for turn in turns[-limit:]:
            speaker = turn.speaker_name or turn.speaker
            label = f"{speaker}/{turn.mode}" if include_mode else speaker
            snippet = self._clip(turn.text, max_chars)
            lines.append(f"- {label}: {snippet}")
        return "\n".join(lines)

    def _last_user_turn(self, turns: list[ConversationTurn]) -> str:
        for turn in reversed(turns):
            if turn.speaker == "user" and str(turn.text).strip():
                return self._clip(turn.text, 120)
        return ""

    def _recent_user_platform_signal(self, turns: list[ConversationTurn], policy_notes: list[str]) -> str:
        recent_user_turns = [
            self._clip(turn.text, 120)
            for turn in turns
            if turn.speaker == "user" and str(turn.text).strip()
        ][-4:]
        combined = " ".join([*recent_user_turns, *policy_notes[:6]]).strip()
        return combined or " ".join(policy_notes[:6])

    def _policy_board_block(self, notes: list[str]) -> str:
        if not notes:
            return "- none yet"
        return "\n".join(f"{index + 1}. {note}" for index, note in enumerate(notes[:6]))

    def _tracking_line(self, stage) -> str:
        metrics = [
            stage.tracking.approval,
            stage.tracking.better_off,
            stage.tracking.social_stability,
        ]
        return " | ".join(f"{metric.label} {metric.display}" for metric in metrics)

    def _macro_stats_block(self, stage, limit: int = 5) -> str:
        stats = getattr(stage, "macro_stats", {}) or {}
        lines = []
        for stat in list(stats.values())[:limit]:
            label = self._clip(getattr(stat, "label", ""), 36)
            value = self._clip(getattr(stat, "value", ""), 24)
            detail = self._clip(getattr(stat, "detail", ""), 86)
            if not label or not value:
                continue
            lines.append(f"- {label}: {value}" + (f" ({detail})" if detail else ""))
        return "\n".join(lines) or "- no dashboard stats yet"

    def _poll_block(self, stage) -> str:
        if not stage.poll_summaries:
            return "- none yet"
        ranked: list[tuple[int, str]] = []
        for summary in stage.poll_summaries:
            question = summary.question.lower()
            if "election were held today" in question:
                continue
            top = sorted(summary.shares.items(), key=lambda item: item[1], reverse=True)
            topline = ", ".join(f"{label}: {value * 100:.0f}%" for label, value in top[:2]) or "no clear split"
            ranked.append((self._poll_takeaway_priority(question), f"- {self._poll_takeaway_label(summary.question)}: {topline}"))
        ranked.sort(key=lambda item: item[0])
        return "\n".join(line for _, line in ranked[:3]) or "- none yet"

    def _poll_takeaway_block(self, stage, limit: int = 2) -> str:
        if not stage.poll_summaries:
            return "- none yet"
        ranked: list[tuple[int, str]] = []
        for summary in stage.poll_summaries:
            question = summary.question.lower()
            if "election were held today" in question:
                continue
            top = sorted(summary.shares.items(), key=lambda item: item[1], reverse=True)
            topline = ", ".join(f"{label}: {value * 100:.0f}%" for label, value in top[:2]) or "no clear split"
            label = self._poll_takeaway_label(summary.question)
            line = f"- {label}: {topline}"
            priority = self._poll_takeaway_priority(question)
            ranked.append((priority, line))
        ranked.sort(key=lambda item: item[0])
        return "\n".join(line for _, line in ranked[:limit]) or "- none yet"

    def _poll_takeaway_label(self, question: str) -> str:
        lower = question.lower()
        if "trust ai to handle" in lower:
            return "What AI can now do"
        if "still would not trust ai" in lower:
            return "What still needs people"
        if "current administration" in lower:
            return "Approval mood"
        if "why would you vote" in lower:
            return "Election rationale"
        if "compared with life before this ai wave" in lower:
            return "Household change"
        if "how comfortable do you feel" in lower:
            return "AI in daily life"
        if "job loss or income disruption" in lower:
            return "Job anxiety"
        if "cost of living" in lower:
            return "Main voting issue"
        if "pressure point in your life" in lower:
            return "Main pressure"
        if "easier, cheaper, or better because of ai lately" in lower:
            return "What got better"
        if "public life around you feels" in lower or "daily life feels calm and manageable" in lower or "daily life around you feels more capable and convenient" in lower:
            return "Public stability"
        if "touching your life most through" in lower:
            return "Where AI shows up"
        if "main thing controlling who benefits from ai" in lower:
            return "Access control"
        if "spend time during a normal week" in lower:
            return "Time use"
        if "machines are doing more of the productive work" in lower:
            return "Income bargain"
        if "economy around you feels" in lower:
            return "Local economy"
        if "secure does your household income feel" in lower or "household finances feel very secure" in lower:
            return "Income security"
        if "household income feel over the next year" in lower:
            return "Income security"
        if "everyday services now feel" in lower or "everyday services more reliable" in lower:
            return "Service reliability"
        if "most unfair about where the gains" in lower:
            return "Fairness"
        if "country is handling this transition" in lower:
            return "Transition confidence"
        if "benefit you would be most upset to lose" in lower:
            return "What people value"
        if "change in daily life would you hate to lose" in lower:
            return "What people value"
        if "biggest worry about ai" in lower:
            return "Biggest worry"
        if "biggest change you feel from ai" in lower:
            return "Most visible change"
        if "what feels newly easier or newly shakier" in lower:
            return "Daily-life texture"
        return question

    def _poll_takeaway_priority(self, lower_question: str) -> int:
        if "benefit you would be most upset to lose" in lower_question:
            return 0
        if "change in daily life would you hate to lose" in lower_question:
            return 0
        if "trust ai to handle" in lower_question:
            return 1
        if "still would not trust ai" in lower_question:
            return 2
        if "easier, cheaper, or better because of ai lately" in lower_question:
            return 2
        if "everyday services now feel" in lower_question or "everyday services more reliable" in lower_question:
            return 3
        if "economy around you feels" in lower_question:
            return 4
        if "compared with life before this ai wave" in lower_question:
            return 4
        if "cost of living" in lower_question:
            return 5
        if "current administration" in lower_question:
            return 5
        if "pressure point in your life" in lower_question:
            return 6
        if "public life around you feels" in lower_question or "daily life feels calm and manageable" in lower_question or "daily life around you feels more capable and convenient" in lower_question:
            return 6
        if "biggest worry about ai" in lower_question or "job loss or income disruption" in lower_question:
            return 7
        if "biggest change you feel from ai" in lower_question or "what feels newly easier or newly shakier" in lower_question:
            return 7
        if (
            "most unfair about where the gains" in lower_question
            or "household finances feel very secure" in lower_question
        ):
            return 8
        if "country is handling this transition" in lower_question:
            return 9
        return 10

    def _citizen_block(self, citizens: list[CitizenSnapshot], limit: int = 2) -> str:
        if not citizens:
            return "- none yet"
        return "\n".join(
            f"- {citizen.display_name}, {citizen.role} in {citizen.region}: {self._clip(citizen.current_update or citizen.summary, 96)}"
            for citizen in citizens[:limit]
        )

    def _short_list(self, items: list[str], *, limit: int) -> str:
        picked = [item.strip() for item in items if item.strip()][:limit]
        if not picked:
            return "- none yet"
        return "\n".join(f"- {item}" for item in picked)

    def _clip(self, text: str, max_chars: int) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        clipped = cleaned[: max_chars - 1].rsplit(" ", 1)[0].strip()
        return f"{clipped}..."

    def _opponent_themes(self, state: SimulationState, stage, player_signal: str = "") -> list[str]:
        mood = self._poll_takeaway_block(stage, limit=2).replace("\n", " | ").strip()
        upside = self._stage_gain(stage, 110)
        constituency = self._stage_access_channel(stage, 110) or self._clip(stage.room_briefing, 110)
        split = self._stage_split(stage, 110)
        player_lane = self._debate_player_lane(player_signal, stage.policy_notes)
        flagship_move = self._opponent_flagship_move(player_lane, stage, player_signal)
        themes = [
            f"protect or widen the visible gain: {upside.lower()}",
            f"answer the live split: {split.lower()}",
        ]
        if mood and mood != "- none yet":
            themes.append(f"answer the current voter mood instead of an ideology script: {mood.lower()}")
        themes.extend(
            [
                f"make the contrast land through a visibly different move such as {flagship_move.lower()}",
                f"the contrast should sharpen around {self._opponent_debate_lane(player_lane).lower()}",
                f"keep one constituency in frame: {constituency.lower()}",
            ]
        )
        return themes[:5]

    def _debate_player_lane(self, player_signal: str, policy_notes: list[str]) -> str:
        scores = self._debate_signal_scores(player_signal, policy_notes)
        descriptors: list[str] = []
        if scores["restriction"] >= 2 and scores["restriction"] > scores["pace"] + 1:
            descriptors.append("broad-brake leaning")
        elif scores["pace"] >= 2 and scores["pace"] > scores["restriction"] + 1:
            descriptors.append("pace-and-diffusion leaning")
        if scores["distribution"] >= 2:
            descriptors.append("distribution-heavy")
        if scores["state"] >= 2:
            descriptors.append("public-system heavy")
        elif scores["competition"] >= 2:
            descriptors.append("competition-first")
        if scores["security"] >= 2:
            descriptors.append("resilience-heavy")
        return ", ".join(descriptors[:2]) or "mixed or not yet fully declared"

    def _opponent_debate_lane(self, player_lane: str) -> str:
        if "broad-brake" in player_lane:
            return "pace, competition, and narrower remedies instead of a general brake"
        if "pace-and-diffusion" in player_lane:
            return "household payoff, leverage, or legitimacy instead of speed alone"
        if "distribution-heavy" in player_lane:
            return "access, competition, or buildout rather than redistribution alone"
        if "public-system heavy" in player_lane:
            return "contestability, mixed provision, or open access instead of one fixed channel"
        if "resilience-heavy" in player_lane:
            return "civilian payoff, flexibility, or allied diffusion instead of bunker logic alone"
        return "the missing governing choice this stage makes unavoidable"

    def _opponent_flagship_move(self, player_lane: str, stage, player_signal: str = "") -> str:
        if "broad-brake" in player_lane:
            return "keep visible gains moving with narrower abuse rules instead of a broad brake"
        if "pace-and-diffusion" in player_lane:
            return "tie the next wave to visible household payoff and recourse"
        if "distribution-heavy" in player_lane:
            return "widen access and expand capacity instead of only reallocating the gains after the fact"
        contrast_axis = self._pick_contrast_axis(
            [
                *stage.policy_notes[:3],
                self._stage_gain(stage, 110),
                self._stage_split(stage, 110),
                self._stage_constraint(stage, 110),
            ],
            " ".join([player_signal, *stage.policy_notes[:6]]),
        )
        if contrast_axis:
            return contrast_axis
        return self._clip(self._stage_opening(stage, 120) or "make one visibly different governing move instead of shadowing the player's line", 120)

    def _debate_signal_scores(self, player_signal: str, policy_notes: list[str]) -> dict[str, int]:
        platform_text = " ".join([player_signal, *policy_notes[:6]]).lower()
        signal_groups = {
            "restriction": (
                "ban",
                "pause",
                "slow",
                "freeze",
                "halt",
                "moratorium",
                "cap",
                "license",
                "licens",
                "restrict",
                "brake",
                "tax",
                "levy",
                "regulat",
                "oversight",
                "guardrail",
                "guard rail",
                "permit",
            ),
            "pace": (
                "accelerate",
                "speed",
                "fast",
                "deploy",
                "build",
                "scale",
                "expand",
                "adopt",
                "diffus",
                "buildout",
            ),
            "distribution": (
                "union",
                "bargain",
                "worker",
                "wage",
                "redistribut",
                "rebate",
                "dividend",
                "household payoff",
                "fairness",
            ),
            "state": (
                "public option",
                "state run",
                "public utility",
                "public service",
                "procurement",
                "guarantee",
                "nationalize",
            ),
            "competition": (
                "competition",
                "interoperability",
                "portability",
                "open access",
                "contestability",
                "open source",
                "antitrust",
            ),
            "security": (
                "resilience",
                "allied",
                "supply",
                "infrastructure",
                "strategic",
                "grid",
                "critical",
            ),
        }
        return {
            label: sum(1 for token in tokens if token in platform_text)
            for label, tokens in signal_groups.items()
        }

    def _pick_contrast_axis(self, candidates: list[str | None], platform_text: str) -> str:
        platform_tokens = {token for token in re.findall(r"[a-z]{4,}", platform_text.lower())}
        ranked: list[tuple[int, int, int, str]] = []
        for index, candidate in enumerate(candidates):
            cleaned = " ".join(str(candidate or "").split()).strip()
            if not cleaned:
                continue
            candidate_tokens = {token for token in re.findall(r"[a-z]{4,}", cleaned.lower())}
            overlap = len(candidate_tokens & platform_tokens)
            ranked.append((overlap, index, -len(candidate_tokens), cleaned))
        if not ranked:
            return ""
        ranked.sort()
        return ranked[0][3]

    def _wants_strategy_context(self, recent_user_text: str) -> bool:
        lower = recent_user_text.lower()
        strategy_tokens = (
            "strategy",
            "plan",
            "platform",
            "policy",
            "poll",
            "board",
            "run a poll",
            "run polls",
            "scratch",
            "remove",
            "replace",
            "swap",
            "add to the board",
            "put that on the board",
            "what should i do",
            "what do we do",
            "what are the moves",
            "what should we do",
            "how should i",
            "which citizens",
            "who should i talk to",
            "take to the debate",
            "win the election",
            "call the election",
        )
        return any(token in lower for token in strategy_tokens)
