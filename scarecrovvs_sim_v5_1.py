# Scarecrovvs Realm Simulator v5.1
# v5 + compost triggers: if_composted_gain:<resource>:<amount> (alias on_compost:...)
# - Robust effect tag parsing (semicolon-separated)
# - Fires on Slot3 compost, Compost field, and Blight
# - Logs on_compost_gain and adds compost columns to summary CSV
# - Workers/player = 2; Initiative capacity = 1; occupancy caps preserved
# - Globals from globals.csv; logs only real moves (no rollout logs)

from __future__ import annotations
import os, csv, json, random, copy, argparse, time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

RES    = ["plasma","ash","shards","nut","berry","mushroom"]
FIELDS = ["plasma","ash","shards","forage","rookery","compost","initiative"]

# ---------------- Data classes ----------------
@dataclass
class Card:
    id: str
    name: str
    buy_cost_plasma: int = 2
    play_cost: Dict[str,int] = field(default_factory=dict)
    type_: str = "None"
    domain: str = "None"
    mat_points: int = 0
    can_play_on_mat: bool = True
    effect: str = ""  # semicolon-separated tags, e.g. "draw:1;if_composted_gain:ash:1"

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
    workers_available: int = 2
    vp: int = 0
    res: Dict[str,int] = field(default_factory=lambda:{k:0 for k in RES})
    owned: Dict[str,int] = field(default_factory=dict)
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
    # VP piles (buy costs)
    vp_cost_1: int = 2
    vp_cost_2: int = 4
    vp_cost_3: int = 6
    # bot knobs
    mcts: int = 0
    rollouts: int = 6
    horizon: int = 3
    # finish games knobs
    vp_urgency_turn: int = 10
    vp_weight: float = 0.35
    late_game_turn: int = 150
    progress_every: int = 5

@dataclass
class RoundMods:
    hand_delta_next_round: int = 0
    forage_bonus_this_round: int = 0
    blight_this_round: bool = False
    decree_claimed: bool = False
    domains_played_this_round: List[set] = field(default_factory=list)
    start_player: int = 0

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
    occupancy: Dict[str,int] = field(default_factory=lambda:{f:0 for f in FIELDS})
    round_mods: RoundMods = field(default_factory=RoundMods)
    log: List[Dict[str,Any]] = field(default_factory=list)
    winner: Optional[int] = None
    record_logs: bool = True

    def emit(self, rec: Dict[str,Any]):
        if self.record_logs:
            self.log.append(rec)

# ---------------- Loaders ----------------
def load_cards(path:str)->Dict[str,Card]:
    lib={}
    with open(path, newline="", encoding="utf-8") as f:
        r=csv.DictReader(f)
        for row in r:
            if not row.get("id"): continue
            c=Card.from_row(row); lib[c.id]=c
    return lib

def load_globals(path:str)->Dict[str,Card]:
    if not os.path.exists(path): return {}
    lib={}
    with open(path, newline="", encoding="utf-8") as f:
        r=csv.DictReader(f)
        for row in r:
            if not row.get("id"): continue
            c=Card.from_row(row); c.type_="Global"; c.can_play_on_mat=False
            lib[c.id]=c
    return lib

# ---------------- Setup ----------------
def setup(cfg:Config)->Game:
    rng=random.Random(cfg.seed)
    lib=load_cards(cfg.cards_csv)
    lib.update(load_globals(cfg.globals_csv))
    supply=[cid for cid in lib.keys() for _ in range(cfg.copies_per_unique)]
    rng.shuffle(supply)
    pool=[supply.pop() for _ in range(min(10,len(supply)))]
    players=[]
    for pid in range(cfg.players):
        deck=["RES:plasma"]*6 + ["VP:1"]*4
        rng.shuffle(deck)
        players.append(Player(id=pid, deck=deck, hand=[], discard=[]))
    g=Game(cfg=cfg, rng=rng, cards=lib, supply=supply, pool=pool, players=players)
    g.round_mods.domains_played_this_round=[set() for _ in range(cfg.players)]
    for p in g.players: draw_to_hand_size(g,p,cfg.hand_size)
    for p in g.players: p.res["plasma"]+=1
    return g

# ---------------- Helpers ----------------
def draw(g:Game,p:Player,n:int):
    for _ in range(n):
        if not p.deck:
            p.deck=p.discard; g.rng.shuffle(p.deck); p.discard=[]
        if not p.deck: return
        p.hand.append(p.deck.pop())

def draw_to_hand_size(g:Game,p:Player,target:int):
    need=max(0,target-len(p.hand))
    if need>0: draw(g,p,need)

def slot2_type(g:Game,p:Player)->Optional[str]:
    for ms in p.mat:
        if ms.slot_index==2:
            return g.cards[ms.cid].type_
    return None

def total_discount_for_card(g:Game,p:Player,c:Card)->int:
    disc=0
    s2=slot2_type(g,p)
    if s2 and c.type_==s2: disc=1
    if any(ms.slot_index==4 for ms in p.mat) and c.type_=="Critter": disc=1
    if any(ms.slot_index==5 for ms in p.mat) and c.type_=="Farm": disc=1
    if any(ms.slot_index==6 for ms in p.mat) and c.type_=="Wild": disc=1
    return min(disc,1)

def discounted_cost(c:Card,disc:int)->Dict[str,int]:
    if disc<=0: return c.play_cost.copy()
    cost=c.play_cost.copy()
    for k in RES:
        if cost.get(k,0)>0:
            cost[k]-=1
            if cost[k]==0: del cost[k]
            break
    return cost

def can_pay_res(p:Player,cost:Dict[str,int])->bool:
    return all(p.res.get(k,0)>=v for k,v in cost.items())

def pay_res(p:Player,cost:Dict[str,int])->None:
    for k,v in cost.items(): p.res[k]-=v

# ----- Effect tag parsing & compost trigger extraction -----
def effect_tags(effect_str: str) -> List[str]:
    if not effect_str: return []
    return [t.strip() for t in effect_str.split(";") if t.strip()]

def compost_gains_for(card: Card) -> Dict[str,int]:
    gains: Dict[str,int] = {}
    for tag in effect_tags(card.effect):
        # supports both if_composted_gain:ash:1 and on_compost:ash:1
        if tag.startswith("if_composted_gain:") or tag.startswith("on_compost:"):
            parts = tag.split(":")
            # expecting 3 parts: prefix, resource, amount
            if len(parts) >= 3:
                res = parts[1].strip().lower()
                try:
                    amt = int(parts[2].strip())
                except:
                    amt = 0
                if res in RES and amt>0:
                    gains[res] = gains.get(res, 0) + amt
    return gains

def grant_resources(p:Player, grants:Dict[str,int]):
    for k,v in grants.items():
        p.res[k] = p.res.get(k,0) + v

# Centralized compost helper: remove a specific hand index & trigger on_compost_gain if relevant
def compost_from_hand(g:Game, pid:int, index:int, reason:str):
    p=g.players[pid]
    if not (0<=index<len(p.hand)): return None
    tok = p.hand.pop(index)
    # If it's a library card id, check compost gains
    if tok in g.cards:
        c = g.cards[tok]
        gains = compost_gains_for(c)
        if gains:
            grant_resources(p, gains)
            g.emit({"t":g.turn,"a":"on_compost_gain","p":pid,"cid":c.id,"grants":gains,"reason":reason})
    # Log the compost itself for traceability
    g.emit({"t":g.turn,"a":"compost","p":pid,"card":tok,"reason":reason})
    return tok

# ---------------- Actions ----------------
def act_buy_pool(g:Game,pid:int,idx:int)->bool:
    p=g.players[pid]
    if not (0<=idx<len(g.pool)): return False
    cid=g.pool[idx]; c=g.cards[cid]
    if p.res["plasma"]<c.buy_cost_plasma: return False
    p.res["plasma"]-=c.buy_cost_plasma
    p.discard.append(cid)
    p.owned[cid]=p.owned.get(cid,0)+1
    g.emit({"t":g.turn,"a":"buy","p":pid,"cid":cid,"name":c.name})
    if g.supply: g.pool[idx]=g.supply.pop()
    else: g.pool.pop(idx)
    return True

def act_buy_vp(g:Game,pid:int,vp:int)->bool:
    p=g.players[pid]
    cost = {1:g.cfg.vp_cost_1, 2:g.cfg.vp_cost_2, 3:g.cfg.vp_cost_3}.get(vp, None)
    if cost is None or p.res["plasma"]<cost: return False
    p.res["plasma"]-=cost
    p.discard.append(f"VP:{vp}")
    g.emit({"t":g.turn,"a":"buy_vp","p":pid,"vp":vp,"cost":cost})
    return True

def act_play(g:Game,pid:int,hand_idx:int,to_mat:bool=False,slot_idx:int=0)->bool:
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
        bonus=2 if any(ms.slot_index==1 for ms in p.mat) else 0
        p.vp+=vp+bonus
        p.discard.append(tok); del p.hand[hand_idx]
        g.emit({"t":g.turn,"a":"play_vp","p":pid,"vp":vp,"bonus":bonus,"total":p.vp}); return True
    # Library / Global
    if tok not in g.cards: return False
    c=g.cards[tok]
    # Globals: immediate, no mat
    if c.type_=="Global":
        cost=c.play_cost.copy()
        if not can_pay_res(p,cost): return False
        pay_res(p,cost)
        del p.hand[hand_idx]; p.discard.append(c.id)
        g.emit({"t":g.turn,"a":"play_global","p":pid,"cid":c.id,"name":c.name,"effect":c.effect,"paid":cost})
        apply_global(g,pid,c); return True
    # Regular card
    disc=total_discount_for_card(g,p,c)
    cost=discounted_cost(c,disc)
    if not can_pay_res(p,cost): return False
    pay_res(p,cost)
    placed=False
    if to_mat and c.can_play_on_mat and len(p.mat)<6 and 1<=slot_idx<=6 and all(ms.slot_index!=slot_idx for ms in p.mat):
        p.mat.append(MatSlot(cid=c.id, placed_turn=g.turn, slot_index=slot_idx)); placed=True
        if slot_idx==3 and p.hand:
            # compost from hand with trigger
            idx=None
            for i,t in enumerate(p.hand):
                if not t.startswith("VP:"): idx=i; break
            if idx is None: idx=0
            compost_from_hand(g, pid, idx, reason="slot3")
    else:
        p.discard.append(c.id)
    del p.hand[hand_idx]
    if c.id not in p.first_play_turn: p.first_play_turn[c.id]=g.turn
    g.emit({"t":g.turn,"a":"play_card","p":pid,"cid":c.id,"name":c.name,"type":c.type_,"domain":c.domain,"to_mat":placed,"slot":(slot_idx if placed else 0),"paid":cost})
    apply_effect(g,pid,c)
    # Crown's Decree
    if c.domain and c.domain!="None":
        g.round_mods.domains_played_this_round[pid].add(c.domain)
        if (not g.round_mods.decree_claimed) and len(g.round_mods.domains_played_this_round[pid])>=3:
            p.vp+=2; g.round_mods.decree_claimed=True
            g.emit({"t":g.turn,"a":"decree_vp","p":pid,"vp":2,"total":p.vp})
    return True

def field_capacity(g:Game, field:str)->int:
    if field=="forage" and g.round_mods.forage_bonus_this_round>0: return 999
    if field in ("ash","shards","initiative"): return 1
    if field in ("plasma","forage","rookery","compost"): return 2
    return 1

def place_worker(g:Game,pid:int,field:str)->bool:
    p=g.players[pid]
    if p.workers_available<=0 or field not in FIELDS: return False
    cap=field_capacity(g,field)
    if g.occupancy[field]>=cap:
        return False
    if field=="plasma": p.res["plasma"]+=1
    elif field=="ash": p.res["ash"]+=g.ash_pile; g.ash_pile=1
    elif field=="shards": p.res["shards"]+=g.shards_pile; g.shards_pile=1
    elif field=="forage":
        bonus=g.round_mods.forage_bonus_this_round
        for _ in range(1+bonus):
            p.res[random.choice(["nut","berry","mushroom"])] += 1
    elif field=="rookery":
        if g.pool:
            idx=g.rng.randrange(len(g.pool)); cid=g.pool.pop(idx)
            p.discard.append(cid); p.owned[cid]=p.owned.get(cid,0)+1
            if g.supply: g.pool.append(g.supply.pop())
    elif field=="compost":
        if p.hand:
            compost_from_hand(g, pid, 0, reason="compost_field")
    elif field=="initiative":
        g.round_mods.start_player=pid
        if g.pool:
            idx=g.rng.randrange(len(g.pool)); removed=g.pool.pop(idx)
            g.emit({"t":g.turn,"a":"initiative_discard","p":pid,"card":removed})
            if g.supply: g.pool.append(g.supply.pop())
    g.occupancy[field]+=1
    p.workers_available-=1
    g.emit({"t":g.turn,"a":"place_worker","p":pid,"field":field,"occ":g.occupancy[field],"cap":cap})
    return True

# ---------------- Effects ----------------
def apply_effect(g:Game,pid:int,c:Card):
    p=g.players[pid]
    # parse semicolon-separated tags; handle what we know, ignore unknowns safely
    for tag in effect_tags(c.effect):
        if tag.startswith("draw:"):
            raw = tag.split(":",1)[1]
            num_str = raw.split(";",1)[0].strip()
            try:
                n = int(num_str)
            except:
                n = 0
            for _ in range(n): draw(g,p,1)
            g.emit({"t":g.turn,"a":"effect","p":pid,"cid":c.id,"e":f"draw:{n}"})
        elif tag=="draw2_discard1":
            draw(g,p,2)
            if p.hand:
                dumped=p.hand.pop(); p.discard.append(dumped)
            g.emit({"t":g.turn,"a":"effect","p":pid,"cid":c.id,"e":"draw2_discard1"})
        elif tag.startswith("if_composted_gain:") or tag.startswith("on_compost:"):
            # handled when card is composted; ignore here
            continue
        else:
            # unknown tag -> ignore, but could log once if desired
            pass

def apply_global(g:Game,pid:int,c:Card):
    # Still allow multiple tags for globals
    for tag in effect_tags(c.effect):
        if tag=="hand_size_delta_next_round:-1":
            g.round_mods.hand_delta_next_round -= 1
            g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"drought_next_round_-1"})
        elif tag=="hand_size_delta_next_round:+1":
            g.round_mods.hand_delta_next_round += 1
            g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"flood_next_round_+1"})
        elif tag=="end_round_all_compost:1":
            g.round_mods.blight_this_round = True
            g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"blight_end_round_compost"})
        elif tag=="forage_yield_bonus_this_round:+1":
            g.round_mods.forage_bonus_this_round += 1
            g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"plentiful_forage_this_round"})
        elif tag=="first_to_play_three_domains:+2vp":
            g.emit({"t":g.turn,"a":"global","p":pid,"cid":c.id,"e":"crowns_decree_active"})
        else:
            # ignore unknown at runtime
            pass

# ---------------- Bot ----------------
def engine_strength(p:Player)->float:
    return p.res["plasma"] + 0.5*(p.res["ash"]+p.res["shards"]) + 0.3*len(p.hand) + 0.8*len(p.mat)

def vp_urgency(g:Game)->float:
    return 0.0 if g.turn<=g.cfg.vp_urgency_turn else min(1.0, 0.1*(g.turn - g.cfg.vp_urgency_turn))

def card_score_for_pool(g:Game,p:Player,c:Card)->float:
    score=0.0
    if p.res["plasma"]>=c.buy_cost_plasma: score+=1.0
    types_on_mat={ g.cards[ms.cid].type_ for ms in p.mat }
    domains_on_mat={ g.cards[ms.cid].domain for ms in p.mat }
    if c.type_ in types_on_mat: score+=1.5
    if c.domain in domains_on_mat: score+=1.5
    if "draw" in c.effect: score+=1.0
    if ("on_mat" in c.effect) or c.mat_points>0: score+=1.2
    if c.type_=="Global": score+=0.6
    score -= vp_urgency(g)*0.6
    return score

def choose_slot_for(p:Player,c:Card)->int:
    if c.type_=="Critter": pref=4
    elif c.type_=="Farm": pref=5
    elif c.type_=="Wild": pref=6
    else: pref=1
    occ={ms.slot_index for ms in p.mat}
    if pref not in occ: return pref
    for s in [1,2,3,4,5,6]:
        if s not in occ: return s
    return 0

def legal_actions(g:Game,pid:int)->List[Tuple[str,Any]]:
    p=g.players[pid]
    acts=[("pass",None)]
    # plays
    for i,tok in enumerate(p.hand):
        if tok.startswith("RES:") or tok.startswith("VP:"):
            acts.append(("play",(i,False,0)))
        elif tok in g.cards:
            c=g.cards[tok]
            if c.type_=="Global":
                if can_pay_res(p,c.play_cost): acts.append(("play",(i,False,0)))
            else:
                disc=total_discount_for_card(g,p,c)
                cost=discounted_cost(c,disc)
                if can_pay_res(p,cost):
                    acts.append(("play",(i,False,0)))
                    if c.can_play_on_mat and len(p.mat)<6:
                        s=choose_slot_for(p,c)
                        if s>0: acts.append(("play",(i,True,s)))
    # buys from Pool
    for idx,cid in enumerate(g.pool):
        if g.cards[cid].buy_cost_plasma <= p.res["plasma"]:
            acts.append(("buy_pool",idx))
    # buys from VP piles
    for vp,cost in ((1,g.cfg.vp_cost_1),(2,g.cfg.vp_cost_2),(3,g.cfg.vp_cost_3)):
        if p.res["plasma"]>=cost:
            acts.append(("buy_vp",vp))
    # workers
    if p.workers_available>0:
        for f in FIELDS:
            if g.occupancy[f] < field_capacity(g,f):
                acts.append(("place_worker",f))
    return acts

def greedy_choose(g:Game,pid:int)->Tuple[str,Any]:
    p=g.players[pid]
    acts=legal_actions(g,pid)
    best=None; bestv=-1e9
    urgency = vp_urgency(g)
    for a,arg in acts:
        v=-1.0
        if a=="play":
            i,to_mat,s=arg
            tok=p.hand[i]
            if tok.startswith("VP:"):
                vp=int(tok.split(":")[1])
                v = (2.0+2.0*urgency)*vp
                if any(ms.slot_index==1 for ms in p.mat): v += 0.5
            elif tok.startswith("RES:"):
                v = 0.5 - 0.2*urgency
            else:
                c=g.cards[tok]; v=1.0 - 0.6*urgency
                if c.type_=="Global": v+=0.6
                if to_mat: v += 0.8 if (c.mat_points>0 or "on_mat" in c.effect) else 0.1
        elif a=="buy_pool":
            cid=g.pool[arg]; c=g.cards[cid]
            v = card_score_for_pool(g,p,c)
        elif a=="buy_vp":
            vp=arg
            v = (1.0+3.0*urgency)*vp
        elif a=="place_worker":
            f=arg
            base={"ash":2.0,"shards":2.0,"plasma":1.0,"forage":1.0,"rookery":1.2,"compost":0.6,"initiative":1.4}.get(f,0.5)
            if any(tok.startswith("VP:") for tok in p.hand):
                base += 0.2
            v = base - 0.6*urgency
            if g.occupancy[f] >= field_capacity(g,f): v = -1.0
        elif a=="pass":
            v=0.0
        if v>bestv:
            best=(a,arg); bestv=v
    if g.turn>=g.cfg.late_game_turn:
        for a,arg in acts:
            if a=="buy_vp" or (a=="play" and p.hand[arg[0]].startswith("VP:")):
                return (a,arg)
    return best if best else ("pass",None)

# -------------- MCTS-lite (no logging in rollouts) --------------
def clone_for_rollout(g:Game)->Game:
    gg:Game = copy.deepcopy(g)
    gg.record_logs=False
    return gg

def apply_action(g:Game,pid:int,a:Tuple[str,Any]):
    kind,arg=a
    if kind=="play": i,to_mat,s=arg; act_play(g,pid,i,to_mat,s)
    elif kind=="buy_pool": act_buy_pool(g,pid,arg)
    elif kind=="buy_vp": act_buy_vp(g,pid,arg)
    elif kind=="place_worker": place_worker(g,pid,arg)

def candidate_actions(g:Game,pid:int)->List[Tuple[str,Any]]:
    acts=legal_actions(g,pid)
    return acts[:8] if len(acts)>8 else acts

def rollout(g:Game,pid:int,horizon:int)->float:
    steps=0
    while steps<horizon and not is_terminal(g):
        a=greedy_choose(g,g.current)
        apply_action(g,g.current,a)
        g.current=(g.current+1)%len(g.players)
        if g.current==0:
            end_of_round(g); start_of_round(g)
        steps+=1
    p=g.players[pid]
    return p.vp + g.cfg.vp_weight*engine_strength(p)

def mcts_choose(g:Game,pid:int,rollouts:int,horizon:int)->Tuple[str,Any]:
    best=None; bestv=-1e9
    for a in candidate_actions(g,pid):
        acc=0.0
        for _ in range(rollouts):
            gg=clone_for_rollout(g)
            apply_action(gg,pid,a)
            val=rollout(gg,pid,horizon)
            acc+=val
        avg=acc/rollouts
        if avg>bestv: best=a; bestv=avg
    return best if best else ("pass",None)

# ---------------- Rounds & Loop ----------------
def end_of_round(g:Game):
    if g.round_mods.blight_this_round:
        for p in g.players:
            idx=None
            for i,t in enumerate(p.hand):
                if not t.startswith("VP:"): idx=i; break
            if idx is None and p.hand: idx=0
            if idx is not None:
                compost_from_hand(g, p.id, idx, reason="blight")
    g.round_mods.blight_this_round=False
    g.round_mods.forage_bonus_this_round=0
    g.round_mods.decree_claimed=False
    g.round_mods.domains_played_this_round=[set() for _ in g.players]
    g.occupancy={f:0 for f in FIELDS}

def start_of_round(g:Game):
    g.ash_pile += 1; g.shards_pile += 1
    g.current = g.round_mods.start_player
    g.emit({"t":g.turn,"a":"initiative_start_player","p":g.current})
    for p in g.players: p.workers_available=2
    target=g.cfg.hand_size + g.round_mods.hand_delta_next_round
    for p in g.players: draw_to_hand_size(g,p,max(0,target))
    g.round_mods.hand_delta_next_round=0
    for p in g.players: p.res["plasma"]+=1

def is_terminal(g:Game)->bool:
    if g.turn>200: return True
    for p in g.players:
        if p.vp>=g.cfg.victory_vp: return True
    return False

def winner_id(g:Game)->Optional[int]:
    for p in g.players:
        if p.vp>=g.cfg.victory_vp: return p.id
    return None

def step_turn(g:Game):
    pid=g.current
    for _ in range(2):
        a = mcts_choose(g,pid,g.cfg.rollouts,g.cfg.horizon) if g.cfg.mcts else greedy_choose(g,pid)
        if a[0]=="pass": break
        apply_action(g,pid,a)
    g.current=(g.current+1)%len(g.players)
    if g.current==0:
        g.turn+=1
        end_of_round(g); start_of_round(g)

def play_one(cfg:Config)->Dict[str,Any]:
    g=setup(cfg)
    while not is_terminal(g):
        step_turn(g)
    g.winner=winner_id(g)
    for p in g.players:
        for ms in p.mat:
            duration=(g.turn - ms.placed_turn) + 1
            g.emit({"t":g.turn,"a":"mat_duration","p":p.id,"cid":ms.cid,"slot":ms.slot_index,"duration":duration})
    g.emit({"t":g.turn,"a":"end","winner":g.winner})
    return {"winner":g.winner,"turn":g.turn,"log":g.log,"players":g.players}

# ---------------- Summaries ----------------
def turn_bucket(t:int)->str:
    if t<=5: return "early"
    if t<=10: return "mid"
    return "late"

def build_card_summary(outs:List[Dict[str,Any]], out_csv:str):
    from statistics import median
    buys, plays, to_mat, ttf, mat_dur, slots = {}, {}, {}, {}, {}, {}
    buy_bucket, play_bucket = {}, {}
    games_owned, wins_when_owned = {}, {}
    compost_trig = {}
    compost_gain = {}  # per card, per resource
    for gi,out in enumerate(outs):
        winner=out["winner"]
        owned_by={i:set() for i,_ in enumerate(out["players"])}
        for e in out["log"]:
            a=e["a"]
            if a=="buy":
                cid=e["cid"]; pid=e["p"]
                owned_by[pid].add(cid)
                buys[cid]=buys.get(cid,0)+1
                bb=turn_bucket(e["t"]); buy_bucket[(cid,bb)]=buy_bucket.get((cid,bb),0)+1
            if a=="buy_vp":
                cid=f"VP:{e['vp']}"
                buys[cid]=buys.get(cid,0)+1
                bb=turn_bucket(e["t"]); buy_bucket[(cid,bb)]=buy_bucket.get((cid,bb),0)+1
            if a=="play_card":
                cid=e["cid"]
                plays[cid]=plays.get(cid,0)+1
                if e.get("to_mat"): to_mat[cid]=to_mat.get(cid,0)+1
                pb=turn_bucket(e["t"]); play_bucket[(cid,pb)]=play_bucket.get((cid,pb),0)+1
                ttf.setdefault(cid,[]).append(e["t"])
            if a=="play_vp":
                cid=f"VP:{e['vp']}"
                plays[cid]=plays.get(cid,0)+1
                pb=turn_bucket(e["t"]); play_bucket[(cid,pb)]=play_bucket.get((cid,pb),0)+1
            if a=="mat_duration":
                cid=e["cid"]; mat_dur.setdefault(cid,[]).append(e["duration"])
                slots.setdefault(cid,{i:0 for i in range(1,7)})
                slots[cid][e["slot"]]=slots[cid].get(e["slot"],0)+1
            if a=="on_compost_gain":
                cid=e["cid"]
                compost_trig[cid]=compost_trig.get(cid,0)+1
                grants=e.get("grants",{})
                for r,v in grants.items():
                    key=(cid,r)
                    compost_gain[key]=compost_gain.get(key,0)+v
        for pid,owned in owned_by.items():
            for cid in owned:
                games_owned[cid]=games_owned.get(cid,0)+1
                if winner==pid:
                    wins_when_owned[cid]=wins_when_owned.get(cid,0)+1
    all_ids = set(list(buys.keys())+list(plays.keys())+list(slots.keys())+list(compost_trig.keys())+[f"VP:{i}" for i in (1,2,3)])
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv,"w",newline="",encoding="utf-8") as f:
        fieldnames=["card_id","bought","played","to_mat_plays","to_mat_rate","time_to_first_play_median_turn","avg_mat_duration_turns",
                    "slot1_plays","slot2_plays","slot3_plays","slot4_plays","slot5_plays","slot6_plays",
                    "buy_early","buy_mid","buy_late","play_early","play_mid","play_late",
                    "games_owned","wins_when_owned","winrate_when_owned",
                    "compost_triggers","compost_gain_plasma","compost_gain_ash","compost_gain_shards","compost_gain_nut","compost_gain_berry","compost_gain_mushroom"]
        w=csv.DictWriter(f, fieldnames=fieldnames); w.writeheader()
        for cid in sorted(all_ids):
            b=buys.get(cid,0); p=plays.get(cid,0); tm=to_mat.get(cid,0)
            ttf_list=ttf.get(cid,[])
            ttf_med = (median(ttf_list) if ttf_list else None)
            md = mat_dur.get(cid,[])
            mat_avg = (sum(md)/len(md) if md else None)
            sl = slots.get(cid,{i:0 for i in range(1,7)})
            go = games_owned.get(cid,0); wwo = wins_when_owned.get(cid,0)
            wr = (wwo/go) if go else None
            row = {
                "card_id":cid,"bought":b,"played":p,"to_mat_plays":tm,"to_mat_rate":(tm/p if p else None),
                "time_to_first_play_median_turn":ttf_med,"avg_mat_duration_turns":mat_avg,
                "slot1_plays":sl.get(1,0),"slot2_plays":sl.get(2,0),"slot3_plays":sl.get(3,0),
                "slot4_plays":sl.get(4,0),"slot5_plays":sl.get(5,0),"slot6_plays":sl.get(6,0),
                "buy_early":buy_bucket.get((cid,"early"),0),"buy_mid":buy_bucket.get((cid,"mid"),0),"buy_late":buy_bucket.get((cid,"late"),0),
                "play_early":play_bucket.get((cid,"early"),0),"play_mid":play_bucket.get((cid,"mid"),0),"play_late":play_bucket.get((cid,"late"),0),
                "games_owned":go,"wins_when_owned":wwo,"winrate_when_owned":wr,
                "compost_triggers": compost_trig.get(cid,0),
                "compost_gain_plasma": compost_gain.get((cid,"plasma"),0),
                "compost_gain_ash": compost_gain.get((cid,"ash"),0),
                "compost_gain_shards": compost_gain.get((cid,"shards"),0),
                "compost_gain_nut": compost_gain.get((cid,"nut"),0),
                "compost_gain_berry": compost_gain.get((cid,"berry"),0),
                "compost_gain_mushroom": compost_gain.get((cid,"mushroom"),0),
            }
            w.writerow(row)

def build_field_summary(outs:List[Dict[str,Any]], out_csv:str):
    totals=[]
    for out in outs:
        visits=[{f:0 for f in FIELDS} for _ in range(8)]
        start_claims=[0]*8
        round_seen_pf=False; rounds_with_pf=0
        for e in out["log"]:
            if e["a"]=="place_worker":
                pid=e["p"]; f=e["field"]; visits[pid][f]+=1
            elif e["a"]=="initiative_discard":
                pid=e["p"]; start_claims[pid]+=1
            elif e["a"]=="global" and e.get("e")=="plentiful_forage_this_round":
                round_seen_pf=True
            elif e["a"]=="initiative_start_player":
                if round_seen_pf:
                    rounds_with_pf+=1; round_seen_pf=False
        for pid in range(len(visits)):
            row={"player_id":pid}
            for f in FIELDS: row[f"visits_{f}"]=visits[pid][f]
            row["initiative_claims"]=start_claims[pid]
            totals.append(row)
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    headers=["player_id"]+[f"visits_{f}" for f in FIELDS]+["initiative_claims"]
    with open(out_csv,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=headers); w.writeheader()
        for r in totals: w.writerow(r)

# ---------------- Runner ----------------
def run_many(games:int=20, seed:int=42, **kwargs)->Dict[str,Any]:
    cfg=Config(**kwargs)
    print(f"[config] games={games} seed={seed} players={cfg.players} vp={cfg.victory_vp} mcts={cfg.mcts} rollouts={cfg.rollouts} horizon={cfg.horizon}")
    print(f"[config] vp_costs: 1VP={cfg.vp_cost_1}, 2VP={cfg.vp_cost_2}, 3VP={cfg.vp_cost_3}; vp_urgency_turn={cfg.vp_urgency_turn}, vp_weight={cfg.vp_weight}")
    rng=random.Random(seed)
    outs=[]
    t0=time.time()
    for i in range(games):
        one_cfg=Config(seed=rng.randrange(1_000_000), **kwargs)
        out=play_one(one_cfg)
        outs.append(out)
        if (i+1)%cfg.progress_every==0 or (i+1)==games:
            turns=[o["turn"] for o in outs]
            med=sorted(turns)[len(turns)//2] if turns else None
            print(f"[progress] finished {i+1}/{games} games | median_turns_so_far={med} | elapsed={time.time()-t0:.1f}s")
    # write logs
    os.makedirs("logs", exist_ok=True)
    log_files=[]
    for i,out in enumerate(outs):
        path=f"logs/game_v5_1_{seed}_{i}.jsonl"
        with open(path,"w",encoding="utf-8") as f:
            for e in out["log"]:
                f.write(json.dumps(e)+"\n")
        log_files.append(os.path.basename(path))
    # summaries
    os.makedirs("summaries", exist_ok=True)
    cards_csv=f"summaries/summary_cards_{seed}.csv"
    fields_csv=f"summaries/summary_fields_{seed}.csv"
    build_card_summary(outs, cards_csv)
    build_field_summary(outs, fields_csv)
    # topline winners
    winners={}
    for o in outs:
        winners[str(o["winner"])]=winners.get(str(o["winner"]),0)+1
    turns=[o["turn"] for o in outs]
    med = sorted(turns)[len(turns)//2] if turns else None
    print(f"[done] logs in ./logs | summaries in ./summaries")
    print(f"[done] summary_cards={cards_csv} | summary_fields={fields_csv}")
    return {"games":games,"median_turns":med,"winner_counts":winners,"logs":log_files,
            "summary_cards":os.path.basename(cards_csv),"summary_fields":os.path.basename(fields_csv)}

# ---------------- CLI ----------------
if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=25)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--mcts", type=int, default=1)
    ap.add_argument("--rollouts", type=int, default=8)
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--cards", type=str, default="cards.csv")
    ap.add_argument("--globals", type=str, default="globals.csv")
    ap.add_argument("--vp_cost_1", type=int, default=2)
    ap.add_argument("--vp_cost_2", type=int, default=4)
    ap.add_argument("--vp_cost_3", type=int, default=6)
    ap.add_argument("--vp_urgency_turn", type=int, default=10)
    ap.add_argument("--vp_weight", type=float, default=0.35)
    ap.add_argument("--late_game_turn", type=int, default=150)
    ap.add_argument("--progress_every", type=int, default=5)
    args=ap.parse_args()

    out = run_many(
        games=args.games, seed=args.seed,
        mcts=args.mcts, rollouts=args.rollouts, horizon=args.horizon,
        cards_csv=args.cards, globals_csv=args.globals,
        vp_cost_1=args.vp_cost_1, vp_cost_2=args.vp_cost_2, vp_cost_3=args.vp_cost_3,
        vp_urgency_turn=args.vp_urgency_turn, vp_weight=args.vp_weight,
        late_game_turn=args.late_game_turn, progress_every=args.progress_every
    )
    print(json.dumps(out, indent=2))
