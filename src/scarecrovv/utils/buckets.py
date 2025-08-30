def buy_bucket(turn:int):
    if turn<=5: return "early"
    if turn<=10: return "mid"
    return "late"
