import collections
import random

def build_chains(wearings, order=3):
    chains = collections.defaultdict(list)
    for e in xrange(0, len(wearings) - order - 1):
        context = tuple(w.shirt for w in wearings[e:e + order])
        chains[context].append(wearings[e + order].shirt)
    return chains

def expochoice(seq):
    lambd = 2. / len(seq)
    while True:
        index = int(random.expovariate(lambd))
        if index < len(seq):
            return seq[index]

def pick_epsilon(chains, previous, preferred_epsilon):
    previous = tuple(previous)
    options = [s for s in preferred_epsilon if previous + (s,) in chains]
    if not options:
        options = preferred_epsilon
    ret = expochoice(options)
    preferred_epsilon.remove(ret)
    return ret

def suggest_next(wearings, count, preferred_epsilon, order=3):
    preferred_epsilon = list(preferred_epsilon)
    chains = build_chains(wearings, order)
    ret = [w.shirt for w in wearings[-order:]]
    for x in xrange(count):
        epsilon = pick_epsilon(chains, ret[-order + 1:], preferred_epsilon)
        choices = chains[tuple(ret[-order:])] + [epsilon]
        ret.append(random.choice(choices))
    return ret[order:]
