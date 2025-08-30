# Scarecrovvs Realm Simulator v4 (Fixed)
# - Windows/macOS friendly (relative paths)
# - 2 workers/player
# - Worker occupancy caps (ash/shards/initiative = 1; plasma/forage/rookery/compost = 2;
#   Forage unlimited when Plentiful Forage active)
# - Initiative field (cap 1): sets start player next round + discards a random Pool card (refill)
# - Mat slots (1..6): slot1 VP +2 bonus on VP play; slot2 "chosen-type" discount; slot3 compost on placement;
#   slot4/5/6 -1 discount for Critter/Farm/Wild. (Discount cap: total -1 per play.)
# - Globals loaded from globals.csv with buy/play costs, cannot be placed on mat, instant effects.
# - Greedy bot + optional MCTS lookahead; NO logging during rollouts (only real game moves logged).
# - Logs to ./logs (JSONL). You can add a summary CSV later if desired.

from __future__ import annotations
import os, csv, json, random, copy, argparse
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

# ---------------- Constants ----------------
RES   = ["plasma","ash","shards","nut","berry","mushroom"]
FIELDS = ["plasma","ash","shards","forage","rookery","compost","initiative"]

# ---------------- Data classes ----------------
@dataclass
class Card:
    id: str
    name: str
    buy_cost_plasma: int = 2
    play_cost: Dict[str,int] = field(default_factory=dict)
    type_: str = "None"     # Farm/Critter/Wild/Global/None
    domain: str = "None"    # Radioactive/Slime/Magic/None
    mat_points: int = 0
    can_play_on_mat: bool = True
    effect: str = ""        # "draw:2", "draw2_discard1", "hand_size_delta_next_round:+1", etc.

    @staticmethod
    def from_row(row: Dict[str,str]) -> "Card":
        def as_int(x, d=0):
            try: return int(x)
            except: return d
        def as_bool(x):
            s=str(x).strip().lower()
            return s in ("1","true","yes","y")
        pc={}
        for k in RES:
            v = as_int(row.get(f"play_cost_{k}",0))
            if v: pc[k]=v
        return Card(
            id=row["id"].strip(),
            name=row["name"].strip(),
            buy_cost_plasma=as_int(row.get("buy_cost_plasma",2)),
            play_cost=pc,
            type_=row.get("type","None").strip() or "None",
            domain=row.get("domain","None").strip() or "None",
            mat_points=as_int(row.get("mat_points",0)),
            can_play_on_mat=as_bool(row.get("can_play_on_mat","true")),
            effect=row.get("effect","").strip()
        )

@dataclass
class MatSlot:
    cid: str
    placed_turn: int
    slot_index: int  # 1..6

@dataclass
class Player:
    id: int
    deck: List[str]
    hand: List[str]
    discard: List[str]
    mat: List[MatSlot] = field(default_factory=list)
    workers_available: int = 2          # ← 2 workers per player (v4)
    vp: int = 0
    res: Dict[str,int] = field(default_factory=lambda: {k:0 for k in RES})
    owned: Dict[str,int] = field(default_factory=dict)
    first_play_turn: Dict[str,int] = field(default_factory=dict)

@dataclass
class Config:
    seed: int = 123
    players: int = 3
    victory_vp: int = 24
    hand_size: int = 5
    copies_per_unique: int = 2
    cards_csv: str = "cards.csv"        # relative path
    globals_csv: str = "globals.csv"    # relative path
    # bot knobs
    mcts: int = 0
    rollouts: int = 6
    horizon: int = 3

@dataclass
class RoundMods:
    hand_delta_next_round: int = 0
    forage_bonus_this_round: int = 0
    blight_this_round: bool = False
    decree_claimed: bool = False
    domains_played_this_round: List[set] = field(default_factory=list)
    start_player: int = 0               # who will start NEXT round

@dataclass
class Game:
    cfg: Config
    rng: random.Random
    cards: Dict[str,Card]
    supply: List[str]
    pool: List[str]
    players: List[Player]
    turn: int = 0
    current: int = 0
    ash_pile: int = 1
    shards_pile: int = 1
    occupancy: Dict[str,int] = field(default_factory=lambda: {f:0 for f in FIELDS})
    round_mods: RoundMods = field(default_factory=RoundMods)
    log: List[Dict[str,Any]] = field(default_factory=list)
    winner: Optional[int] = None
    record_logs: bool = True            # ← turn off during rollouts

    def emit(self, rec: Dict[str,Any]):
        if self.record_logs:
            self.log.append(rec)

# ---------------- Loaders ----------------
def load_cards(path: str) -> Dict[str,Card]:
    lib={}
    with open(path, newline="", encoding="utf-8") as f:
        r=csv.DictReader(f)
        for row in r:
            if not row.get("id"): continue
            c=Card.from_row(row)
            lib[c.id]=c
    return lib

def load_globals(path: str) -> Dict[str,Card]:
    if not os.path.exists(path): return {}
    lib={}
    with open(path, newline="", encoding="utf-8") as f:
        r=csv.DictReader(f)
        for row in r:
            if not row.get("id"): continue
            c=Card.from_row(row)
            c.type_="Global"
            c.can_play_on_mat=False
            lib[c.id]=c
    return lib

# ---------------- Setup ----------------
def setup(cfg: Config) -> Game:
    rng = random.Random(cfg.seed)
    lib = load_cards(cfg.cards_csv)
    lib.update(load_globals(cfg.globals_csv))
    supply = [cid for cid in lib.keys() for _ in range(cfg.copies_per_unique)]
    rng.shuffle(supply)
    pool = [supply.pop() for _ in range(min(10,len(supply)))]
    players=[]
    for pid in range(cfg.players):
        deck=["RES:plasma"]*6 + ["VP:1"]*4
        rng.shuffle(deck)
        players.append(Player(id=pid, deck=deck, hand=[], discard=[]))
    g = Game(cfg=cfg, rng=rng, cards=lib, supply=supply, pool=pool, players=players)
    g.round_mods.domains_played_this_round=[set() for _ in range(cfg.players)]
    for p in g.players: draw_to_hand_size(g, p, cfg.hand_size)
    for p in g.players: p.res["plasma"]+=1
    return g

# ---------------- Core helpers ----------------
def draw(g:Game, p:Player, n:int):
    for _ in range(n):
        if not p.deck:
            p.deck = p.discard
            g.rng.shuffle(p.deck)
            p.discard=[]
        if not p.deck: return
        p.hand.append(p.deck.pop())

def draw_to_hand_size(g:Game, p:Player, target:int):
    need=max(0, target-len(p.hand))
    if need>0: draw(g,p,need)

# ----- Mat discounts -----
def slot2_type(g:Game, p:Player) -> Optional[str]:
    for ms in p.mat:
        if ms.slot_index==2:
            cid=ms.cid
            return g.cards[cid].type_
    return None

def total_discount_for_card(g:Game, p:Player, c:Card) -> int:
    disc = 0
    # slot2 chosen-type discount
    s2 = slot2_type(g,p)
    if s2 and c.type_ == s2:
        disc = 1
    # slots 4/5/6: type-specific discounts
    s4 = any(ms.slot_index==4 for ms in p.mat)
    s5 = any(ms.slot_index==5 for ms in p.mat)
    s6 = any(ms.slot_index==6 for ms in p.mat)
    if (s4 and c.type_=="Critter") or (s5 and c.type_=="Farm") or (s6 and c.type_=="Wild"):
        disc = 1
    # cap total to -1 (one resource)
    return min(disc, 1)

def discounted_cost(c:Card, disc:int) -> Dict[str,int]:
    if disc<=0: return c.play_cost.copy()
    cost = c.play_cost.copy()
    for k in ["plasma","ash","shards","nut","berry","mushroom"]:
        if cost.get(k,0)>0:
            cost[k]-=1
            if cost[k]==0: del cost[k]
            break
    return cost

def can_pay_res(p:Player, cost:Dict[str,int]) -> bool:
    return all(p.res.get(k,0)>=v for k,v in cost.items())

def pay_res(p:Player, cost:Dict[str,int]) -> None:
    for k,v in cost.items():
        p.res[k]-=v

# ---------------- Actions ----------------
def act_buy_pool(g:Game, pid:int, pool_idx:int) -> bool:
    p = g.players[pid]
    if not (0<=pool_idx<len(g.pool)): return False
    cid = g.pool[pool_idx]; c=g.cards[cid]
    if p.res["plasma"] < c.buy_cost_plasma: return False
    p.res["plasma"] -= c.buy_cost_plasma
    p.discard.append(cid)
    p.owned[cid]=p.owned.get(cid,0)+1
    g.emit({"t":g.turn,"a":"buy","p":pid,"cid":cid,"name":c.name})
    if g.supply:
        g.pool[pool_idx]=g.supply.pop()
    else:
        g.pool.pop(pool_idx)
    return True

def act_play(g:Game, pid:int, hand_idx:int, to_mat:bool=False, slot_idx:int=0) -> bool:
    p=g.players[pid]
    if not (0<=hand_idx<len(p.hand)): return False
    tok=p.hand[hand_idx]

    # Resource
    if tok.startswith("RES:"):
        r=tok.split(":")[1]
        p.res[r]+=1
        p.discard.append(tok)
        del p.hand[hand_idx]
        g.emit({"t":g.turn,"a":"play_res","p":pid,"res":r})
        return True

    # VP
    if tok.startswith("VP:"):
        vp=int(tok.split(":")[1])
        bonus = 2 if any(ms.slot_index==1 for ms in p.mat) else 0
        p.vp += vp + bonus
        p.discard.append(tok)
        del p.hand[hand_idx]
        g.emit({"t":g.turn,"a":"play_vp","p":pid,"vp":vp,"bonus":bonus,"total":p.vp})
        return True

    # Library / Global
    if tok not in g.cards: return False
    c=g.cards[tok]

    # Globals: immediate, no mat
    if c.type_=="Global":
        cost = c.play_cost.copy()
        if not can_pay_res(p, cost): return False
        pay_res(p, cost)
        del p.hand[hand_idx]
        p.discard.append(c.id)
        g.emit({"t":g.turn,"a":"play_global","p":pid,"cid":c.id,"name":c.name,"effect":c.effect,"paid":cost})
        apply_global(g, pid, c)
        return True

    # Regular card cost (apply mat discounts, capped to -1)
    disc = total_discount_for_card(g,p,c)
    cost = discounted_cost(c, disc)
    if not can_pay_res(p, cost): return False
    pay_res(p, cost)

    # Place to mat or play active
    placed=False
    if to_mat and c.can_play_on_mat and len(p.mat)<6 and 1<=slot_idx<=6 and all(ms.slot_index!=slot_idx for ms in p.mat):
        p.mat.append(MatSlot(cid=c.id, placed_turn=g.turn, slot_index=slot_idx))
        placed=True
        # Slot 3 compost on placement
        if slot_idx==3 and p.hand:
            # remove first non-VP if possible, else first
            idx=None
            for i,t in enumerate(p.hand):
                if not t.startswith("VP:"): idx=i; break
            if idx is None: idx=0
            removed=p.hand.pop(idx)
            g.emit({"t":g.turn,"a":"slot3_compost","p":pid,"card":removed})
    else:
        p.discard.append(c.id)

    del p.hand[hand_idx]
    if c.id not in p.first_play_turn:
        p.first_play_turn[c.id]=g.turn
    g.emit({"t":g.turn,"a":"play_card","p":pid,"cid":c.id,"name":c.name,"type":c.type_,"domain":c.domain,"to_mat":placed,"slot":(slot_idx if placed else 0),"paid":cost})

    apply_effect(g,pid,c)

    # Crown's Decree (domains tracker) — reward handled when threshold reached
    if c.domain and c.domain!="None":
        g.round_mods.domains_played_this_round[pid].add(c.domain)
        if (not g.round_mods.decree_claimed) and len(g.round_mods.domains_played_this_round[pid])>=3:
            p.vp += 2
            g.round_mods.decree_claimed=True
            g.emit({"t":g.turn,"a":"decree_vp","p":pid,"vp":2,"total":p.vp})
    return True

def field_capacity(g:Game, field:str) -> int:
    # Accumulating fields cap=1; Initiative cap=1; others cap=2; Forage unlimited when Plentiful Forage active
    if field=="forage" and g.round_mods.forage_bonus_this_round>0:
        return 999
    if field in ("ash","shards","initiative"):
        return 1
    if field in ("plasma","forage","rookery","compost"):
        return 2
    return 1

def place_worker(g:Game, pid:int, field:str) -> bool:
    p=g.players[pid]
    if p.workers_available<=0 or field not in FIELDS: return False
    cap=field_capacity(g, field)
    if g.occupancy[field] >= cap:
        return False
    # resolve effect
    if field=="plasma":
        p.res["plasma"]+=1
    elif field=="ash":
        p.res["ash"]+=g.ash_pile; g.ash_pile=1
    elif field=="shards":
        p.res["shards"]+=g.shards_pile; g.shards_pile=1
    elif field=="forage":
        bonus=g.round_mods.forage_bonus_this_round
        for _ in range(1+bonus):
            p.res[random.choice(["nut","berry","mushroom"])] += 1
    elif field=="rookery":
        # simple: take a random pool card for free
        if g.pool:
            idx=g.rng.randrange(len(g.pool))
            cid=g.pool.pop(idx)
            p.discard.append(cid)
            p.owned[cid]=p.owned.get(cid,0)+1
            if g.supply:
                g.pool.append(g.supply.pop())
    elif field=="compost":
        if p.hand:
            removed=p.hand.pop(0)
            g.emit({"t":g.turn,"a":"compost","p":pid,"card":removed})
    elif field=="initiative":
        # Start player next round + discard a random pool card immediately
        g.round_mods.start_player = pid
        if g.pool:
            idx=g.rng.randrange(len(g.pool))
            removed=g.pool.pop(idx)
            g.emit({"t":g.turn,"a":"initiative_discard","p":pid,"card":removed})
            if g.supply:
                g.pool.append(g.supply.pop())

    g.occupancy[field]+=1
    p.workers_available-=1
    g.emit({"t":g.turn,"a":"place_worker","p":pid,"field":field,"occ":g.occupancy[field],"cap":cap})
    return True

# ---------------- Effects ----------------
def apply_effect(g:Game, pid:int, c:Card):
    p=g.players[pid]
    if c.effect.startswith("draw:"):
        n=int(c.effect.split(":")[1])
        for _ in range(n): draw(g,p,1)
        g.emit({"t":g.turn,"a":"effect","p":pid,"cid":c.id,"e":f"draw:{n}"})
    elif c.effect=="draw2_discard1":
        draw(g,p,2)
        if p.hand:
            dumped=p.hand.pop()
            p.discard.append(dumped)
        g.emit({"t":g.turn,"a":"effect","p":pid,"cid":c.id,"e":"draw2_discard1"})
    # (extend as needed)

def apply_global(g:Game, pid:int, c:Card):
    eff=c.effect
    if eff=="hand_size_delta_next_round:-1":
        g.round_mods.hand_delta_next_round -= 1
        g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"drought_next_round_-1"})
    elif eff=="hand_size_delta_next_round:+1":
        g.round_mods.hand_delta_next_round += 1
        g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"flood_next_round_+1"})
    elif eff=="end_round_all_compost:1":
        g.round_mods.blight_this_round = True
        g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"blight_end_round_compost"})
    elif eff=="forage_yield_bonus_this_round:+1":
        g.round_mods.forage_bonus_this_round += 1
        g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"plentiful_forage_this_round"})
    elif eff=="first_to_play_three_domains:+2vp":
        # handled via tracker inside act_play
        g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"crowns_decree_active"})
    else:
        # unknown global effect tag
        g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":eff})

# ---------------- Bot ----------------
def engine_strength(p:Player) -> float:
    # crude proxy for pacing VP vs engine
    return p.res["plasma"] + 0.5*(p.res["ash"]+p.res["shards"]) + 0.3*len(p.hand) + 0.8*len(p.mat)

def card_score_for_pool(g:Game, p:Player, c:Card) -> float:
    score=0.0
    if p.res["plasma"]>=c.buy_cost_plasma: score+=1.0
    # simple synergy: match types/domains on mat
    types_on_mat={ g.cards[ms.cid].type_ for ms in p.mat }
    domains_on_mat={ g.cards[ms.cid].domain for ms in p.mat }
    if c.type_ in types_on_mat: score+=1.5
    if c.domain in domains_on_mat: score+=1.5
    if "draw" in c.effect: score+=1.0
    if "on_mat" in c.effect or c.mat_points>0: score+=1.2
    if c.type_=="Global": score+=0.6
    return score

def choose_slot_for(p:Player, c:Card) -> int:
    # prefer type-discount slots
    if c.type_=="Critter": pref=4
    elif c.type_=="Farm": pref=5
    elif c.type_=="Wild": pref=6
    else: pref=1
    occ={ms.slot_index for ms in p.mat}
    if pref not in occ: return pref
    for s in [1,2,3,4,5,6]:
        if s not in occ: return s
    return 0

def legal_actions(g:Game, pid:int) -> List[Tuple[str,Any]]:
    p=g.players[pid]
    acts=[("pass",None)]
    # plays
    for i,tok in enumerate(p.hand):
        if tok.startswith("RES:") or tok.startswith("VP:"):
            acts.append(("play",(i,False,0)))
        elif tok in g.cards:
            c=g.cards[tok]
            if c.type_=="Global":
                if can_pay_res(p, c.play_cost): acts.append(("play",(i,False,0)))
            else:
                disc=total_discount_for_card(g,p,c)
                cost=discounted_cost(c,disc)
                if can_pay_res(p,cost):
                    acts.append(("play",(i,False,0)))
                    if c.can_play_on_mat and len(p.mat)<6:
                        s=choose_slot_for(p,c)
                        if s>0: acts.append(("play",(i,True,s)))
    # buys
    for idx,cid in enumerate(g.pool):
        if g.cards[cid].buy_cost_plasma <= p.res["plasma"]:
            acts.append(("buy_pool",idx))
    # place worker (only if capacity remains)
    if p.workers_available>0:
        for f in ["ash","shards","plasma","forage","rookery","compost","initiative"]:
            if g.occupancy[f] < field_capacity(g,f):
                acts.append(("place_worker",f))
    return acts

def greedy_choose(g:Game, pid:int) -> Tuple[str,Any]:
    acts=legal_actions(g,pid)
    best=None; bestv=-1e9
    for a,arg in acts:
        v=-1.0
        if a=="play":
            i,to_mat,s=arg
            tok=g.players[pid].hand[i]
            if tok.startswith("VP:"):
                vp=int(tok.split(":")[1])
                v = 2.5*vp if engine_strength(g.players[pid])>=vp else 0.1*vp
            elif tok.startswith("RES:"):
                v = 0.8
            else:
                c=g.cards[tok]; v=1.0
                if c.type_=="Global": v+=0.6
                if to_mat: v += 0.8 if (c.mat_points>0 or "on_mat" in c.effect) else 0.1
        elif a=="buy_pool":
            cid=g.pool[arg]; c=g.cards[cid]
            v = card_score_for_pool(g,g.players[pid],c)
        elif a=="place_worker":
            f=arg
            base={"ash":2.0,"shards":2.0,"plasma":1.0,"forage":1.0,"rookery":1.2,"compost":0.6,"initiative":1.4}.get(f,0.5)
            v = base if g.occupancy[f] < field_capacity(g,f) else -1.0
        elif a=="pass":
            v=0.0
        if v>bestv:
            best=(a,arg); bestv=v
    return best if best else ("pass",None)

# -------------- MCTS-lite (no logging in rollouts) --------------
def clone_for_rollout(g:Game) -> Game:
    gg: Game = copy.deepcopy(g)
    gg.record_logs = False
    return gg

def apply_action(g:Game, pid:int, a:Tuple[str,Any]):
    kind,arg=a
    if kind=="play":
        i,to_mat,s=arg
        act_play(g,pid,i,to_mat,s)
    elif kind=="buy_pool":
        act_buy_pool(g,pid,arg)
    elif kind=="place_worker":
        place_worker(g,pid,arg)
    # pass: do nothing

def candidate_actions(g:Game, pid:int) -> List[Tuple[str,Any]]:
    # take a subset to keep rollouts light
    acts=legal_actions(g,pid)
    return acts[:8] if len(acts)>8 else acts

def rollout(g:Game, pid:int, horizon:int) -> float:
    steps=0
    while steps<horizon and not is_terminal(g):
        a = greedy_choose(g, g.current)
        apply_action(g, g.current, a)
        g.current=(g.current+1)%len(g.players)
        if g.current==0:
            end_of_round(g)
            start_of_round(g)
        steps+=1
    p=g.players[pid]
    return p.vp + 0.2*engine_strength(p)

def mcts_choose(g:Game, pid:int, rollouts:int, horizon:int) -> Tuple[str,Any]:
    best=None; bestv=-1e9
    for a in candidate_actions(g,pid):
        acc=0.0
        for _ in range(rollouts):
            gg=clone_for_rollout(g)
            apply_action(gg, pid, a)
            val=rollout(gg, pid, horizon)
            acc+=val
        avg=acc/rollouts
        if avg>bestv: best=a; bestv=avg
    return best if best else ("pass",None)

# ---------------- Round transitions ----------------
def end_of_round(g:Game):
    # Blight: compost 1 for everyone (non-VP preferred)
    if g.round_mods.blight_this_round:
        for p in g.players:
            idx=None
            for i,t in enumerate(p.hand):
                if not t.startswith("VP:"):
                    idx=i; break
            if idx is None and p.hand: idx=0
            if idx is not None:
                removed=p.hand.pop(idx)
                g.emit({"t":g.turn,"a":"blight_compost","p":p.id,"card":removed})
    # reset per-round trackers
    g.round_mods.blight_this_round=False
    g.round_mods.forage_bonus_this_round=0
    g.round_mods.decree_claimed=False
    g.round_mods.domains_played_this_round=[set() for _ in g.players]
    # reset field occupancy
    g.occupancy = {f:0 for f in FIELDS}

def start_of_round(g:Game):
    # grow accumulators
    g.ash_pile += 1; g.shards_pile += 1
    # start player for new round
    g.current = g.round_mods.start_player
    g.emit({"t":g.turn,"a":"initiative_start_player","p":g.current})
    # reset workers
    for p in g.players:
        p.workers_available = 2
    # hand top-up (with next-round delta) then reset delta
    target = g.cfg.hand_size + g.round_mods.hand_delta_next_round
    for p in g.players:
        draw_to_hand_size(g, p, max(0, target))
    g.round_mods.hand_delta_next_round = 0
    # income drip
    for p in g.players:
        p.res["plasma"] += 1

# ---------------- Loop ----------------
def is_terminal(g:Game) -> bool:
    if g.turn>200: return True
    for p in g.players:
        if p.vp >= g.cfg.victory_vp:
            return True
    return False

def winner_id(g:Game) -> Optional[int]:
    for p in g.players:
        if p.vp >= g.cfg.victory_vp:
            return p.id
    return None

def step_turn(g:Game):
    pid=g.current
    # two micro-actions
    for _ in range(2):
        a = mcts_choose(g, pid, g.cfg.rollouts, g.cfg.horizon) if g.cfg.mcts else greedy_choose(g, pid)
        if a[0]=="pass": break
        apply_action(g, pid, a)
    # next player
    g.current=(g.current+1)%len(g.players)
    if g.current==0:
        g.turn+=1
        end_of_round(g)
        start_of_round(g)

def play_one(cfg:Config) -> Dict[str,Any]:
    g=setup(cfg)
    while not is_terminal(g):
        step_turn(g)
    g.winner = winner_id(g)
    # mat durations at end (optional; helpful for analysis tooling)
    for p in g.players:
        for ms in p.mat:
            duration = (g.turn - ms.placed_turn) + 1
            g.emit({"t":g.turn,"a":"mat_duration","p":p.id,"cid":ms.cid,"slot":ms.slot_index,"duration":duration})
    g.emit({"t":g.turn,"a":"end","winner":g.winner})
    return {"winner":g.winner,"turn":g.turn,"log":g.log}

def run_many(games:int=20, seed:int=42, **kwargs) -> Dict[str,Any]:
    rng=random.Random(seed)
    outs=[]
    for _ in range(games):
        cfg=Config(seed=rng.randrange(1_000_000), **kwargs)
        outs.append(play_one(cfg))
    # write logs
    os.makedirs("logs", exist_ok=True)
    log_files=[]
    for i,out in enumerate(outs):
        path=f"logs/game_v4_{seed}_{i}.jsonl"
        with open(path,"w",encoding="utf-8") as f:
            for e in out["log"]:
                f.write(json.dumps(e)+"\n")
        log_files.append(os.path.basename(path))
    # winner counts
    winners={}
    for o in outs:
        winners[str(o["winner"])]=winners.get(str(o["winner"]),0)+1
    # median turns (lazy)
    turns=[o["turn"] for o in outs]
    med = sorted(turns)[len(turns)//2] if turns else None
    return {"games":games,"median_turns":med,"winner_counts":winners,"logs":log_files}

# ---------------- CLI ----------------
if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--seed", type=int, default=123)
    ap.add_argument("--mcts", type=int, default=1)
    ap.add_argument("--rollouts", type=int, default=6)
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--cards", type=str, default="cards.csv")
    ap.add_argument("--globals", type=str, default="globals.csv")
    args=ap.parse_args()

    cfg = Config(
        seed=args.seed, mcts=args.mcts, rollouts=args.rollouts, horizon=args.horizon,
        cards_csv=args.cards, globals_csv=args.globals
    )
    out = run_many(args.games, args.seed, mcts=args.mcts, rollouts=args.rollouts, horizon=args.horizon)
    print(json.dumps(out, indent=2))
