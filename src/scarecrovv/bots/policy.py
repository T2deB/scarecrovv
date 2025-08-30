import random

def pick_random(actions, rng:random.Random):
    return rng.choice(actions) if actions else ("pass", None)
