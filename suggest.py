import collections
import random

def build_chains(wearings, order=3):
    chains = collections.defaultdict(list)
    for e in xrange(0, len(wearings) - order - 1):
        context = tuple(w.shirt for w in wearings[e:e + order])
        chains[context].append(wearings[e + order].shirt)
    return chains

def expochoice(seq):
    lambd = 4. / len(seq)
    while True:
        index = int(random.expovariate(lambd))
        if index < len(seq):
            return seq[index]

def pick_epsilon(chains, previous, preferred_epsilon):
    previous = tuple(previous)
    # preserving order (since list sorting is stable), rearrange
    # preferred_epsilon so that the options that are in chains end up first
    # (and therefore are more likely to be chosen)
    options = sorted(preferred_epsilon, key=lambda s: previous + (s,) not in chains)
    ret = expochoice(options)
    preferred_epsilon.remove(ret)
    return ret

def suggest_next(wearings, count, preferred_epsilon, order=3):
    preferred_epsilon = list(preferred_epsilon)
    chains = build_chains(wearings, order)
    shirts = [w.shirt for w in wearings[-order:]]
    ret = []
    for x in xrange(count):
        epsilon = pick_epsilon(chains, shirts[-order + 1:], preferred_epsilon)
        choices = chains[tuple(shirts[-order:])] + [epsilon]
        choice = random.choice(choices)
        shirts.append(choice)
        ret.append((choice, len(choices), choice is epsilon))
    return ret
