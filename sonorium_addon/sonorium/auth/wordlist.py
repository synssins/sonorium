"""
256 short words for the security words: the first four bytes of the HTTPS
certificate's SHA-256 fingerprint, one word each (WORDS[byte]). The app
uses the same list, in the same order, to show the same four words.
"""

WORDS = (
    "acorn", "actor", "adult", "agent", "alarm", "album", "alley", "amber",
    "angle", "ankle", "apple", "apron", "arrow", "atlas", "attic", "award",
    "bacon", "badge", "bagel", "baker", "banjo", "barn", "basil", "beach",
    "beard", "bench", "berry", "bike", "birch", "bison", "blade", "blank",
    "bloom", "board", "boat", "bonus", "boot", "bread", "brick", "bride",
    "brook", "brush", "bucket", "buddy", "bugle", "cabin", "cable", "cactus",
    "camel", "candy", "canoe", "cargo", "carpet", "castle", "cedar", "chair",
    "chalk", "cheese", "cherry", "chess", "chief", "cider", "cliff", "clock",
    "cloud", "clover", "coach", "coast", "cobra", "comet", "coral", "cotton",
    "couch", "crane", "crown", "cube", "daisy", "dance", "delta", "denim",
    "desk", "diary", "dingo", "disco", "dock", "dolphin", "donut", "dragon",
    "drum", "duck", "eagle", "earth", "easel", "echo", "elbow", "elder",
    "ember", "engine", "fable", "falcon", "fancy", "farm", "feast", "fence",
    "ferry", "fiddle", "field", "flag", "flame", "flute", "focus", "forest",
    "fossil", "fox", "frost", "fruit", "gadget", "garden", "garlic", "gecko",
    "giant", "ginger", "glass", "globe", "glove", "goose", "grape", "gravy",
    "guitar", "hammer", "harbor", "harp", "hazel", "heron", "hiker", "honey",
    "hornet", "hotel", "igloo", "inlet", "iris", "island", "ivory", "jacket",
    "jaguar", "jelly", "jewel", "jockey", "judge", "juice", "jungle", "kayak",
    "kettle", "kiosk", "kitten", "koala", "ladder", "lagoon", "lamp", "lemon",
    "lilac", "lion", "lizard", "llama", "lobster", "locket", "lotus", "lunar",
    "magnet", "mango", "maple", "marble", "meadow", "melon", "mint", "mirror",
    "mitten", "monkey", "moose", "mosaic", "motor", "muffin", "napkin", "nectar",
    "needle", "nickel", "noodle", "nutmeg", "oasis", "ocean", "olive", "onion",
    "opera", "orbit", "orchid", "otter", "owl", "oyster", "paddle", "panda",
    "parrot", "peach", "pebble", "pencil", "pepper", "piano", "pickle", "pilot",
    "pine", "planet", "plum", "pony", "poppy", "pretzel", "puffin", "pumpkin",
    "quail", "quartz", "quilt", "rabbit", "radar", "radish", "raven", "reef",
    "ribbon", "river", "robin", "rocket", "rose", "ruby", "saddle", "salmon",
    "sandal", "scarf", "shell", "silver", "sketch", "sled", "spoon", "squid",
    "stone", "sugar", "summit", "tango", "teapot", "tiger", "tomato", "torch",
    "tulip", "turtle", "velvet", "violin", "walnut", "whale", "willow", "zebra",
)

assert len(WORDS) == 256 and len(set(WORDS)) == 256


def security_words(fingerprint_hex: str) -> list[str]:
    """Four words from the first four bytes of a SHA-256 fingerprint (hex)."""
    raw = bytes.fromhex(fingerprint_hex.replace(":", ""))
    return [WORDS[b] for b in raw[:4]]
