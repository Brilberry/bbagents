"""Consent-based social actions; the game owns transactions, not the model.

All mutations run serially on the simulation thread. Framework ActionRuntime
provides authorization/results; versioned state stores and Plans track offers
and shared work. This is not a distributed transaction or recovery service.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
import json

from bbagents import Action, ActionRuntime, AuthorizationDecision, Observation, Plan, PlanStep, StepStatus, ToolCall
from bbagents.memory import MemoryRecord
from bbagents.persistence import InMemoryStateStore
from bbagents.provenance import Provenance
from .progression import RULES, NUTRITION, missing_prerequisites


ITEMS = {"wood", "stone", "berries", "ration", "spear", "axe", "flask", "water", "fish", "fishing_rod", "bow", "meat"}
ITEMS |= {"vegetables", "cooked_fish", "cooked_meat", "knife", "hide", "leather", "leather_garment"}
ITEMS.add("reservoir")
TOPICS = {"water", "food", "wood", "shelter"} | set(RULES)
SOCIAL_TOOLS = {"trade", "collaborate", "respond_offer", "cancel_offer", "work_project",
                "leave_project", "collect_water", "use_water", "communicate"}
SHAPES = {"trade": ({"agent_id", "offer", "request"}, set()),
          "collaborate": ({"agent_id", "project"}, set()),
          "respond_offer": ({"offer_id", "accept"}, set()),
          "cancel_offer": ({"offer_id"}, set()),
          "work_project": ({"project_id"}, set()),
          "leave_project": ({"project_id"}, set()),
          "collect_water": (set(), set()), "use_water": (set(), set()),
          "communicate": ({"agent_id", "message"}, set())}


@dataclass(frozen=True)
class SocialTraits:
    generosity: int = 50
    cooperation: int = 50
    caution: int = 50


def bundle(value):
    if not isinstance(value, dict) or len(value) > len(ITEMS):
        raise ValueError("items must be a bounded item/count object")
    if any(key not in ITEMS or type(count) is not int or not 1 <= count <= 50
           for key, count in value.items()):
        raise ValueError("unknown item or invalid positive integer quantity")
    return dict(value)


def validate_social(value):
    if not isinstance(value, dict) or set(value) != {"tool", "arguments"}:
        raise ValueError("social action requires tool and arguments")
    tool, args = value["tool"], value["arguments"]
    if not isinstance(tool, str) or tool not in SHAPES or not isinstance(args, dict):
        raise ValueError("unsupported social action")
    required, optional = SHAPES[tool]
    if not required <= set(args) <= required | optional:
        raise ValueError("invalid social action arguments")
    if "agent_id" in args and not isinstance(args["agent_id"], str):
        raise ValueError("recipient must be an ID")
    for key in ("offer_id", "project_id"):
        if key in args and not isinstance(args[key], str):
            raise ValueError("identity must be a string")
    if tool == "respond_offer" and type(args["accept"]) is not bool:
        raise ValueError("accept must be boolean")
    if tool == "trade":
        bundle(args["offer"])
        bundle(args["request"])
    if tool == "collaborate" and not isinstance(args["project"], dict):
        raise ValueError("project must be an object")
    if tool == "communicate" and not isinstance(args["message"], (str, dict)):
        raise ValueError("message must be text or a structured message")
    if len(json.dumps(value)) > 2400:
        raise ValueError("social action too large")
    return deepcopy(value)


class SocialAuthorization:
    def __init__(self, world):
        self.world = world

    def decide(self, action):
        agent = self.world.agent(action.metadata.get("actor"))
        if agent is None or not agent.alive or agent.sleep_remaining:
            return AuthorizationDecision.deny("social actor must be alive and awake")
        if action.name not in SHAPES:
            return AuthorizationDecision.deny("social capability not granted")
        return AuthorizationDecision.allow("actor-bound social capability")


class SocialWorld:
    def __init__(self, simulation):
        self.sim = simulation
        self.offers = InMemoryStateStore()
        self.projects = InMemoryStateStore()
        self.offer_ids = []
        self.project_ids = []
        self.active_offers = set()
        self.active_sites = {}
        self.serial = 0
        self.project_plans = {}
        self.runtime = ActionRuntime({name: self._handler(name) for name in SHAPES},
                                     policy=SocialAuthorization(self))

    def _handler(self, name):
        return lambda actor, **args: getattr(self, "do_" + name)(self.agent(actor), **args)

    def agent(self, identity):
        return next((agent for agent in self.sim.agents if agent.id == identity), None)

    def execute(self, actor, tool, **args):
        validate_social({"tool": tool, "arguments": args})
        action = Action(tool, {"actor": actor.id, **deepcopy(args)}, metadata={"actor": actor.id})
        result = self.runtime.execute(action, retries=0)
        self.sim.trace.record("social.action", {"agent": actor.id, "tool": tool,
            "status": result.status.value, "error": result.error, "attempts": result.attempts})
        if not result.success:
            raise ValueError(result.error)
        return result.output

    def contact(self, actor, recipient):
        other = self.agent(recipient)
        if (other is None or not other.alive or other.id not in actor.known_survivors
                or other not in self.sim.nearby_survivors(actor)):
            raise ValueError("recipient must be discovered, alive, and nearby")
        return other

    def _record(self, event, actors, **data):
        for actor in actors:
            self.sim.trace.record(event, {"agent": actor.id, **data})
            actor.memory.remember(MemoryRecord(f"{event}: {json.dumps(data, default=str)}",
                source="social", metadata={"tick": self.sim.tick, **data},
                provenance=Provenance.internal("island-transactions")))

    def message(self, actor, recipient, payload):
        return self.sim.communicate(actor, recipient, payload)

    def pending(self, actor):
        rows = [deepcopy(self.offers.load(key).value) for key in sorted(self.active_offers)
                if self.offers.load(key).value["status"] == "pending"
                and actor.id in (self.offers.load(key).value["sender"], self.offers.load(key).value["recipient"])]
        for row in rows:
            if row["recipient"] == actor.id and "information" in row:
                row["information"] = {"topic": row["information"]["topic"]}
        return rows

    def offer(self, actor, recipient, kind, give=None, want=None, **details):
        other = self.contact(actor, recipient)
        if other.sleep_remaining:
            raise ValueError("cannot negotiate with a sleeping survivor")
        give, want = bundle(give or {}), bundle(want or {})
        if any(actor.inventory[item] < amount for item, amount in give.items()):
            raise ValueError("cannot offer items not owned")
        if len(self.pending(actor)) >= 4 or len(self.pending(other)) >= 4:
            raise ValueError("too many pending offers")
        if kind == "trade" and not (give or want):
            raise ValueError("empty trade")
        if any(row["recipient"] == other.id and row["kind"] == kind and row["sender"] == actor.id
               for row in self.pending(actor)):
            raise ValueError("an offer of this kind is already pending with this survivor")
        self.serial += 1
        identity = f"offer-{self.serial}"
        value = {"id": identity, "sender": actor.id, "recipient": other.id, "kind": kind,
                 "give": give, "want": want, "created_tick": self.sim.tick,
                 "expires_tick": self.sim.tick + 32, "status": "pending", **deepcopy(details)}
        self.offers.save(identity, value, expected_version=0)
        self.offer_ids.append(identity)
        self.active_offers.add(identity)
        self.message(actor, other.id, {"kind": "offer", "offer_id": identity})
        self._record("social.offer_created", [actor, other], offer=identity, kind=kind,
                     give=give, want=want, expires_tick=value["expires_tick"], version=1)
        return Observation(kind, f"proposed {kind} {identity} to {other.name}", {"offer_id": identity})

    def do_trade(self, actor, agent_id, offer, request):
        if not actor.behavior.get("trading", {}).get("enabled", True):
            raise ValueError("trading disabled by survivor policy")
        if actor.behavior:
            for group in (tuple(NUTRITION), ("water",)):
                if any(offer.get(item, 0) for item in group):
                    remaining = sum(actor.inventory[item] - offer.get(item, 0) + request.get(item, 0) for item in group)
                    if remaining < actor.behavior["trading"]["reserve"]:
                        raise ValueError("trade would spend reserved supplies")
        return self.offer(actor, agent_id, "trade", offer, request)

    def information(self, actor, topic):
        if topic not in TOPICS:
            raise ValueError("unsupported information topic")
        if topic in RULES:
            if topic not in actor.learned_skills:
                raise ValueError(f"survivor has not learned {topic}")
            return {"topic": topic, "skill": topic}
        if topic == "water":
            position = self.sim.policy.nearest_known_water(actor)
        elif topic == "shelter":
            position = actor.home
        else:
            resource = "berries" if topic == "food" else "wood"
            positions = actor.known_resources.get(resource, set())
            position = min(positions, key=lambda pos: self.sim.distance(actor.position, pos), default=None)
        if position is None or position not in actor.known_tiles:
            raise ValueError("survivor does not know this information")
        return {"topic": topic, "position": list(position), "terrain": actor.known_tiles[position].value}

    def learn(self, actor, information, sender):
        if information["topic"] in RULES:
            if missing_prerequisites(actor, information["topic"]):
                self.sim.trace.record("skill.lesson_deferred", {"agent": actor.id,
                    "skill": information["topic"], "sender": sender,
                    "missing": missing_prerequisites(actor, information["topic"])})
                return
            self.sim.learn_skill(actor, information["topic"], sender)
            return
        from .simulation import Terrain
        position = tuple(information["position"])
        actor.known_tiles[position] = Terrain(information["terrain"])
        topic = information["topic"]
        if topic in {"food", "wood"}:
            actor.known_resources.setdefault("berries" if topic == "food" else "wood", set()).add(position)
        actor.memory.remember(MemoryRecord(f"{sender} supplied {topic} information at {position}",
            source=sender, provenance=Provenance.external(sender),
            metadata={"tick": self.sim.tick, **information}))
        self.sim.trace.record("social.information_received", {"agent": actor.id, "sender": sender,
                                                             **information})

    def do_communicate(self, actor, agent_id, message):
        if isinstance(message, dict) and message.get("kind") == "quote_info":
            if set(message) != {"kind", "topic", "price"}:
                raise ValueError("information quote needs topic and price")
            information = self.information(actor, message["topic"])
            return self.offer(actor, agent_id, "information", want=bundle(message["price"]),
                              information=information)
        return self.sim.communicate(actor, agent_id, message)

    def validate_message(self, actor, payload):
        if not isinstance(payload, dict):
            raise ValueError("structured message must be an object")
        kind = payload.get("kind")
        if kind == "request_info" and set(payload) == {"kind", "topic"} and payload["topic"] in TOPICS:
            return deepcopy(payload)
        if kind == "information" and set(payload) == {"kind", "topic"}:
            return {"kind": kind, "information": self.information(actor, payload["topic"])}
        if kind == "status" and set(payload) == {"kind"}:
            return {"kind": kind, "inventory": {key: actor.inventory[key] for key in ITEMS},
                    "hunger": actor.hunger, "thirst": actor.thirst, "needs_home": actor.home is None}
        if kind == "decline" and set(payload) == {"kind", "topic"} and payload["topic"] in TOPICS:
            return deepcopy(payload)
        if kind == "offer" and set(payload) == {"kind", "offer_id"}:
            snapshot = self.offers.load(payload["offer_id"]) if isinstance(payload["offer_id"], str) else None
            if snapshot and snapshot.value["sender"] == actor.id:
                return deepcopy(payload)
        raise ValueError("unsupported or fabricated structured message")

    def ingest(self, actor, message):
        if message.envelope is None:
            return
        payload = message.envelope.content
        kind = payload["kind"]
        if kind == "information":
            self.learn(actor, payload["information"], message.sender)
        elif kind == "status":
            actor.peer_reports[message.sender] = {"tick": self.sim.tick, **deepcopy(payload)}
        elif kind == "request_info":
            actor.social_requests.append({"sender": message.sender, "tick": self.sim.tick,
                                          "topic": payload["topic"]})

    def do_collaborate(self, actor, agent_id, project):
        site = self._site(actor, project)
        self.contact(actor, agent_id)
        existing = self.project_at(site)
        if existing and (actor.id not in existing["members"] or len(existing["members"]) >= 2):
            raise ValueError("project cannot admit another collaborator")
        if actor.social_project and (not existing or actor.social_project != existing["id"]):
            raise ValueError("finish or leave the current project first")
        return self.offer(actor, agent_id, "collaborate", site=list(site),
                          project_id=existing["id"] if existing else None)

    def _site(self, actor, project):
        from .simulation import Terrain
        if set(project) != {"position"} or not isinstance(project["position"], (list, tuple)):
            raise ValueError("project requires a position")
        position = tuple(project["position"])
        if len(position) != 2 or any(type(value) is not int for value in position):
            raise ValueError("invalid project coordinates")
        if (position not in actor.known_tiles or not self.sim.world.in_bounds(position)
                or self.sim.distance(actor.position, position) > actor.visibility):
            raise ValueError("project site must be observed and nearby")
        tile = self.sim.world.tile(position)
        if tile.terrain != Terrain.GRASS or tile.shelter_owner is not None:
            raise ValueError("project requires empty grass")
        return position

    def do_respond_offer(self, actor, offer_id, accept):
        snapshot = self.offers.load(offer_id)
        if snapshot is None or snapshot.value["recipient"] != actor.id:
            raise ValueError("only the named recipient may respond")
        offer = deepcopy(snapshot.value)
        if offer["status"] != "pending" or self.sim.tick >= offer["expires_tick"]:
            raise ValueError("offer is closed or expired")
        sender = self.contact(actor, offer["sender"])
        if sender.sleep_remaining:
            raise ValueError("sender is sleeping")
        if not accept:
            offer["status"] = "rejected"
            self.offers.save(offer_id, offer, expected_version=snapshot.version)
            self._record("social.offer_rejected", [actor, sender], offer=offer_id)
            return Observation("respond_offer", f"rejected {offer_id}")
        if offer["kind"] == "information" and offer["information"]["topic"] in RULES:
            missing = missing_prerequisites(actor, offer["information"]["topic"])
            if missing:
                raise ValueError("cannot buy a lesson before learning its prerequisites")
        for owner, items in ((sender, offer["give"]), (actor, offer["want"])):
            if any(owner.inventory[item] < amount for item, amount in items.items()):
                raise ValueError("inventory changed; trade cannot execute")
            if owner.behavior and offer["kind"] != "collaborate":
                if not owner.behavior["trading"]["enabled"]:
                    raise ValueError("participant trading disabled")
                received = offer["want"] if owner is sender else offer["give"]
                for group in (tuple(NUTRITION), ("water",)):
                    if any(items.get(item, 0) for item in group):
                        total = sum(owner.inventory[item] - items.get(item, 0) + received.get(item, 0) for item in group)
                        if total < owner.behavior["trading"]["reserve"]:
                            raise ValueError("inventory changed; reserved supplies would be spent")
        site, existing = None, None
        if offer["kind"] == "collaborate":
            site = self._site(actor, {"position": offer["site"]})
            existing = self.project_at(site)
            if (sender.social_project and (not existing or sender.social_project != existing["id"])) or actor.social_project:
                raise ValueError("participant is already committed to a project")
            if (offer["project_id"] is not None and existing is None) or (existing and
                    (existing["id"] != offer["project_id"] or existing["members"] != [sender.id]
                     or sender.social_project != existing["id"])):
                raise ValueError("project site changed")
        # Commit terminal state before infallible Counter updates. No awaits or
        # external I/O intervene: repeat acceptances cannot spend inventories twice.
        offer["status"] = "accepted"
        self.offers.save(offer_id, offer, expected_version=snapshot.version)
        for giver, receiver, items in ((sender, actor, offer["give"]), (actor, sender, offer["want"])):
            for item, amount in items.items():
                giver.inventory[item] -= amount
                receiver.inventory[item] += amount
        for participant in (sender, actor):
            if participant.inventory["leather_garment"] == 0:
                participant.leather_equipped = False
        if offer["kind"] == "information":
            self.learn(actor, offer["information"], sender.id)
        elif site is not None:
            self._start_project(sender, site, [sender, actor], existing=existing)
        self._record("social.offer_executed", [sender, actor], offer=offer_id, kind=offer["kind"],
                     give=offer["give"], want=offer["want"], version=snapshot.version + 1)
        return Observation("respond_offer", f"accepted {offer_id}", {"offer_id": offer_id})

    def do_cancel_offer(self, actor, offer_id):
        snapshot = self.offers.load(offer_id)
        if snapshot is None or snapshot.value["sender"] != actor.id or snapshot.value["status"] != "pending":
            raise ValueError("only the proposer may cancel a pending offer")
        value = {**snapshot.value, "status": "cancelled"}
        self.offers.save(offer_id, value, expected_version=snapshot.version)
        self._record("social.offer_cancelled", [actor], offer=offer_id)
        return Observation("cancel_offer", f"cancelled {offer_id}")

    def project_at(self, site):
        identity = self.active_sites.get(site)
        return deepcopy(self.projects.load(identity).value) if identity else None

    def site_available(self, actor, position):
        tile = self.sim.world.tile(position)
        project = self.project_at(position)
        return (tile.terrain.value == "grass" and tile.shelter_owner is None
                and (project is None or not project["members"] or actor.id in project["members"]))

    def _start_project(self, actor, site, members, existing=None):
        if existing:
            project = deepcopy(existing)
            project.update(members=[member.id for member in members])
            project["last_progress_tick"] = self.sim.tick
            if len(members) > 1:
                project.update(capacity=2, wood_required=14, work_required=6)
            snapshot = self.projects.load(project["id"])
            self.projects.save(project["id"], project, expected_version=snapshot.version)
        else:
            self.serial += 1
            identity = f"project-{self.serial}"
            shared = len(members) == 2
            project = {"id": identity, "site": list(site), "members": [member.id for member in members],
                       "capacity": 2 if shared else 1, "wood_required": 14 if shared else 10,
                       "work_required": 6 if shared else 4, "wood": 0, "work": 0,
                       "contributions": {}, "last_work": {}, "status": "active",
                       "last_progress_tick": self.sim.tick}
            self.projects.save(identity, project, expected_version=0)
            self.project_ids.append(identity)
        self.active_sites[site] = project["id"]
        for member in members:
            member.social_project = project["id"]
            old_plan = self.project_plans.get((project["id"], member.id))
            self.project_plans[(project["id"], member.id)] = Plan("Build shelter together" if len(members) > 1 else "Build personal shelter", [
                PlanStep("reach", "Reach project site"),
                PlanStep("materials", "Gather and contribute materials"),
                PlanStep("work", "Perform construction work", depends_on=["reach"],
                         evidence=list(old_plan.steps[2].evidence) if old_plan else [])],
                version=old_plan.version + 1 if old_plan else 1)
        self._record("project.started", members, project=project["id"], site=site, capacity=project["capacity"])
        return project

    def build(self, actor):
        if not actor.alive or actor.sleep_remaining:
            raise ValueError("builder must be alive and awake")
        project = self.project_at(actor.position)
        if project is None:
            if actor.inventory["wood"] < 10:
                raise ValueError("shelter requires 10 wood")
            project = self._start_project(actor, actor.position, [actor])
        elif actor.id not in project["members"]:
            if any(self.agent(identity).alive for identity in project["members"]):
                raise ValueError("join this construction project with consent first")
            project = self._start_project(actor, actor.position, [actor], existing=project)
        return self.execute(actor, "work_project", project_id=project["id"])

    def do_work_project(self, actor, project_id):
        snapshot = self.projects.load(project_id)
        if snapshot is None:
            raise ValueError("unknown project")
        project = deepcopy(snapshot.value)
        if (project["status"] != "active" or actor.id not in project["members"]
                or actor.position != tuple(project["site"]) or actor.energy < 1.5):
            raise ValueError("construction requires membership, the project site, and energy")
        if project["last_work"].get(actor.id) == self.sim.tick:
            raise ValueError("one construction action per survivor per tick")
        tile = self.sim.world.tile(actor.position)
        if tile.shelter_owner is not None:
            raise ValueError("project site is no longer empty")
        wood = min(actor.inventory["wood"], project["wood_required"] - project["wood"])
        work = 1 if project["work"] < project["work_required"] else 0
        if not wood and not work:
            raise ValueError("project still needs wood")
        project["wood"] += wood
        project["work"] += work
        project["last_work"][actor.id] = self.sim.tick
        project["last_progress_tick"] = self.sim.tick
        own = project["contributions"].setdefault(actor.id, {"wood": 0, "work": 0})
        own["wood"] += wood
        own["work"] += work
        finished = (project["work"] >= project["work_required"]
                    and project["wood"] >= project["wood_required"])
        if finished:
            project["status"] = "completed"
        self.projects.save(project_id, project, expected_version=snapshot.version)
        actor.inventory["wood"] -= wood
        actor.energy -= (1.5 * (1.5 - actor.strength / 10)) if work else 0
        plan = self.project_plans.get((project_id, actor.id))
        if plan:
            plan.steps[0].status = StepStatus.DONE
            plan.steps[1].status = StepStatus.DONE if project["wood"] >= project["wood_required"] else StepStatus.PENDING
            plan.steps[2].evidence.append(f"tick {self.sim.tick}: {wood} wood, {work} work")
            if finished:
                plan.steps[2].status = StepStatus.DONE
        if finished:
            self.active_sites.pop(actor.position, None)
            members = [self.agent(identity) for identity in project["members"] if self.agent(identity).alive]
            tile.shelter_owner = members[0].id if members else actor.id
            tile.shelter_capacity = project["capacity"]
            tile.shelter_residents = {member.id for member in members}
            for member in members:
                member.home = actor.position
                member.social_project = None
                other_plan = self.project_plans.get((project_id, member.id))
                if other_plan:
                    for step in other_plan.steps:
                        step.status = StepStatus.DONE
            self._record("project.completed", members, project=project_id, site=actor.position,
                         capacity=tile.shelter_capacity, contributions=project["contributions"])
            self.sim.event_log.appendleft(f"Tick {self.sim.tick}: shelter completed ({tile.shelter_capacity} beds)")
        self.sim.trace.record("project.contributed", {"agent": actor.id, "project": project_id,
            "wood": wood, "work": work, "total_wood": project["wood"], "total_work": project["work"],
            "version": snapshot.version + 1})
        return Observation("build", "built shelter" if finished else "contributed to shelter project",
                           {"project_id": project_id, "completed": finished, "wood": wood, "work": work})

    def do_leave_project(self, actor, project_id):
        snapshot = self.projects.load(project_id)
        if snapshot is None or actor.id not in snapshot.value["members"] or snapshot.value["status"] != "active":
            raise ValueError("not a member of an active project")
        project = deepcopy(snapshot.value)
        project["members"].remove(actor.id)
        self.projects.save(project_id, project, expected_version=snapshot.version)
        actor.social_project = None
        self._record("project.left", [actor] + [self.agent(member) for member in project["members"]],
                     project=project_id, survivor=actor.id)
        return Observation("leave_project", "left project; previous contributions remain")

    def do_collect_water(self, actor):
        if actor.inventory["flask"] < 1:
            raise ValueError("collecting water requires an empty flask")
        if not any(self.sim.world.tile(pos).terrain.is_water for pos in self.sim.world.positions_within(actor.position, 1)):
            raise ValueError("no water within reach")
        actor.inventory["flask"] -= 1
        actor.inventory["water"] += 1
        return Observation("collect_water", "filled one flask", {"water": actor.inventory["water"]})

    def do_use_water(self, actor):
        if actor.inventory["water"] < 1:
            raise ValueError("no stored water")
        actor.inventory["water"] -= 1
        actor.inventory["flask"] += 1
        actor.thirst = max(0, actor.thirst - 50)
        return Observation("use_water", "drank stored water", {"thirst": actor.thirst})

    def refresh(self):
        for identity in list(self.active_offers):
            snapshot = self.offers.load(identity)
            value = snapshot.value
            if value["status"] != "pending":
                self.active_offers.discard(identity)
                continue
            parties = [self.agent(value[key]) for key in ("sender", "recipient")]
            if self.sim.tick >= value["expires_tick"] or any(not actor.alive for actor in parties):
                updated = {**value, "status": "expired" if all(actor.alive for actor in parties) else "cancelled"}
                self.offers.save(identity, updated, expected_version=snapshot.version)
                self._record("social.offer_closed", parties, offer=identity, status=updated["status"])
                self.active_offers.discard(identity)
        for identity in list(self.active_sites.values()):
            snapshot = self.projects.load(identity)
            value = snapshot.value
            if value["status"] == "active" and any(not self.agent(member).alive for member in value["members"]):
                project = deepcopy(value)
                project["members"] = [member for member in value["members"] if self.agent(member).alive]
                self.projects.save(identity, project, expected_version=snapshot.version)

    def policy_response(self, actor, offer):
        """Individual preferences return framework authorization decisions, not effects."""
        traits = actor.social_traits
        if (offer["kind"] == "information" and offer["information"]["topic"] in RULES
                and missing_prerequisites(actor, offer["information"]["topic"])):
            return AuthorizationDecision.deny("missing prerequisite knowledge")
        preferences = actor.behavior.get("preferences", {})
        trading = actor.behavior.get("trading", {})
        if offer["kind"] in {"trade", "information"} and not trading.get("enabled", True):
            return AuthorizationDecision.deny("trading disabled")
        if traits.caution >= 60 and any(result.record.metadata.get("survivor") == offer["sender"]
                for result in actor.memory.retrieve("project.left", limit=4)):
            return AuthorizationDecision.deny("past partner abandonment makes me cautious")
        if offer["kind"] == "collaborate":
            if preferences.get("shelter") == "solo":
                return AuthorizationDecision.deny("prefers a solo shelter")
            if (traits.cooperation < 35 or actor.social_project or
                    (actor.home is not None and traits.generosity < 80)):
                return AuthorizationDecision.deny("not willing to join this project")
            return AuthorizationDecision.allow("shared construction fits my current priorities")
        for item, quantity in offer["want"].items():
            remaining = actor.inventory[item] - quantity
            if actor.behavior and item in set(NUTRITION) | {"water"}:
                group = ("water",) if item == "water" else tuple(NUTRITION)
                total = sum(actor.inventory[name] - offer["want"].get(name, 0) + offer["give"].get(name, 0) for name in group)
                if total < trading.get("reserve", 2):
                    return AuthorizationDecision.deny("exchange would spend reserved supplies")
            if remaining < 0 or (item == "berries" and remaining < (0 if traits.generosity > 75 else 2)):
                return AuthorizationDecision.deny("I cannot afford this without risking my reserves")
            if item == "water" and remaining < 1 and actor.thirst > 40:
                return AuthorizationDecision.deny("I need my remaining water")
        def utility(items):
            return sum(quantity * ({"water": 2 + actor.thirst / 20,
                "berries": 1 + actor.hunger / 35, "wood": 0.5 if actor.home else 1.5}.get(item, 2))
                for item, quantity in items.items())
        benefit, cost = utility(offer["give"]), utility(offer["want"])
        if offer["kind"] == "information":
            topic = offer["information"]["topic"]
            try:
                self.information(actor, topic)
                benefit = 0
            except ValueError:
                benefit = 5 if topic == "water" else 3
        threshold = 1.3 - traits.cooperation / 200 - traits.generosity / 300
        if benefit >= cost * threshold and (benefit > 0 or traits.generosity >= 75):
            return AuthorizationDecision.allow("exchange is useful at a price I accept")
        return AuthorizationDecision.deny("exchange is not useful enough for me")

    def choose(self, actor, policy):
        """Reference social policy: finite replies, own reserves, no private peer reads."""
        contacts = {other.id: other for other in self.sim.nearby_survivors(actor)
                    if other.id in actor.known_survivors and not other.sleep_remaining}
        for offer in self.pending(actor):
            if offer["recipient"] == actor.id and offer["sender"] in contacts and self.sim.tick < offer["expires_tick"]:
                if (offer["id"] in getattr(policy, "deferred_offers", {}).get(actor.id, set())
                        and offer["expires_tick"] - self.sim.tick > 8):
                    continue  # Keep acting; use local consent before an offer expires if inference is slow.
                decision = self.policy_response(actor, offer)
                self.sim.trace.record("social.policy_decision", {"agent": actor.id, "offer": offer["id"],
                    "allowed": decision.allowed, "reason": decision.reason})
                return ToolCall("respond_offer", {"offer_id": offer["id"], "accept": decision.allowed})
        while actor.social_requests:
            request = actor.social_requests.popleft()
            if request["sender"] not in contacts or self.sim.tick - request["tick"] >= 32:
                continue
            topic = request["topic"]
            try:
                self.information(actor, topic)
                knowledge = actor.behavior.get("preferences", {}).get("knowledge")
                if knowledge == "private" or (knowledge is None and actor.social_traits.generosity < 20 and actor.social_traits.cooperation < 20):
                    message = {"kind": "decline", "topic": topic}
                elif knowledge == "share" or (knowledge is None and actor.social_traits.generosity >= 60):
                    message = {"kind": "information", "topic": topic}
                else:
                    message = {"kind": "quote_info", "topic": topic, "price": {"berries": 1}}
            except ValueError:
                message = {"kind": "decline", "topic": topic}
            return ToolCall("communicate", {"agent_id": request["sender"], "message": message})
        if actor.social_project:
            snapshot = self.projects.load(actor.social_project)
            if snapshot and snapshot.value["status"] == "active":
                project = snapshot.value
                site = tuple(project["site"])
                if self.sim.tick - project["last_progress_tick"] >= 96 + actor.social_traits.cooperation:
                    return ToolCall("leave_project", {"project_id": project["id"]})
                if (len(project["members"]) == 1 and actor.social_traits.cooperation >= 35
                        and self.sim.distance(actor.position, site) <= actor.visibility
                        and self.sim.tick >= actor.social_cooldowns.get("invite", 0)):
                    for identity in contacts:
                        if identity != actor.id and not any(row["recipient"] == identity for row in self.pending(actor)):
                            actor.social_cooldowns["invite"] = self.sim.tick + 32
                            return ToolCall("collaborate", {"agent_id": identity,
                                "project": {"position": list(site)}})
                plan = self.project_plans.get((project["id"], actor.id))
                if plan and actor.position == site:
                    plan.steps[0].status = StepStatus.DONE
                missing = project["wood_required"] - project["wood"]
                if missing > 0 and actor.inventory["wood"] < min(missing, 7):
                    call, _ = policy._collect(actor, "wood", min(missing, 7))
                    return call
                if actor.position != site:
                    return ToolCall("move", {"direction": policy._direction_toward(actor.position, site)})
                if plan:
                    plan.steps[0].status = StepStatus.DONE
                return ToolCall("work_project", {"project_id": project["id"]})
            actor.social_project = None
        if actor.inventory["water"] and actor.thirst >= 35:
            return ToolCall("use_water")
        at_water = any(self.sim.world.tile(pos).terrain.is_water
                       for pos in self.sim.world.positions_within(actor.position, 1))
        if at_water and actor.inventory["flask"]:
            return ToolCall("collect_water")
        if actor.home and actor.inventory["flask"] + actor.inventory["water"] < 2 and actor.inventory["wood"] >= 2:
            return ToolCall("craft", {"item": "flask"})
        if self.sim.tick < actor.social_cooldowns.get("outgoing", 0):
            return None
        for identity in contacts:
            if self.pending(actor) and any(row["recipient"] == identity for row in self.pending(actor)):
                continue
            actor.social_cooldowns["outgoing"] = self.sim.tick + 24
            report = actor.peer_reports.get(identity)
            if report and self.sim.tick - report["tick"] < 64:
                inventory = report["inventory"]
                can_trade = actor.behavior.get("trading", {}).get("enabled", True)
                reserve = actor.behavior.get("trading", {}).get("reserve", 2)
                if can_trade and actor.thirst >= 35 and not actor.inventory["water"] and actor.inventory["berries"] >= reserve + 2 and inventory.get("water", 0):
                    return ToolCall("trade", {"agent_id": identity, "offer": {"berries": 2}, "request": {"water": 1}})
                if can_trade and actor.hunger >= 35 and actor.inventory["berries"] < 2 and actor.inventory["water"] > reserve and inventory.get("berries", 0) >= 2:
                    return ToolCall("trade", {"agent_id": identity, "offer": {"water": 1}, "request": {"berries": 2}})
                if (actor.home is None and actor.behavior.get("preferences", {}).get("shelter") != "solo" and actor.social_traits.cooperation >= (20 if actor.behavior.get("preferences", {}).get("shelter") == "shared" else 35) and report["needs_home"]
                        and self.sim.world.tile(actor.position).terrain.value == "grass"
                        and self.sim.world.tile(actor.position).shelter_owner is None
                        and not self.project_at(actor.position)):
                    return ToolCall("collaborate", {"agent_id": identity, "project": {"position": list(actor.position)}})
            for topic in ("water", "food", "wood", "fishing", "axe", "spear", "bow", "hunting",
                          "gardening", "fire", "cooking", "skinning", "leatherworking", "reservoir"):
                if topic in RULES and missing_prerequisites(actor, topic):
                    continue
                if topic in {"gardening", "fire", "cooking", "reservoir"} and not self.sim.home_is_valid(actor):
                    continue
                if topic in {"skinning", "leatherworking"} and not actor.hunting_kills:
                    continue
                if topic in {"bow", "hunting"} and not actor.seen_wildlife:
                    continue
                if topic == "fishing" and not any(terrain.value == "lake" for terrain in actor.known_tiles.values()):
                    continue
                if topic == "axe" and not (actor.wood_gathered >= 8 and actor.home is None):
                    continue
                if topic == "spear" and self.sim.tick - actor.last_threat_tick > 120:
                    continue
                try:
                    self.information(actor, topic)
                except ValueError:
                    key = f"question:{identity}:{topic}"
                    if self.sim.tick >= actor.social_cooldowns.get(key, 0):
                        actor.social_cooldowns[key] = self.sim.tick + 100
                        return ToolCall("communicate", {"agent_id": identity,
                            "message": {"kind": "request_info", "topic": topic}})
            if actor.social_traits.caution > 75 and actor.social_traits.cooperation < 30:
                return None
            return ToolCall("communicate", {"agent_id": identity, "message": {"kind": "status"}})
        return None

    def call_available(self, actor, call):
        try:
            validate_social({"tool": call.tool_name, "arguments": dict(call.arguments)})
            args = call.arguments
            if "agent_id" in args:
                self.contact(actor, args["agent_id"])
            if "offer_id" in args:
                snapshot = self.offers.load(args["offer_id"])
                if not snapshot or snapshot.value["status"] != "pending" or self.sim.tick >= snapshot.value["expires_tick"]:
                    return False
                party = "recipient" if call.tool_name == "respond_offer" else "sender"
                if snapshot.value[party] != actor.id:
                    return False
            if "project_id" in args:
                snapshot = self.projects.load(args["project_id"])
                if not snapshot or snapshot.value["status"] != "active" or actor.id not in snapshot.value["members"]:
                    return False
                if call.tool_name == "work_project" and actor.position != tuple(snapshot.value["site"]):
                    return False
            return actor.alive and not actor.sleep_remaining
        except (ValueError, TypeError):
            return False

    def context(self, actor):
        project = self.projects.load(actor.social_project) if actor.social_project else None
        return {"traits": asdict(actor.social_traits), "offers": self.pending(actor),
                "project": deepcopy(project.value) if project else None,
                "peer_reports": list(actor.peer_reports.items())[-4:]}
