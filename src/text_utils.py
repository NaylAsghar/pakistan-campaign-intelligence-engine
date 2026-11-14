"""Shared helpers for Project 3: text cleaning, language detection, topics, intent, sentiment words.
All the word lists are plain Python lists. Editing them is allowed (and good for your report):
add words you see in your own comments, rerun label_comments.py, and compare.
"""
import re

# ------------------------------------------------------------------ cleaning
URL_RE = re.compile(r"https?://\S+|www\.\S+")
HANDLE_RE = re.compile(r"@\w+")
EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?92|0)?[\s-]?3\d{2}[\s-]?\d{7}(?!\d)")
URDU_SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")


def mask_private(text):
    """Remove personal details (emails, phone numbers, @handles) before analysis."""
    text = EMAIL_RE.sub(" [email] ", str(text))
    text = PHONE_RE.sub(" [phone] ", text)
    return HANDLE_RE.sub(" @user ", text)


def clean_text(text):
    """Lowercase, remove links, punctuation and emojis. Keeps letters, digits and Urdu script."""
    text = URL_RE.sub(" ", str(text).lower())
    text = re.sub(r"[^\w\s]|_", " ", text)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)          # soooo -> soo
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------ language
ROMAN_URDU_WORDS = {
    "hai", "hain", "hy", "tha", "thi", "thay", "thy", "hun", "hoon", "hn", "kar", "ka", "ki", "ke", "ko",
    "se", "sy", "mein", "nahi", "nahin", "nhi", "ni", "aur", "tum", "tu", "tera", "teri", "mera", "meri",
    "yeh", "ye", "woh", "wo", "dil", "pyar", "pyara", "pyari", "mohabbat", "ishq", "zindagi", "gaana",
    "gana", "bohat", "bahut", "bohot", "boht", "bht", "kya", "kuch", "abhi", "phir", "jab", "tab", "sab",
    "koi", "lekin", "magar", "acha", "accha", "achha", "achi", "bhai", "yaar", "yar", "dost", "awaz",
    "awaaz", "aawaz", "sunta", "sunti", "suna", "yaad", "raat", "waqt", "purane", "wah", "waah",
    "zabardast", "kamaal", "lajawab", "behtareen", "hamesha", "aaj", "kal", "rha", "raha", "rahi", "gaya",
    "gayi", "lagta", "lagti", "tumhari", "apni", "apna", "dua", "dunya", "mashallah", "bachpan", "aansu",
    "dard", "sirf", "bilkul", "mujhe", "mjh", "tujhe", "humein", "hamari", "kabhi", "wapas", "khubsurat",
    "kahan", "kaha", "kab", "kb", "bta", "bata", "batao", "btao", "dein", "den", "krna", "krne", "karna",
    "karne", "wali", "wala", "jaye", "apko", "apka", "aap", "kesy", "kesay", "kaisay", "kaise", "kese",
    "chahiye", "chahye", "chaiye", "khareed", "mangwa", "kitne", "kitni", "kitna", "mehnga", "mehngi",
    "sasta", "sasti", "paisay", "paise", "qeemat", "shukriya", "yahan", "wahan", "bhi", "sath", "saath",
    "ha", "muja", "lana", "laina", "naam", "milega", "milegi", "sakta", "sakti", "lage", "laga", "lagi",
    "hamara", "hmara", "dain", "kia", "kiya", "kyun", "mei", "bhot", "sai", "sahi", "hen", "hein", "ajayn",
    "kapry", "kapde", "ganey", "ganay", "gane", "gaane", "dekhni", "suney", "mil", "dena", "dedo", "yaaar",
}
AMBIGUOUS = {"ka", "ki", "ke", "ko", "se", "den", "dein"}   # also real English / very short: weak evidence


def detect_language(text):
    """'Urdu script', 'Roman Urdu', 'English' or 'Emoji/Other'."""
    letters = [c for c in str(text) if c.isalpha()]
    if not letters:
        return "Emoji/Other"
    urdu = sum(1 for c in letters if URDU_SCRIPT_RE.match(c))
    if urdu / len(letters) > 0.3:
        return "Urdu script"
    if sum(1 for c in letters if c.isascii()) / len(letters) < 0.5:
        return "Emoji/Other"
    tokens = clean_text(text).split()
    strong = sum(1 for t in tokens if t in ROMAN_URDU_WORDS and t not in AMBIGUOUS)
    weak = sum(1 for t in tokens if t in AMBIGUOUS)
    if strong >= 2 or (strong >= 1 and (len(tokens) <= 4 or weak >= 1)) or weak >= 3:
        return "Roman Urdu"
    return "English"


# ------------------------------------------------------------------ topics
# Single words must match a whole word; entries with spaces match as a phrase.
TOPICS = {
    "delivery_service": ["delivery", "deliver", "delivered", "shipping", "ship", "courier", "parcel",
        "tracking", "dispatch", "dispatched", "refund", "return", "returns", "exchange", "customer service",
        "customer care", "no reply", "not received", "never received", "helpline", "international delivery",
        "cash on delivery", "cod", "order status", "my order", "complaint", "complain", "dlivry", "delivry",
        "charges", "delivery charges", "payment", "ڈیلیوری"],
    "sizing": ["size", "sizes", "sizing", "fitting", "xl", "xxl", "xs", "plus size", "full length", "naap",
        "سائز"],
    "price": ["price", "prices", "pricing", "cost", "costly", "expensive", "mehnga", "mehngi", "mehnge",
        "mehenga", "rate", "rates", "kitne ka", "kitni ka", "kitna ka", "kitne ki", "paisay", "paise",
        "rs", "pkr", "rupees", "afford", "affordable", "cheap", "sasta", "sasti", "qeemat", "قیمت", "مہنگا",
        "overpriced"],
    "discount_offers": ["sale", "discount", "discounts", "offer", "offers", "voucher", "promo", "coupon",
        "giveaway", "give away", "clearance", "flat off", "off", "free delivery", "free shipping",
        "buy one get one", "bogo", "sale kab", "sale kb", "sell kb", "sell kab", "sale lage", "sell lage",
        "sale lagi", "sell lagi", "ڈسکاؤنٹ"],
    "availability": ["available", "availability", "stock", "in stock", "out of stock", "sold out", "restock",
        "store", "stores", "outlet", "branch", "branches", "shop", "shops", "online", "website",
        "where to buy", "where can i buy", "where can i get", "launch", "launching", "launched", "when will",
        "link", "where can i", "kahan se", "kahan milay", "milega", "milegi", "mil jaye", "kahan"],
    "fabric_quality": ["quality", "fabric", "fabrics", "material", "stitching", "stitched", "cotton", "silk",
        "viscose", "khaddar", "linen", "chiffon", "cambric", "karandi", "organza", "jacquard", "velvet",
        "fade", "fades", "faded", "shrink", "shrinks", "thread", "lining", "کوالٹی"],
    "complaint_criticism": ["bakwas", "boring", "worst", "waste", "fake", "disappointed", "disappointing",
        "poor", "scam", "overrated", "annoying", "cringe", "ghalat", "kharab", "ganda", "jhoot", "hate",
        "bad", "unprofessional", "rude", "zulm", "useless", "pathetic", "ghatya", "vahiyat", "foolish",
        "stupid", "dhoka", "dhokha", "dhoke", "gnda", "cheat", "fraud", "nonsense", "irritating", "rubbish", "trash", "بکواس"],
    "music_song": ["song", "songs", "music", "singer", "voice", "lyrics", "track", "soundtrack", "sound",
        "gana", "gaana", "awaz", "awaaz", "aawaz", "vocal", "vocals", "composition", "bgm", "ost", "tune",
        "melody", "song name", "full song", "full version", "background music", "singers", "ganey", "ganay",
        "gane", "gaane", "ka naam", "name plz", "singer name", "آواز", "گانا"],
    "celebrity_model": ["model", "models", "actress", "actor", "heroine", "queen", "celebrity", "ambassador",
        "yumna", "zaidi", "ayeza", "aiza", "iqra", "aziz", "mahira", "hania", "sajal", "shuja", "haider",
        "ramsha", "nehaal", "naseem", "saba", "kubra", "mawra", "kinza", "zara", "sana javed"],
    "brand_loyalty": ["brand", "brands", "favourite brand", "favorite brand", "all time favourite",
        "all time favorite", "always wear", "always buy", "loyal", "trusted", "trust"],
    "design_style": ["design", "designs", "print", "prints", "colour", "colours", "color", "colors",
        "pattern", "embroidery", "dress", "dresses", "outfit", "outfits", "collection", "look", "looks",
        "style", "styling", "shades", "clothes", "suit", "suits", "kapra", "kapray", "kapre", "kaprey", "kapry",
        "kapde", "کپڑا"],
    "content_request": ["bts", "behind the scenes", "upload", "part 2", "part two", "full video",
        "complete part", "more videos", "making of", "interview", "bloopers", "shoot", "teaser", "trailer"],
    "chit_chat": ["first", "frist", "1st", "2nd", "hello", "hi", "hey", "salam", "assalam", "aoa", "early",
        "here", "watching", "subscribe", "follow", "bangladesh", "india", "pakistan", "nepal", "uk", "usa",
        "canada", "dubai", "lahore", "karachi", "islamabad", "love from", "big fan", "fan"],
    "general_praise": ["beautiful", "nice", "amazing", "awesome", "love", "loved", "lovely", "gorgeous",
        "great", "best", "wow", "woww", "waoo", "superb", "excellent", "mashallah", "masha allah", "zabardast",
        "kamaal", "khubsurat", "pyari", "perfect", "stunning", "fantastic", "good", "gud", "nyc", "osm",
        "well done", "good job"],
}
PRIORITY = list(TOPICS)       # commerce topics first; used to break ties

SPAM_WORDS = {"crypto", "bitcoin", "forex", "trading", "invest", "investing", "investment", "telegram",
              "binance", "loan", "casino", "betting", "manufacturer", "supplier", "kamao", "silica", "سکس"}
SPAM_PHRASES = ["promote your brand", "sponsored video", "paisa kamao", "earn money", "make money",
                "work from home"]


AVAIL_AMBIGUOUS = {"link", "where can i", "kahan se", "kahan milay", "milega", "milegi", "mil jaye", "kahan"}


def topic_hits(text_clean):
    padded = f" {text_clean} "
    tokens = set(text_clean.split())
    found = {t: [w for w in words if (" " in w and f" {w} " in padded) or (" " not in w and w in tokens)]
             for t, words in TOPICS.items()}
    if not found["music_song"] and any(t.startswith("song") for t in tokens):
        found["music_song"] = ["song"]
    if found["music_song"]:        # "link of this song" / "where can I find this song" is about the song
        found["availability"] = [w for w in found["availability"] if w not in AVAIL_AMBIGUOUS]
    return {t: len(ws) for t, ws in found.items()}


def is_spam(text_clean):
    n = len(text_clean.split())
    padded = f" {text_clean} "
    word_hit = bool(set(text_clean.split()) & SPAM_WORDS) and (n >= 5 or "سکس" in text_clean)
    return word_hit or any(f" {p} " in padded for p in SPAM_PHRASES)


def primary_topic(hits, has_letters, text_clean=""):
    if not has_letters:
        return "emoji_only"
    best = max(hits.values())
    if best == 0:
        return "noise" if len(text_clean) <= 4 else "other"
    return next(t for t in PRIORITY if hits[t] == best)


# ------------------------------------------------------------------ purchase intent
HIGH_INTENT = re.compile(
    r"\b(want|wanna|need|like|looking|wish)\s+(to\s+)?(buy|order|purchase|get)\b|"
    r"\bhow\s+(to|can i|do i|may i)\s+(order|buy|purchase|get)\b|"
    r"\bwhere\s+(can i|to|do i|could i)\s+(buy|get|find|order|purchase)\b|"
    r"\b(kaise|kese|kaisay|kesay)\s+(order|buy|mangwa|khareed|mil)|"
    r"\bkhareed|\blena\s+(hai|hy|he|chahta|chahti|chahte)\b|\blene\s+(hain|hai|hy)\b|"
    r"\b(chahiye|chahye|chaiye|chahiey)\b|\b(order|buy)\s+(krna|karna|kr|kar|krne|karne)\b|"
    r"\b(inbox|dm|whatsapp)\b|\bcontact\s+(number|no)\b|\bnumber\s+(dein|do|den|plz|please)\b|"
    r"\bi\s+want\s+(this|these|it)\b|\bwant\s+(this|these)\b")
MEDIUM_INTENT = re.compile(
    r"\bprice\b|\bprices\b|\brate\b|kitne ka|kitni ka|\bkitna\b|\bavailable\b|\bavailability\b|\blink\b|"
    r"\bsale\b|\bdiscount\b|\bin stock\b|\bstock\b|\bwhere\b.*\b(dress|dresses|suit|suits|lawn)\b|"
    r"\bcost\b|\bwebsite\b|\bonline\b|\bstore\b|\bbranch\b|\bshop\b")


COMMERCE_WORDS = re.compile(r"\b(buy|order|purchase|khareed|price|dress|dresses|suit|suits|lawn|collection|"
                            r"stitched|unstitched)\b")


def purchase_intent(text_clean, about_music=False):
    if about_music and not COMMERCE_WORDS.search(text_clean):
        return "none"                      # "we want this song" is not a buying signal
    if HIGH_INTENT.search(text_clean):
        return "high"
    if MEDIUM_INTENT.search(text_clean):
        return "medium"
    return "none"


# ------------------------------------------------------------------ sentiment words
SENTIMENT_EXTRAS = {
    "zabardast": 3.0, "kamaal": 2.8, "lajawab": 3.0, "behtareen": 3.0, "umda": 2.5, "shandar": 2.8,
    "mashallah": 2.5, "waah": 2.5, "wah": 2.0, "pyara": 2.5, "pyari": 2.5, "khubsurat": 2.8,
    "acha": 2.0, "accha": 2.0, "achi": 2.0, "legend": 2.5, "goat": 2.5, "masterpiece": 3.0,
    "evergreen": 2.5, "iconic": 2.5, "magical": 2.5, "fire": 2.0, "craze": 2.0, "crazy": 2.0,
    "gold": 2.0, "osum": 2.5, "osm": 2.5, "awsm": 2.5, "gud": 1.9, "nyc": 1.8, "gr8": 2.0,
    "melodious": 2.5, "soulful": 2.5, "goosebumps": 2.5, "favourite": 2.0, "unforgettable": 2.0,
    "زبردست": 3.0, "کمال": 2.8, "بہترین": 3.0, "لاجواب": 3.0, "واہ": 2.5, "ماشاءاللہ": 2.5, "شاندار": 2.8,
    "bakwas": -3.0, "bekar": -2.5, "faltu": -2.5, "ganda": -2.5, "bura": -2.0, "afsos": -2.0,
    "kharab": -2.5, "ghalat": -2.0, "jhoot": -2.0, "zulm": -2.5, "بکواس": -3.0, "بیکار": -2.5,
}
# In music/fashion comments these words describe a mood, not dissatisfaction.
NEUTRALIZED_WORDS = ["sad", "cry", "crying", "tears", "pain", "heartbroken", "heartbreak", "miss",
                     "missing", "alone", "lonely", "hurt", "hurts", "broken", "lost", "badly"]
SAD_EMOJIS = ("\U0001F622\U0001F62D\U0001F613\U0001F614\U0001F61E\U0001F625\U0001F630\U0001F97A"
              "\U0001F63F\U0001F494\U0001F629\U0001F62B\U0001F979\U0001F62A")
