
from __future__ import annotations
import os, json, csv, random, copy, argparse
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from statistics import median

RES = ["plasma","ash","shards","nut","berry","mushroom"]
BASIC_FIELDS = ["plasma","ash","shards","forage","rookery","compost"]

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
    effect: str = ""

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
    workers_available: int = 3
    vp: int = 0
    res: Dict[str,int] = field(default_factory=lambda:{k:0 for k in RES})
    owned: Dict[str,int] = field(default_factory=dict)
    # telemetry helpers
    first_play_turn: Dict[str,int] = field(default_factory=dict)
    mat_slot_counts: Dict[int,int] = field(default_factory=lambda:{i:0 for i in range(1,7)})
    mat_acc_vp_bonus_slot1: int = 0
    slot2_type: Optional[str] = None

@dataclass
class Config:
    seed: int = 123
    players: int = 3
    victory_vp: int = 24
    hand_size: int = 5
    copies_per_unique: int = 2
    cards_csv: str = "cards.csv"
    # bot knobs
    mcts: int = 0
    rollouts: int = 6
    horizon: int = 3

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
    # logs
    log: List[Dict[str,Any]] = field(default_factory=list)
    ended: bool = False
    winner: Optional[int] = None

    def emit(self, rec: Dict[str,Any]): self.log.append(rec)

def load_cards(path: str) -> Dict[str,Card]:
    lib={}
    with open(path, newline="", encoding="utf-8") as f:
        r=csv.DictReader(f)
        for row in r:
            if not row.get("id"): continue
            c=Card.from_row(row)
            lib[c.id]=c
    return lib

def setup(cfg: Config) -> Game:
    rng = random.Random(cfg.seed)
    lib = load_cards(cfg.cards_csv)
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
    for p in g.players: draw(g,p,cfg.hand_size)
    # round start drip
    for p in g.players: p.res["plasma"]+=1
    return g

def draw(g:Game,p:Player,n:int):
    for _ in range(n):
        if not p.deck:
            p.deck = p.discard
            g.rng.shuffle(p.deck)
            p.discard=[]
        if not p.deck: return
        p.hand.append(p.deck.pop())

# ----- Mat slot bonuses -----
def slot_discount(p:Player, card:Card) -> int:
    # slot2: -1 for chosen type; slots 4/5/6: -1 for Critter/Farm/Wild
    d=0
    # slot 2 chosen type
    if p.slot2_type and card.type_ == p.slot2_type:
        d += 1
    for s in p.mat:
        if s.slot_index==4 and card.type_=="Critter": d+=1
        if s.slot_index==5 and card.type_=="Farm": d+=1
        if s.slot_index==6 and card.type_=="Wild": d+=1
    return d

def can_pay(p:Player, card:Card) -> bool:
    # apply only a single -1 discount once (as described) – cap at 1 total discount
    # You can extend to multiple if needed.
    cost = card.play_cost.copy()
    d = min(slot_discount(p, card), 1)
    if d>0:
        # apply to any available resource in priority order plasma>ash>shards>nut>berry>mushroom
        for k in ["plasma","ash","shards","nut","berry","mushroom"]:
            if cost.get(k,0)>0:
                cost[k]-=1
                if cost[k]==0: del cost[k]
                break
    # check
    return all(p.res.get(k,0)>=v for k,v in cost.items())

def pay(p:Player, card:Card) -> Dict[str,int]:
    # same discount logic as can_pay to ensure consistency
    cost = card.play_cost.copy()
    d = min(slot_discount(p, card), 1)
    if d>0:
        for k in ["plasma","ash","shards","nut","berry","mushroom"]:
            if cost.get(k,0)>0:
                cost[k]-=1
                if cost[k]==0: del cost[k]
                break
    for k,v in cost.items():
        p.res[k]-=v
    return cost

# ----- Actions -----
def act_play(g:Game, pid:int, hand_idx:int, to_mat:bool=False, slot_idx:int=0) -> bool:
    p=g.players[pid]
    if not (0<=hand_idx<len(p.hand)): return False
    tok=p.hand[hand_idx]
    # resource
    if tok.startswith("RES:"):
        r=tok.split(":")[1]
        p.res[r]+=1; p.discard.append(tok); del p.hand[hand_idx]
        g.emit({"t":g.turn,"a":"play_res","p":pid,"res":r}); return True
    # point card
    if tok.startswith("VP:"):
        vp=int(tok.split(":")[1])
        # slot 1 bonus: +2 VP when playing VP cards if you have a card in slot 1
        bonus = 2 if any(ms.slot_index==1 for ms in p.mat) else 0
        p.vp += vp + bonus
        p.mat_acc_vp_bonus_slot1 += bonus
        p.discard.append(tok); del p.hand[hand_idx]
        g.emit({"t":g.turn,"a":"play_vp","p":pid,"vp":vp,"bonus":bonus,"total":p.vp}); return True
    # library card
    if tok not in g.cards: return False
    c=g.cards[tok]
    if not can_pay(p,c): return False
    paid=pay(p,c)
    # decide placement
    if to_mat and c.can_play_on_mat and len(p.mat)<6 and 1<=slot_idx<=6 and all(ms.slot_index!=slot_idx for ms in p.mat):
        # Slot-specific hooks
        if slot_idx == 2:
            # Choose discount type based on the card placed (if multi-type existed we'd choose once).
            p.slot2_type = c.type_
        if slot_idx == 3:
            # Compost one card from hand (remove from game) – prefer RES, then any non-VP.
            rm_idx = None
            for j,tok2 in enumerate(p.hand):
                if tok2.startswith("RES:"):
                    rm_idx = j; break
            if rm_idx is None:
                for j,tok2 in enumerate(p.hand):
                    if not tok2.startswith("VP:"):
                        rm_idx = j; break
            if rm_idx is not None:
                removed = p.hand.pop(rm_idx)
                g.emit({"t":g.turn,"a":"slot3_compost","p":pid,"removed":removed})
        p.mat.append(MatSlot(cid=c.id, placed_turn=g.turn, slot_index=slot_idx))
        p.mat_slot_counts[slot_idx]+=1
        to_mat_flag=True
    else:
        p.discard.append(c.id); to_mat_flag=False; slot_idx=0
    del p.hand[hand_idx]
    # track first play
    if c.id not in p.first_play_turn:
        p.first_play_turn[c.id]=g.turn
    g.emit({"t":g.turn,"a":"play_card","p":pid,"cid":c.id,"name":c.name,"type":c.type_,"domain":c.domain,"to_mat":to_mat_flag,"slot":slot_idx,"paid":paid,"mat_pts":c.mat_points})
    # simple effects
    apply_effect(g,pid,c)
    return True

def act_buy_pool(g:Game,pid:int,pool_idx:int)->bool:
    p=g.players[pid]
    if not (0<=pool_idx<len(g.pool)): return False
    cid=g.pool[pool_idx]; c=g.cards[cid]
    if p.res["plasma"]<c.buy_cost_plasma: return False
    p.res["plasma"]-=c.buy_cost_plasma; p.discard.append(cid)
    p.owned[cid]=p.owned.get(cid,0)+1
    g.emit({"t":g.turn,"a":"buy","p":pid,"cid":cid,"name":c.name})
    # refill
    if g.supply: g.pool[pool_idx]=g.supply.pop()
    else: g.pool.pop(pool_idx)
    return True

def place_worker(g:Game,pid:int,field:str)->bool:
    p=g.players[pid]
    if p.workers_available<=0 or field not in BASIC_FIELDS: return False
    if field=="plasma": p.res["plasma"]+=1
    elif field=="ash": p.res["ash"]+=g.ash_pile; g.ash_pile=1
    elif field=="shards": p.res["shards"]+=g.shards_pile; g.shards_pile=1
    elif field=="forage": p.res[random.choice(["nut","berry","mushroom"])] += 1
    elif field=="rookery":
        # heuristic: if <2 affordable cards, try replace; else take
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
        # remove a random non-VP if possible
        i=None
        for j,tok in enumerate(p.hand):
            if not tok.startswith("VP:"): i=j; break
        if i is None and p.hand: i=0
        if i is not None:
            removed=p.hand.pop(i); g.emit({"t":g.turn,"a":"compost","p":pid,"card":removed})
    p.workers_available-=1
    g.emit({"t":g.turn,"a":"place_worker","p":pid,"field":field})
    return True

# ----- Effects (subset) -----
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

# ----- Bot (greedy + optional MCTS) -----
def engine_strength(p:Player)->float:
    # crude proxy: plasma + 0.5*ash + 0.5*shards + 0.3*hand_size + 0.8*mat_cards
    return p.res["plasma"] + 0.5*(p.res["ash"]+p.res["shards"]) + 0.3*len(p.hand) + 0.8*len(p.mat)

def card_score_for_pool(g:Game,p:Player,c:Card)->float:
    score = 0.0
    # affordability
    if p.res["plasma"]>=c.buy_cost_plasma: score += 1.0
    # synergy
    types_on_mat=set(g.cards[ms.cid].type_ for ms in p.mat)
    domains_on_mat=set(g.cards[ms.cid].domain for ms in p.mat)
    if c.type_ in types_on_mat: score += 1.5
    if c.domain in domains_on_mat: score += 1.5
    # text signals
    if "draw" in c.effect: score += 1.0
    if "on_mat" in c.effect or c.mat_points>0: score += 1.2
    return score

def choose_slot_for(p:Player,c:Card)->int:
    # prioritize matching discount slots
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
    # play actions (hand_idx, to_mat, slot)
    for i,tok in enumerate(p.hand):
        if tok.startswith("RES:") or tok.startswith("VP:"):
            acts.append(("play",(i,False,0)))
        elif tok in g.cards:
            c=g.cards[tok]
            if can_pay(p,c):
                # add both active and mat options if room
                acts.append(("play",(i,False,0)))
                if c.can_play_on_mat and len(p.mat)<6:
                    s=choose_slot_for(p,c)
                    if s>0: acts.append(("play",(i,True,s)))
    # buyables
    for idx,cid in enumerate(g.pool):
        if p.res["plasma"]>=g.cards[cid].buy_cost_plasma:
            acts.append(("buy_pool",idx))
    # place worker
    if p.workers_available>0:
        # prefer ash/shards listed first
        for f in ["ash","shards","plasma","forage","rookery","compost"]:
            acts.append(("place_worker",f))
    return acts

def greedy_value(g:Game,pid:int)->float:
    # simple value: VP + 0.2*engine_strength
    p=g.players[pid]
    return p.vp + 0.2*engine_strength(p)

def greedy_choose(g:Game,pid:int)->Tuple[str,Any]:
    acts=legal_actions(g,pid)
    best=None; bestv=-1e9
    for a,arg in acts:
        # heuristic score per action
        v = -1.0  # base
        if a=="play":
            i,to_mat,s = arg
            tok=g.players[pid].hand[i]
            if tok.startswith("VP:"):
                # play VP only if engine strong
                vp=int(tok.split(":")[1])
                v = 2.5*vp if engine_strength(g.players[pid])>=vp else 0.1*vp
                if to_mat: v -= 0.2
            elif tok.startswith("RES:"):
                v = 0.8
            else:
                c=g.cards[tok]; v = 1.0
                if to_mat: v += 0.8 if (c.mat_points>0 or "on_mat" in c.effect) else 0.2
        elif a=="buy_pool":
            cid=g.pool[arg]; c=g.cards[cid]; v = card_score_for_pool(g,g.players[pid],c)
        elif a=="place_worker":
            f=arg; v = {"ash":2.0,"shards":2.0,"plasma":1.0,"forage":1.0,"rookery":1.2,"compost":0.5}.get(f,0.5)
        elif a=="pass":
            v=0.0
        if v>bestv: best=(a,arg); bestv=v
    return best

def apply_action(g:Game, pid:int, action:Tuple[str,Any]):
    a,arg=action
    if a=="play":
        i,to_mat,s=arg; act_play(g,pid,i,to_mat,s)
    elif a=="buy_pool":
        act_buy_pool(g,pid,arg)
    elif a=="place_worker":
        place_worker(g,pid,arg)
    # pass -> do nothing

def step_turn(g:Game):
    pid=g.current
    # two micro-actions
    for _ in range(2):
        if g.cfg.mcts:
            act = mcts_choose(g, pid, g.cfg.rollouts, g.cfg.horizon)
        else:
            act = greedy_choose(g, pid)
        if act[0]=="pass": break
        apply_action(g, pid, act)
    # next player / round handling
    g.current=(g.current+1)%len(g.players)
    if g.current==0:
        g.turn+=1
        # unclaimed accumulators grow
        g.ash_pile+=1; g.shards_pile+=1
        for p in g.players:
            p.workers_available=3
        # round start drip
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

# ----- MCTS-lite (rollout with greedy policy) -----
def clone(g:Game)->Game:
    return copy.deepcopy(g)

def rollout(g:Game, pid:int, horizon:int)->float:
    steps=0
    while steps<horizon and not is_terminal(g):
        act=greedy_choose(g, g.current)
        apply_action(g, g.current, act)
        g.current=(g.current+1)%len(g.players)
        if g.current==0:
            g.turn+=1
    # return value for the *evaluated player*
    return g.players[pid].vp + 0.2*engine_strength(g.players[pid])

def candidate_actions(g:Game, pid:int)->List[Tuple[str,Any]]:
    # prune to top-K by greedy score to keep things fast
    acts=legal_actions(g,pid)
    scored=[]
    for a,arg in acts:
        # quick score
        if a=="play":
            i,to_mat,s=arg; tok=g.players[pid].hand[i]
            if tok.startswith("VP:"): v=2.0
            elif tok.startswith("RES:"): v=1.0
            else: v=1.5
        elif a=="buy_pool": v=1.2
        elif a=="place_worker": v=1.0
        else: v=0.1
        scored.append((v,(a,arg)))
    scored.sort(reverse=True)
    return [aa for _,aa in scored[:6]]  # top-6

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

def play_one(cfg:Config)->Dict[str,Any]:
    g=setup(cfg)
    while not is_terminal(g):
        step_turn(g)
    g.ended=True; g.winner=winner_id(g)
    # finalize log with end + mat durations
    for p in g.players:
        for ms in p.mat:
            duration = (g.turn - ms.placed_turn) + 1  # inclusive
            g.emit({"t":g.turn,"a":"mat_duration","p":p.id,"cid":ms.cid,"slot":ms.slot_index,"duration":duration})
    g.emit({"t":g.turn,"a":"end","winner":g.winner})
    return {"winner":g.winner,"turn":g.turn,"log":g.log,"players":g.players}

def run_many(games:int=20, seed:int=42, **kwargs)->Dict[str,Any]:
    rng=random.Random(seed)
    outs=[]
    for _ in range(games):
        cfg=Config(seed=rng.randrange(1_000_000), **kwargs)
        outs.append(play_one(cfg))
    # write logs
    os.makedirs("/mnt/data/logs", exist_ok=True)
    log_files=[]
    for i,out in enumerate(outs):
        path=f"/mnt/data/logs/game_v3_{seed}_{i}.jsonl"
        with open(path,"w",encoding="utf-8") as f:
            for e in out["log"]:
                f.write(json.dumps(e)+"\n")
        log_files.append(os.path.basename(path))
    # summary
    summary_path=f"/mnt/data/summaries/summary_v3_{seed}.csv"
    build_summary(outs, summary_path)
    # median turns
    med = median([o["turn"] for o in outs]) if outs else None
    return {"games":games,"median_turns":med,"winner_counts":{str(o['winner']):sum(1 for x in outs if x['winner']==o['winner']) for o in outs},"logs":log_files,"summary":os.path.basename(summary_path)}

def turn_bucket(t:int)->str:
    if t<=5: return "early"
    if t<=10: return "mid"
    return "late"

def build_summary(outs:List[Dict[str,Any]], out_csv:str):
    # per-card aggregations
    by_card = {}
    # helper maps
    owned_games = {}    # (card, game_index) -> bool
    time_to_first = {}  # (card) -> list of first-play turn
    to_mat_counts = {}  # card -> plays to mat
    play_counts = {}    # card -> total plays
    mat_duration = {}   # card -> list of durations
    slot_usage = {}     # card -> {slot:count}
    buys = {}           # card -> total buys
    buy_turn_bucket = {} # (card,bucket) -> count
    play_turn_bucket = {}# (card,bucket) -> count
    wins_when_owned = {} # card -> wins
    games_owned = {}     # card -> games where owned

    for gi,out in enumerate(outs):
        winner = out["winner"]
        # ownership
        # We infer ownership by counting buys from the log
        owned_by_player = {pid:set() for pid in range(len(out["players"]))}
        for e in out["log"]:
            if e["a"]=="buy":
                cid=e["cid"]; pid=e["p"]
                owned_by_player[pid].add(cid)
                buys[cid]=buys.get(cid,0)+1
                bt = turn_bucket(e["t"])
                buy_turn_bucket[(cid,bt)] = buy_turn_bucket.get((cid,bt),0)+1
        # plays + to_mat + time-to-first
        seen_first = set()
        for e in out["log"]:
            if e["a"]=="play_card":
                cid=e["cid"]; pid=e["p"]
                play_counts[cid]=play_counts.get(cid,0)+1
                if e.get("to_mat"): to_mat_counts[cid]=to_mat_counts.get(cid,0)+1
                ptb = turn_bucket(e["t"]); play_turn_bucket[(cid,ptb)] = play_turn_bucket.get((cid,ptb),0)+1
                if cid not in seen_first:
                    seen_first.add(cid)
                    time_to_first.setdefault(cid,[]).append(e["t"])
            if e["a"]=="mat_duration":
                cid=e["cid"]; mat_duration.setdefault(cid,[]).append(e["duration"])
                slot_usage.setdefault(cid,{i:0 for i in range(1,7)})
                slot_usage[cid][e["slot"]] += 1
        # wins when owned
        for pid, owned_set in owned_by_player.items():
            for cid in owned_set:
                games_owned[cid]=games_owned.get(cid,0)+1
                if winner==pid:
                    wins_when_owned[cid]=wins_when_owned.get(cid,0)+1

    # build rows
    # collect all card ids encountered
    all_ids = set(list(buys.keys())+list(play_counts.keys())+list(slot_usage.keys()))
    rows=[]
    for cid in sorted(all_ids):
        bought = buys.get(cid,0)
        played = play_counts.get(cid,0)
        to_mat = to_mat_counts.get(cid,0)
        ttf_list = time_to_first.get(cid,[])
        ttf_median = (median(ttf_list) if ttf_list else None)
        md_list = mat_duration.get(cid,[])
        mat_avg = (sum(md_list)/len(md_list) if md_list else None)
        slots = slot_usage.get(cid,{i:0 for i in range(1,7)})
        go = games_owned.get(cid,0)
        wwo = wins_when_owned.get(cid,0)
        wr = (wwo/go) if go else None
        row = {
            "card_id": cid,
            "bought": bought,
            "played": played,
            "to_mat_plays": to_mat,
            "to_mat_rate": (to_mat/played) if played else None,
            "time_to_first_play_median_turn": ttf_median,
            "avg_mat_duration_turns": mat_avg,
            "slot1_plays": slots.get(1,0),
            "slot2_plays": slots.get(2,0),
            "slot3_plays": slots.get(3,0),
            "slot4_plays": slots.get(4,0),
            "slot5_plays": slots.get(5,0),
            "slot6_plays": slots.get(6,0),
            "buy_early": buy_turn_bucket.get((cid,"early"),0),
            "buy_mid": buy_turn_bucket.get((cid,"mid"),0),
            "buy_late": buy_turn_bucket.get((cid,"late"),0),
            "play_early": play_turn_bucket.get((cid,"early"),0),
            "play_mid": play_turn_bucket.get((cid,"mid"),0),
            "play_late": play_turn_bucket.get((cid,"late"),0),
            "games_owned": go,
            "wins_when_owned": wwo,
            "winrate_when_owned": wr,
        }
        rows.append(row)
    import csv as _csv
    with open(out_csv,"w",newline="",encoding="utf-8") as f:
        w=_csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["card_id"])
        w.writeheader()
        for r in rows: w.writerow(r)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--seed", type=int, default=999)
    ap.add_argument("--mcts", type=int, default=1)
    ap.add_argument("--rollouts", type=int, default=6)
    ap.add_argument("--horizon", type=int, default=3)
    args=ap.parse_args()
    summary = run_many(args.games, args.seed, mcts=args.mcts, rollouts=args.rollouts, horizon=args.horizon)
    print(json.dumps(summary, indent=2))
