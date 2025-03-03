from __future__ import annotations

from difflib import SequenceMatcher

from typing import List
import stanza
import spacy

from argostranslate import package, settings
from argostranslate.package import Package
from argostranslate.utils import info
from argostranslate.networking import cache_spacy


class ISentenceBoundaryDetectionModel():
    # https://github.com/argosopentech/sbd/blob/main/main.py
    pkg: Package

    def split_sentences(self, text: str) -> List[str]:
        raise NotImplementedError


# Spacy sentence boundary detection Sentencizer
# https://community.libretranslate.com/t/sentence-boundary-detection-for-machine-translation/606/3
# https://spacy.io/usage/linguistic-features/#sbd
# Download model:
# python -m spacy download xx_sent_ud_sm
class SpacySentencizerSmall(ISentenceBoundaryDetectionModel):
    def __init__(self, pkg: Package):
        '''
        Packaging specific spacy when "xx_sent_ud_sm" doesn't cover the language improves performances over stanza.
        Please use small models ".._core/web_sm" for consistency.
        '''
        if pkg.packaged_sbd_path is not None:
            self.nlp = spacy.load(pkg.packaged_sbd_path, exclude=["parser"])
        # Case sbd is not packaged, use cached Spacy multilingual (xx_ud_sent_sm)
        else:
            cached_spacy = cache_spacy()
            self.nlp = spacy.load(cached_spacy, exclude=["parser"])
        self.nlp.add_pipe("sentencizer")

    def split_sentences(self, text: str) -> List[str]:
        doc = self.nlp(text)
        return [sent.text for sent in doc.sents]

    def __str__(self):
            return "Using Spacy model."

# Stanza sentence boundary detection Sentencizer (legacy, but quite a few languages need it)
class StanzaSentencizer(ISentenceBoundaryDetectionModel):
    # Initializes the stanza pipeline, formerly coded in translate.py (commented lines 438-477)
    # which is actually a tokenizer, hence the slow-motion when running it
    def __init__(self, pkg: Package):
         self.stanza_pipeline = stanza.Pipeline(
            lang=pkg.from_code,
            dir=str(pkg.packaged_sbd_path),
            processors="tokenize",
            use_gpu=settings.device == "cuda",
            logging_level="WARNING",
        )

    def split_sentences(self, text: str) -> List[str]:
        doc = self.stanza_pipeline(text)
        return [sent.text for sent in doc.sentences]

    def __str__(self):
        return "Using Stanza library"

# Few Shot Sentence Boundary Detection

fewshot_prompt = """<detect-sentence-boundaries> I walked down to the river. Then I went to the
I walked down to the river. <sentence-boundary>
----------
<detect-sentence-boundaries> Argos Translate is machine translation software. It is also
Argos Translate is machine translation software. <sentence-boundary>
----------
<detect-sentence-boundaries> Argos Translate is written in Python and uses OpenAI. It also supports
Argos Translate is written in Python and uses OpenAI. <sentence-boundary>
----------
"""

DETECT_SENTENCE_BOUNDARIES_TOKEN = "<detect-sentence-boundaries>"
SENTENCE_BOUNDARY_TOKEN = "<sentence-boundary>"
FEWSHOT_BOUNDARY_TOKEN = "-" * 10


def get_sbd_package() -> Package | None:
    packages = package.get_installed_packages()
    for pkg in packages:
        if pkg.type == "sbd":
            return pkg
    return None


def generate_fewshot_sbd_prompt(
    input_text: str, sentence_guess_length: int = 150
) -> str:
    sentence_guess = input_text[:sentence_guess_length]
    to_return = fewshot_prompt + "<detect-sentence-boundaries> " + sentence_guess
    info("generate_fewshot_sbd_prompt", to_return)
    return to_return


def parse_fewshot_response(response_text: str) -> str | None:
    response = response_text.split(FEWSHOT_BOUNDARY_TOKEN)
    info("parse_fewshot_response", response)
    if len(response) < 2:
        return None
    response = response[-2].split("\n")
    if len(response) < 2:
        return None
    return response[-1]


def process_seq2seq_sbd(input_text: str, sbd_translated_guess: str) -> int:
    sbd_translated_guess_index = sbd_translated_guess.find(SENTENCE_BOUNDARY_TOKEN)
    if sbd_translated_guess_index != -1:
        sbd_translated_guess = sbd_translated_guess[:sbd_translated_guess_index]
        info("sbd_translated_guess:", sbd_translated_guess)
        best_index = None
        best_ratio = 0.0
        for i in range(len(input_text)):
            candidate_sentence = input_text[:i]
            sm = SequenceMatcher()
            sm.set_seqs(candidate_sentence, sbd_translated_guess)
            ratio = sm.ratio()
            if best_index is None or ratio > best_ratio:
                best_index = i
                best_ratio = ratio
        return best_index
    else:
        return -1


def detect_sentence(
    input_text: str, sbd_translation, sentence_guess_length: int = 150
) -> int:
    """Given input text, return the index after the end of the first sentence.

    Args:
        input_text: The text to detect the first sentence of.
        sbd_translation: An ITranslation for detecting sentences.
        sentence_guess_length: Estimated number of chars > than most sentences.

    Returns:
        The index of the character after the end of the sentence.
                -1 if not found.
    """
    # TODO: Cache
    sentence_guess = input_text[:sentence_guess_length]
    info("sentence_guess:", sentence_guess)
    sbd_translated_guess = sbd_translation.translate(
        DETECT_SENTENCE_BOUNDARIES_TOKEN + sentence_guess
    )
    return process_seq2seq_sbd(input_text, sbd_translated_guess)
