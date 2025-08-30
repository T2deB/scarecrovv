
from __future__ import annotations
import os, csv, json, random, copy, argparse
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from statistics import median

RES = ["plasma","ash","shards","nut","berry","mushroom"]
FIELDS = ["plasma","ash","shards","forage","rookery","compost","initiative"]

# ---------------- Data ----------------
@dataclass
class Card:
    id: str
    name: str
    buy_cost_plasma: int = 2
    play_cost: Dict[str,int] = field(default_factory=dict)
    type_: str = "None"   # Farm/Critter/Wild/Global/None
    domain: str = "None"  # Radioactive/Slime/Magic/None
    mat_points: int = 0
    can_play_on_mat: bool = True
    effect: str = ""      # keywords for engine

    @staticmethod
    def from_row(row: Dict[str,str]) -> "Card":
        def as_int(x, d=0):
            try: return int(x)
            except: return d
        def as_bool(x):
            s=str(x).strip().lower()
            return s in ("1","true","yes","y")
        pc = {}
        for k in ["plasma","ash","shards","nut","berry","mushroom"]:
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
    workers_available: int = 2  # v4: two workers per player
    vp: int = 0
    res: Dict[str,int] = field(default_factory=lambda:{k:0 for k in RES})
    owned: Dict[str,int] = field(default_factory=dict)
    # telemetry
    first_play_turn: Dict[str,int] = field(default_factory=dict)

@dataclass
class Config:
    seed: int = 123
    players: int = 3
    victory_vp: int = 24
    hand_size: int = 5
    copies_per_unique: int = 2
    cards_csv: str = "cards.csv"
    globals_csv: str = "globals.csv"
    # bot knobs
    mcts: int = 0
    rollouts: int = 6
    horizon: int = 3

@dataclass
class RoundMods:
    hand_delta_next_round: int = 0     # Drought/Flood
    forage_bonus_this_round: int = 0   # Plentiful Forage
    blight_this_round: bool = False    # Blight
    decree_claimed: bool = False       # Crown's Decree
    domains_played_this_round: List[set] = field(default_factory=list)

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
    # map accumulators
    ash_pile: int = 1
    shards_pile: int = 1
    # occupancy (per round)
    occupancy: Dict[str,int] = field(default_factory=lambda: {f:0 for f in FIELDS})
    # globals
    round_mods: RoundMods = field(default_factory=RoundMods)
    # initiative
    next_round_starter: Optional[int] = None
    # logs
    log: List[Dict[str,Any]] = field(default_factory=list)
    winner: Optional[int] = None

    def emit(self, rec: Dict[str,Any]): self.log.append(rec)

# -------------- Loaders --------------
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
    """globals.csv columns: id,name,effect,buy_cost_plasma,play_cost_plasma,play_cost_ash,play_cost_shards,play_cost_nut,play_cost_berry,play_cost_mushroom"""
    if not os.path.exists(path): return {}
    lib={}
    with open(path, newline="", encoding="utf-8") as f:
        r=csv.DictReader(f)
        for row in r:
            if not row.get("id"): continue
            c=Card.from_row(row)
            c.type_="Global"; c.can_play_on_mat=False
            lib[c.id]=c
    return lib

# -------------- Setup --------------
def setup(cfg: Config) -> Game:
    rng = random.Random(cfg.seed)
    lib = load_cards(cfg.cards_csv)
    glib = load_globals(cfg.globals_csv)
    lib.update(glib)
    supply=[]
    for cid in lib.keys():
        supply += [cid]*cfg.copies_per_unique
    rng.shuffle(supply)
    pool=[supply.pop() for _ in range(min(10,len(supply)))]
    players=[]
    for pid in range(cfg.players):
        deck=["RES:plasma"]*6 + ["VP:1"]*4
        rng.shuffle(deck)
        p=Player(id=pid, deck=deck, hand=[], discard=[])
        players.append(p)
    g = Game(cfg=cfg, rng=rng, cards=lib, supply=supply, pool=pool, players=players)
    g.round_mods.domains_played_this_round=[set() for _ in range(cfg.players)]
    # opening draw
    for p in g.players: draw_to_hand_size(g, p, cfg.hand_size)
    # round start drip
    for p in g.players: p.res["plasma"]+=1
    return g

# -------------- Mechanics --------------
def draw(g:Game,p:Player,n:int):
    for _ in range(n):
        if not p.deck:
            p.deck = p.discard
            g.rng.shuffle(p.deck)
            p.discard=[]
        if not p.deck: return
        p.hand.append(p.deck.pop())

def draw_to_hand_size(g:Game,p:Player, target:int):
    need = max(0, target - len(p.hand))
    if need>0: draw(g,p,need)

def apply_discount(cost: Dict[str,int], d:int) -> Dict[str,int]:
    if d<=0: return cost
    new=cost.copy()
    for k in ["plasma","ash","shards","nut","berry","mushroom"]:
        if new.get(k,0)>0:
            new[k]-=1
            if new[k]==0: del new[k]
            break
    return new

def can_pay(p:Player, cost: Dict[str,int]) -> bool:
    return all(p.res.get(k,0) >= v for k,v in cost.items())

def pay(p:Player, cost: Dict[str,int]) -> None:
    for k,v in cost.items():
        p.res[k]-=v

# ----- Mat slot bonuses (v3 simplified) -----
def slot_discount_for(p:Player, card:Card) -> int:
    # slot 1: VP bonus handled when playing VP
    # slot 2: type discount for the type of the card already in slot 2 (approximation: your chosen plan matches future plays)
    disc = 0
    slots = {ms.slot_index: ms for ms in p.mat}
    if 2 in slots:
        # Approximation: slot-2 gives -1 to cards of same type as the card sitting in slot 2
        s2_cid = slots[2].cid
        s2_type = None
        # We'll fetch the actual type via a small inline lookup later if needed; here we assume if types match => discount
        # For now, apply discount when types match
        # In practice the greedy bot picks slot 2 with a card of the desired type
        pass
    if (4 in slots and card.type_=="Critter") or \
       (5 in slots and card.type_=="Farm") or \
       (6 in slots and card.type_=="Wild"):
        disc = 1
    return disc

# ----- Action: play -----
def act_play(g:Game, pid:int, hand_idx:int, to_mat:bool=False, slot_idx:int=0) -> bool:
    p=g.players[pid]
    if not (0<=hand_idx<len(p.hand)): return False
    tok=p.hand[hand_idx]
    # Resource
    if tok.startswith("RES:"):
        r=tok.split(":")[1]
        p.res[r]+=1; p.discard.append(tok); del p.hand[hand_idx]
        g.emit({"t":g.turn,"a":"play_res","p":pid,"res":r}); return True
    # VP
    if tok.startswith("VP:"):
        vp=int(tok.split(":")[1])
        bonus = 2 if any(ms.slot_index==1 for ms in p.mat) else 0
        p.vp += vp + bonus
        p.discard.append(tok); del p.hand[hand_idx]
        g.emit({"t":g.turn,"a":"play_vp","p":pid,"vp":vp,"bonus":bonus,"total":p.vp}); return True
    # Library / Global
    if tok not in g.cards: return False
    c=g.cards[tok]
    # Compute play cost with discounts (not for Globals)
    cost = c.play_cost.copy()
    if c.type_!="Global":
        cost = apply_discount(cost, slot_discount_for(p,c))
    if not can_pay(p, cost): return False
    pay(p, cost)
    # Global immediate
    if c.type_=="Global":
        del p.hand[hand_idx]; p.discard.append(c.id)
        g.emit({"t":g.turn,"a":"play_global","p":pid,"cid":c.id,"name":c.name,"effect":c.effect,"paid":cost})
        apply_global(g, pid, c)
        return True
    # Normal card: place or active
    place_to_mat=False
    if to_mat and c.can_play_on_mat and len(p.mat)<6 and 1<=slot_idx<=6 and all(ms.slot_index!=slot_idx for ms in p.mat):
        p.mat.append(MatSlot(cid=c.id, placed_turn=g.turn, slot_index=slot_idx))
        place_to_mat=True
        # Slot 3 compost on placement
        if slot_idx==3 and p.hand:
            idx=None
            for i,t in enumerate(p.hand):
                if not t.startswith("VP:"): idx=i; break
            if idx is None: idx=0
            removed=p.hand.pop(idx)
            g.emit({"t":g.turn,"a":"slot3_compost","p":pid,"card":removed})
    else:
        p.discard.append(c.id)
    del p.hand[hand_idx]
    if c.id not in p.first_play_turn: p.first_play_turn[c.id]=g.turn
    g.emit({"t":g.turn,"a":"play_card","p":pid,"cid":c.id,"name":c.name,"type":c.type_,"domain":c.domain,"to_mat":place_to_mat,"slot":(slot_idx if place_to_mat else 0),"paid":cost})
    apply_effect(g,pid,c)
    # Crown's Decree tracker (domains)
    if c.domain and c.domain!="None":
        g.round_mods.domains_played_this_round[pid].add(c.domain)
        if (not g.round_mods.decree_claimed) and len(g.round_mods.domains_played_this_round[pid])>=3:
            p.vp += 2
            g.round_mods.decree_claimed = True
            g.emit({"t":g.turn,"a":"decree_vp","p":pid,"vp":2,"total":p.vp})
    return True

# ----- Action: buy from Pool -----
def act_buy_pool(g:Game,pid:int,pool_idx:int)->bool:
    p=g.players[pid]
    if not (0<=pool_idx<len(g.pool)): return False
    cid=g.pool[pool_idx]; c=g.cards[cid]
    if p.res["plasma"]<c.buy_cost_plasma: return False
    p.res["plasma"]-=c.buy_cost_plasma; p.discard.append(cid)
    p.owned[cid]=p.owned.get(cid,0)+1
    g.emit({"t":g.turn,"a":"buy","p":pid,"cid":cid,"name":c.name})
    if g.supply: g.pool[pool_idx]=g.supply.pop()
    else: g.pool.pop(pool_idx)
    return True

# ----- Worker placement with occupancy caps -----
def field_capacity(g:Game, field:str) -> int:
    # Accumulation fields: 1; Other basic fields: 2; Forage unlimited when Plentiful Forage active; Initiative: 1
    if field == "forage" and g.round_mods.forage_bonus_this_round > 0:
        return 999
    if field in ("ash","shards","initiative"): return 1
    if field in ("plasma","forage","rookery","compost"): return 2
    return 1

def place_worker(g:Game,pid:int,field:str)->bool:
    p=g.players[pid]
    if p.workers_available<=0 or field not in FIELDS: return False
    cap = field_capacity(g, field)
    if g.occupancy[field] >= cap:
        return False  # blocked this round
    # resolve field
    if field=="plasma": p.res["plasma"]+=1
    elif field=="ash": p.res["ash"]+=g.ash_pile; g.ash_pile=1
    elif field=="shards": p.res["shards"]+=g.shards_pile; g.shards_pile=1
    elif field=="forage":
        bonus = g.round_mods.forage_bonus_this_round
        for _ in range(1 + bonus):
            p.res[random.choice(["nut","berry","mushroom"])] += 1
    elif field=="rookery":
        affordable=sum(1 for cid in g.pool if g.cards[cid].buy_cost_plasma<=p.res["plasma"])
        if affordable<2 and g.supply:
            replaced=[]
            k=min(3,len(g.pool))
            for _ in range(k):
                idx=random.randrange(len(g.pool))
                replaced.append(g.pool[idx])
                g.pool[idx]=g.supply.pop()
            g.emit({"t":g.turn,"a":"rookery_replace","p":pid,"replaced":replaced})
        elif g.pool:
            idx=random.randrange(len(g.pool)); cid=g.pool.pop(idx)
            p.discard.append(cid); p.owned[cid]=p.owned.get(cid,0)+1
            g.emit({"t":g.turn,"a":"rookery_take","p":pid,"cid":cid})
            if g.supply: g.pool.append(g.supply.pop())
    elif field=="compost":
        i=None
        for j,tok in enumerate(p.hand):
            if not tok.startswith("VP:"): i=j; break
        if i is None and p.hand: i=0
        if i is not None:
            removed=p.hand.pop(i); g.emit({"t":g.turn,"a":"compost","p":pid,"card":removed})
    elif field=="initiative":
        # Claim first player next round and discard one Pool card immediately
        g.next_round_starter = pid
        if g.pool:
            idx = g.rng.randrange(len(g.pool))
            removed = g.pool.pop(idx)
            g.emit({"t":g.turn,"a":"initiative_discard_pool","p":pid,"removed":removed,"idx":idx})
            if g.supply:
                g.pool.append(g.supply.pop())
    # record placement
    g.occupancy[field] += 1
    p.workers_available-=1
    g.emit({"t":g.turn,"a":"place_worker","p":pid,"field":field,"occ":g.occupancy[field],"cap":cap})
    return True

# ---------- Effects ----------
def apply_effect(g:Game,pid:int,c:Card):
    p=g.players[pid]
    if c.effect.startswith("draw:"):
        n=int(c.effect.split(":")[1])
        for _ in range(n): draw(g,p,1)
        g.emit({"t":g.turn,"a":"effect","p":pid,"cid":c.id,"e":f"draw:{n}"})
    elif c.effect=="draw2_discard1":
        draw(g,p,2)
        if p.hand:
            dumped=p.hand.pop(); p.discard.append(dumped)
        g.emit({"t":g.turn,"a":"effect","p":pid,"cid":c.id,"e":"draw2_discard1"})
    # extend as needed

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
        g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"crowns_decree_active"})
    else:
        g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":eff})

# ---------- Bot ----------
def engine_strength(p:Player)->float:
    return p.res["plasma"] + 0.5*(p.res["ash"]+p.res["shards"]) + 0.3*len(p.hand) + 0.8*len(p.mat)

def card_score_for_pool(g:Game,p:Player,c:Card)->float:
    score = 0.0
    if p.res["plasma"]>=c.buy_cost_plasma: score += 1.0
    # crude synergy
    types_on_mat=set(getattr(g.cards[ms.cid],'type_',None) for ms in p.mat)
    domains_on_mat=set(getattr(g.cards[ms.cid],'domain',None) for ms in p.mat)
    if c.type_ in types_on_mat: score += 1.5
    if c.domain in domains_on_mat: score += 1.5
    if "draw" in c.effect: score += 1.0
    if "on_mat" in c.effect or c.mat_points>0: score += 1.2
    if c.type_=="Global": score += 0.6
    return score

def choose_slot_for(p:Player,c:Card)->int:
    if c.type_=="Critter": pref=4
    elif c.type_=="Farm": pref=5
    elif c.type_=="Wild": pref=6
    else: pref=1
    occupied={ms.slot_index for ms in p.mat}
    if pref not in occupied: return pref
    for s in [1,2,3,4,5,6]:
        if s not in occupied: return s
    return 0

def legal_actions(g:Game,pid:int)->List[Tuple[str,Any]]:
    p=g.players[pid]
    acts=[("pass",None)]
    # play actions
    for i,tok in enumerate(p.hand):
        if tok.startswith("RES:") or tok.startswith("VP:"):
            acts.append(("play",(i,False,0)))
        elif tok in g.cards:
            c=g.cards[tok]
            # compute discounted cost for feasibility
            cost = c.play_cost.copy()
            if c.type_!="Global":
                cost = apply_discount(cost, slot_discount_for(p,c))
            if c.type_=="Global":
                if can_pay(p, cost): acts.append(("play",(i,False,0)))
            elif can_pay(p, cost):
                acts.append(("play",(i,False,0)))
                if c.can_play_on_mat and len(p.mat)<6:
                    s=choose_slot_for(p,c)
                    if s>0: acts.append(("play",(i,True,s)))
    # buyables
    for idx,cid in enumerate(g.pool):
        if p.res["plasma"]>=g.cards[cid].buy_cost_plasma:
            acts.append(("buy_pool",idx))
    # place worker if space exists anywhere
    if p.workers_available>0:
        for f in ["ash","shards","initiative","plasma","forage","rookery","compost"]:
            if g.occupancy[f] < field_capacity(g,f):
                acts.append(("place_worker",f))
    return acts

def greedy_choose(g:Game,pid:int)->Tuple[str,Any]:
    acts=legal_actions(g,pid)
    best=None; bestv=-1e9
    for a,arg in acts:
        v = -1.0
        if a=="play":
            i,to_mat,s = arg
            tok=g.players[pid].hand[i]
            if tok.startswith("VP:"):
                vp=int(tok.split(":")[1])
                v = 2.5*vp if engine_strength(g.players[pid])>=vp else 0.1*vp
            elif tok.startswith("RES:"):
                v = 0.8
            else:
                c=g.cards[tok]; v = 1.0
                if c.type_=="Global": v += 0.6
                if to_mat: v += 0.8 if (c.mat_points>0 or "on_mat" in c.effect) else 0.1
        elif a=="buy_pool":
            cid=g.pool[arg]; c=g.cards[cid]; v = card_score_for_pool(g,g.players[pid],c)
        elif a=="place_worker":
            f=arg; v = {"ash":2.0,"shards":2.0,"initiative":1.6,"plasma":1.0,"forage":1.0,"rookery":1.2,"compost":0.5}.get(f,0.5)
            if g.occupancy[f] >= field_capacity(g,f): v = -1.0
        elif a=="pass":
            v=0.0
        if v>bestv: best=(a,arg); bestv=v
    return best if best else ("pass",None)

# Simple MCTS-lite
def clone(g:Game)->Game:
    return copy.deepcopy(g)

def apply_action(g:Game, pid:int, action:Tuple[str,Any]):
    a,arg=action
    if a=="play":
        i,to_mat,s=arg; act_play(g,pid,i,to_mat,s)
    elif a=="buy_pool":
        act_buy_pool(g,pid,arg)
    elif a=="place_worker":
        place_worker(g,pid,arg)

def rollout(g:Game, pid:int, horizon:int)->float:
    steps=0
    while steps<horizon and not is_terminal(g):
        act=greedy_choose(g, g.current)
        apply_action(g, g.current, act)
        g.current=(g.current+1)%len(g.players)
        if g.current==0:
            end_of_round(g); start_of_round(g)
    return g.players[pid].vp + 0.2*(engine_strength(g.players[pid]))

def candidate_actions(g:Game, pid:int)->List[Tuple[str,Any]]:
    acts=legal_actions(g,pid)
    return acts[:6] if len(acts)>6 else acts

def mcts_choose(g:Game, pid:int, rollouts:int, horizon:int)->Tuple[str,Any]:
    cands=candidate_actions(g,pid)
    best=None; bestv=-1e9
    for a in cands:
        acc=0.0
        for _ in range(rollouts):
            gg=clone(g)
            apply_action(gg, pid, a)
            val=rollout(gg, pid, horizon)
            acc+=val
        avg=acc/rollouts
        if avg>bestv: best=a; bestv=avg
    return best if best else ("pass",None)

# -------------- Round transitions --------------
def end_of_round(g:Game):
    # Blight: compost 1 for all players (non-VP preferred)
    if g.round_mods.blight_this_round:
        for p in g.players:
            idx=None
            for i,t in enumerate(p.hand):
                if not t.startswith("VP:"): idx=i; break
            if idx is None and p.hand: idx=0
            if idx is not None:
                removed=p.hand.pop(idx)
                g.emit({"t":g.turn,"a":"blight_compost","p":p.id,"card":removed})
    # reset per-round trackers
    g.round_mods.blight_this_round = False
    g.round_mods.forage_bonus_this_round = 0
    g.round_mods.decree_claimed = False
    g.round_mods.domains_played_this_round=[set() for _ in g.players]
    # reset field occupancy
    g.occupancy = {f:0 for f in FIELDS}

def start_of_round(g:Game):
    # set start player if someone claimed initiative
    if g.next_round_starter is not None:
        g.current = g.next_round_starter
        g.emit({"t":g.turn,"a":"initiative_start_player","p":g.current})
        g.next_round_starter = None
    else:
        # otherwise continue normal rotation (g.current already 0 at new round)
        pass
    # grow accumulation
    g.ash_pile+=1; g.shards_pile+=1
    # reset workers to 2 (v4)
    for p in g.players: p.workers_available=2
    # hand top-up (with next-round delta)
    target = g.cfg.hand_size + g.round_mods.hand_delta_next_round
    for p in g.players:
        draw_to_hand_size(g, p, max(0, target))
    g.round_mods.hand_delta_next_round = 0
    # income drip
    for p in g.players: p.res["plasma"]+=1

# -------------- Loop --------------
def step_turn(g:Game):
    pid=g.current
    for _ in range(2):
        act = mcts_choose(g, pid, g.cfg.rollouts, g.cfg.horizon) if g.cfg.mcts else greedy_choose(g, pid)
        if act[0]=="pass": break
        apply_action(g, pid, act)
    g.current=(g.current+1)%len(g.players)
    if g.current==0:
        g.turn+=1
        end_of_round(g)
        start_of_round(g)

def is_terminal(g:Game)->bool:
    if g.turn>200: return True
    for p in g.players:
        if p.vp>=g.cfg.victory_vp: return True
    return False

def winner_id(g:Game)->Optional[int]:
    for p in g.players:
        if p.vp>=g.cfg.victory_vp: return p.id
    return None

def play_one(cfg:Config)->Dict[str,Any]:
    g=setup(cfg)
    while not is_terminal(g):
        step_turn(g)
    g.winner=winner_id(g)
    # log mat durations
    for p in g.players:
        for ms in p.mat:
            duration = (g.turn - ms.placed_turn) + 1
            g.emit({"t":g.turn,"a":"mat_duration","p":p.id,"cid":ms.cid,"slot":ms.slot_index,"duration":duration})
    g.emit({"t":g.turn,"a":"end","winner":g.winner})
    return {"winner":g.winner,"turn":g.turn,"log":g.log}

def run_many(games:int=20, seed:int=42, **kwargs)->Dict[str,Any]:
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
    # median turns
    med = median([o["turn"] for o in outs]) if outs else None
    return {"games":games,"median_turns":med,"logs":log_files}

# -------------- CLI --------------
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
    cfg = Config(seed=args.seed, mcts=args.mcts, rollouts=args.rollouts, horizon=args.horizon,
                 cards_csv=args.cards, globals_csv=args.globals)
    out = run_many(args.games, args.seed, mcts=args.mcts, rollouts=args.rollouts, horizon=args.horizon)
    print(json.dumps(out, indent=2))
