"""Normalize and polish text extracted from thesis PDF cover pages."""

from __future__ import annotations

import re
import unicodedata

import wordninja

from thesis2lib.utils import clean_text

SMALL_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "using",
        "vs",
        "with",
    }
)

DISPLAY_TERMS: dict[str, str] = {
    "ai": "AI",
    "ml": "ML",
    "dss": "DSS",
    "shap": "SHAP",
    "smote": "SMOTE",
    "xgboost": "XGBoost",
    "tabpfn": "TabPFN",
    "tabnet": "TabNet",
    "gan": "GAN",
    "gans": "GANs",
    "nlp": "NLP",
    "rna": "RNA",
    "dna": "DNA",
    "roberta": "RoBERTa",
    "xlm-r": "XLM-R",
    "xlmr": "XLM-R",
    "eu": "EU",
    "liss": "LISS",
    "morf": "MORF",
    "lerf": "LERF",
    "topk": "Top-k",
    "3d": "3D",
    "usa": "USA",
    "us": "U.S.",
    "uk": "UK",
    "pdf": "PDF",
    "api": "API",
    "covid": "COVID",
    "covid-19": "COVID-19",
    "defi": "DeFi",
    "ph": "pH",
}

COMPOUND_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bXG\s+BOOST\b", re.I), "XGBoost"),
    (re.compile(r"\bTAB\s+PF\s+N\b", re.I), "TabPFN"),
    (re.compile(r"\bTAB\s+NET\b", re.I), "TabNet"),
    (re.compile(r"\bEXPLAIN\s+ABILITY\b", re.I), "Explainability"),
    (re.compile(r"\bINTERPRET\s+ABILITY\b", re.I), "Interpretability"),
    (re.compile(r"\bINTERPRET\s+ABLE\b", re.I), "Interpretable"),
    (re.compile(r"\bXL\s+M\s+R\b", re.I), "XLM-R"),
    (re.compile(r"\bXLMR\b", re.I), "XLM-R"),
    (re.compile(r"\bROBERTA\b", re.I), "RoBERTa"),
    (re.compile(r"\bRE\s+SAMPLING\b", re.I), "Resampling"),
    (re.compile(r"\bCO\s+MORBID\b", re.I), "Comorbid"),
    (re.compile(r"\bAS\s+PATIO\s+TEMPORAL\b", re.I), "A Spatiotemporal"),
    (re.compile(r"\bA\s+SPATIO\s+TEMPORAL\b", re.I), "A Spatiotemporal"),
    (re.compile(r"\bSPATIO\s+TEMPORAL\b", re.I), "Spatiotemporal"),
    (re.compile(r"\bSPATIOTEMPORAL\b", re.I), "Spatiotemporal"),
    (re.compile(r"\bTIME\s+ON\s+MARKET\b", re.I), "Time-on-Market"),
    (re.compile(r"\bONE\s+YEAR\b", re.I), "One-Year"),
    (re.compile(r"\bEND\s+TO\s+END\b", re.I), "End-to-End"),
    (re.compile(r"\bWORK\s+LIFE\b", re.I), "Work-Life"),
    (re.compile(r"\bPOST\s+HOC\b", re.I), "Post-hoc"),
    (re.compile(r"\bPRIOR\s+FITTED\b", re.I), "Prior-Fitted"),
    (re.compile(r"\bTOP\s+K\b", re.I), "Top-k"),
    (re.compile(r"\bT\s+EP\s+ING\b", re.I), "TEP-ing"),
    (re.compile(r"\bPR\s+DIC\s+TING\b", re.I), "Predicting"),
    (re.compile(r"\bSMOTE\s+TOMEK\b", re.I), "SMOTE-Tomek"),
    (re.compile(r"\bSMOTE\s+EN\s+N\b", re.I), "SMOTE-ENN"),
    (re.compile(r"\bSMOTE\s+ENN\b", re.I), "SMOTE-ENN"),
    (re.compile(r"\bEUROPEAN\s+UNION\b", re.I), "European Union"),
    (re.compile(r"\bPEOPLE\s+'\s*S\b", re.I), "People's"),
    (re.compile(r"\bPEOPLE\s+S\b", re.I), "People's"),
    (re.compile(r"\bU\s+S\b(?!\w)", re.I), "U.S."),
    (re.compile(r"\bEXTREME\s+GRADIENT\s+BOOST(ING)?\b", re.I), "Extreme Gradient Boosting"),
    (re.compile(r"\bAUTO\s+INSURANCE\b", re.I), "Auto Insurance"),
    (re.compile(r"\bCREDIT\s+CARD\b", re.I), "Credit Card"),
    (re.compile(r"\bMACHINE\s+LEARNING\b", re.I), "Machine Learning"),
    (re.compile(r"\bDEEP\s+LEARNING\b", re.I), "Deep Learning"),
    (re.compile(r"\bC\s+Ovid\b", re.I), "COVID"),
    (re.compile(r"\bC\s+OVID\b", re.I), "COVID"),
    (re.compile(r"\bCovid\b", re.I), "COVID"),
    (re.compile(r"\bCross\s+Lingual\b", re.I), "Cross-Lingual"),
    (re.compile(r"\bLong\s+Term\b", re.I), "Long-Term"),
    (re.compile(r"\bTree\s+Based\b", re.I), "Tree-Based"),
    (re.compile(r"\bEmbedding\s+S\b", re.I), "Embeddings"),
    (re.compile(r"\bPh\s+Values\b", re.I), "pH Values"),
    (re.compile(r"\bIn\s+Defi\b", re.I), "in DeFi"),
    (re.compile(r"\bDefi\b", re.I), "DeFi"),
    (re.compile(r"\bMulti\s+Window\b", re.I), "Multi-Window"),
    (re.compile(r"\bGame\s+Chat\b", re.I), "Game Chat"),
    (re.compile(r"\bAccuracy\s+Efficiency\b", re.I), "Accuracy-Efficiency"),
    (re.compile(r"\bMarket\s+Odds\b", re.I), "Market Odds"),
    (re.compile(r"\bModel\s+Explainability\b", re.I), "Model Explainability"),
    (re.compile(r"\bTurnover\s+Intention\b", re.I), "Turnover Intention"),
    (re.compile(r"\bPerception\s+Based\b", re.I), "Perception-Based"),
    (re.compile(r"\bPublic\s+Sector\b", re.I), "Public Sector"),
    (re.compile(r"\bFootball\s+Match\b", re.I), "Football Match"),
    (re.compile(r"\bRandom\s+Forest\b", re.I), "Random Forest"),
    (re.compile(r"\bNeural\s+Networks\b", re.I), "Neural Networks"),
    (re.compile(r"\bStacking\s+Ensembles\b", re.I), "Stacking Ensembles"),
    (re.compile(r"\bVirtual\s+Screening\b", re.I), "Virtual Screening"),
    (re.compile(r"\bProtein\s+Wise\b", re.I), "Protein-Wise"),
    (re.compile(r"\bClassical\s+Models\b", re.I), "Classical Models"),
    (re.compile(r"\bHybrid\s+Machine\s+Learning\b", re.I), "Hybrid Machine Learning"),
    (re.compile(r"\bLarge\s+Language\s+Models\b", re.I), "Large Language Models"),
    (re.compile(r"\bSafety\s+Alignment\b", re.I), "Safety Alignment"),
    (re.compile(r"\bCoral\s+Reef\b", re.I), "Coral Reef"),
    (re.compile(r"\bRug\s+Pulls\b", re.I), "Rug Pulls"),
    (re.compile(r"\bLiquidity\s+Flow\b", re.I), "Liquidity Flow"),
    (re.compile(r"\bTransformer\s+Embeddings\b", re.I), "Transformer Embeddings"),
    (re.compile(r"\bMULTI\s+MODAL\b", re.I), "Multi-Modal"),
    (re.compile(r"\bCROSS\s+REGIONAL\b", re.I), "Cross-Regional"),
    (re.compile(r"\bFOLLOW\s+UP\b", re.I), "Follow-up"),
    (re.compile(r"\bHEALTH\s+CARE\b", re.I), "Healthcare"),
    (re.compile(r"\bFAIR\s+NESS\b", re.I), "Fairness"),
    (re.compile(r"\bACCUR\s+ACY\b", re.I), "Accuracy"),
)

ALLOWED_SHORT_WORDS = frozenset({"a", "an", "of", "in", "on", "to", "vs", "3d", "ai", "ml", "eu", "uk", "us"})

OCR_JUNK_FRAGMENT = re.compile(
    r"^(?:"
    r"idl|til|tshd|logo|"
    r"tilburg(?:\s+university)?|"
    r"university|"
    r"o\s*s\s*e|"
    r"humanities|"
    r"digital"
    r")$",
    re.I,
)


def normalize_unicode(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00ad": "",
        "\ufffd": "",
        "\u00a0": " ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def fix_compound_terms(text: str) -> str:
    result = normalize_unicode(text)
    result = re.sub(r"\s+'\s*", "'", result)
    for pattern, replacement in COMPOUND_FIXES:
        result = pattern.sub(replacement, result)
    result = re.sub(r"\s+-\s+", "-", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def looks_like_spaced_letters(text: str) -> bool:
    """True when OCR/PDF text has spaces between individual letters."""
    parts = text.split()
    if len(parts) < 4:
        return False
    single = sum(1 for part in parts if len(part) == 1 and part.isalpha())
    short = sum(1 for part in parts if len(part) <= 2 and part.isalpha())
    if single >= 5 and single / len(parts) >= 0.35:
        return True
    if len(parts) >= 8 and short / len(parts) >= 0.65:
        return True
    return False


def _collapse_spaced_letter_blob(text: str) -> str:
    collapsed = re.sub(r"\s*-\s*", "-", text)
    return re.sub(r"\s+", "", collapsed)


def _split_spaced_letter_blob(blob: str) -> str:
    return fix_compound_terms(" ".join(wordninja.split(blob)))


def denoise_spaced_text(text: str) -> str:
    """Collapse letter-spaced OCR text such as 'P R E D I C T I N G' into words."""
    text = normalize_unicode(clean_text(text))
    if not text:
        return ""
    if not looks_like_spaced_letters(text):
        return fix_compound_terms(re.sub(r"\s+", " ", text))

    if ":" in text:
        parts = [
            _split_spaced_letter_blob(_collapse_spaced_letter_blob(part))
            for part in re.split(r"\s*:\s*", text)
            if part.strip()
        ]
        return fix_compound_terms(": ".join(parts))

    blob = _collapse_spaced_letter_blob(text)
    return _split_spaced_letter_blob(blob)


def _is_mixed_case_word(word: str) -> bool:
    letters = [char for char in word if char.isalpha()]
    if not letters:
        return False
    return any(char.islower() for char in letters) and any(char.isupper() for char in letters)


def _display_word(word: str, index: int) -> str:
    stripped = word.strip(".,;:")
    if not stripped:
        return word

    if _is_mixed_case_word(stripped):
        return word

    lower = stripped.lower()
    if lower in DISPLAY_TERMS:
        return _restore_edge_punctuation(word, DISPLAY_TERMS[lower])

    if index > 0 and lower in SMALL_WORDS:
        return _restore_edge_punctuation(word, lower)

    if "-" in stripped:
        parts = stripped.split("-")
        rebuilt = "-".join(_display_word(part, index) for part in parts)
        return _restore_edge_punctuation(word, rebuilt)

    if stripped.isupper() or stripped.islower():
        return _restore_edge_punctuation(word, stripped.capitalize())

    return word


def _restore_edge_punctuation(original: str, core: str) -> str:
    prefix = ""
    suffix = ""
    if original and not original[0].isalnum():
        prefix = original[0]
    if original and not original[-1].isalnum():
        suffix = original[-1]
    return f"{prefix}{core}{suffix}"


def smart_title_case(text: str) -> str:
    text = fix_compound_terms(text)
    if not text:
        return ""
    sections = re.split(r"\s*:\s*", text)
    cased_sections: list[str] = []
    for section in sections:
        words = section.split()
        cased_sections.append(
            " ".join(_display_word(word, index) for index, word in enumerate(words))
        )
    return ": ".join(cased_sections)


def _is_ocr_junk_fragment(fragment: str) -> bool:
    text = clean_fragment(fragment)
    if not text:
        return True
    if OCR_JUNK_FRAGMENT.match(text):
        return True
    lower = text.lower()
    if "tilburg" in lower and len(text) < 30:
        return True
    letters = re.sub(r"[^A-Za-z]", "", text)
    if not letters:
        return True
    if letters.lower() in DISPLAY_TERMS or letters.lower() in ALLOWED_SHORT_WORDS:
        return False
    if len(letters) <= 3:
        return True
    return False


def clean_fragment(fragment: str) -> str:
    return re.sub(r"\s+", " ", normalize_unicode(fragment)).strip(" .|:-_")


def _is_ocr_junk_word(word: str) -> bool:
    core = re.sub(r"[^\w'-]", "", word)
    if not core:
        return True
    lower = core.lower()
    if lower in DISPLAY_TERMS or lower in ALLOWED_SHORT_WORDS:
        return False
    return _is_ocr_junk_fragment(core)


def strip_ocr_noise(text: str) -> str:
    text = fix_compound_terms(text)
    if not text:
        return ""

    if "|" in text:
        parts = [clean_fragment(part) for part in text.split("|") if clean_fragment(part)]
        if len(parts) >= 2 and _is_ocr_junk_fragment(parts[0]):
            text = " ".join(parts[1:])
        else:
            text = " ".join(parts)

    words = text.split()
    while words and _is_ocr_junk_word(words[0]):
        words.pop(0)
    text = " ".join(words)
    return re.sub(r"\s+", " ", text).strip(" .|:-_")


def trim_trailing_title_fragments(text: str) -> str:
    """Drop broken OCR tail fragments such as 'Indef I' at the end of a title."""
    words = text.split()
    while words:
        last = words[-1]
        if len(last) <= 2 and last.lower() not in ALLOWED_SHORT_WORDS and last.lower() not in DISPLAY_TERMS:
            words.pop()
            continue
        if len(words) >= 2:
            prev = words[-2]
            if len(prev) <= 5 and len(last) <= 3 and prev.isalpha() and last.isalpha():
                if prev.lower() not in DISPLAY_TERMS and last.lower() not in DISPLAY_TERMS:
                    words.pop()
                    words.pop()
                    continue
        break
    return " ".join(words)


def polish_thesis_title(text: str) -> str:
    text = denoise_spaced_text(text)
    text = strip_ocr_noise(text)
    text = trim_trailing_title_fragments(text)
    text = smart_title_case(text)
    return re.sub(r"\s+", " ", text).strip(" .|:-_")


def normalize_manual_thesis_title(text: str) -> str:
    """Normalize a human-entered title without OCR tail trimming."""
    raw = re.sub(r"\s+", " ", normalize_unicode(text)).strip(" .|:-_")
    if not raw:
        return ""
    polished = polish_thesis_title(raw)
    if polished:
        return polished
    return smart_title_case(fix_compound_terms(raw))
