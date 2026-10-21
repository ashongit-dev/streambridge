"""Pure routing policy, independent of the encoder and network."""
def choose_source(preferred, automatic, healthy):
    if preferred == "fallback":
        return "fallback"
    if healthy.get(preferred, False):
        return preferred
    other = "irl" if preferred == "desk" else "desk"
    return other if automatic and healthy.get(other, False) else "fallback"
