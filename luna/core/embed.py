"""Transformer speech embeddings: each speaker's quotes as one vector, an optional extra kind of speech evidence for matching
groups to collection characters (core/matching.py, "Speech (transformer)"). Off by default and computed only when you ask
(the Collection tool); it never replaces the stylometric speech counts.

A quote becomes the mean of the model's last layer over its tokens (at most 128 of them); a speaker is the mean of their
quotes. Only models already downloaded (the Hugging Face cache, where BookNLP keeps its BERT models) are used, never the
internet; `available_models` lists those this installation of `transformers` can load. torch and transformers are imported
only when a model is loaded, so nothing else in the editor depends on them.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MODEL = "google/bert_uncased_L-12_H-768_A-12"   # BookNLP's own BERT
MAX_TOKENS = 128
BATCH = 32


def hub_dir() -> Path:
    """Where Hugging Face keeps downloaded models (HF_HUB_CACHE, else HF_HOME/hub, else ~/.cache/huggingface/hub)."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def available_models():
    """The downloaded models this installation can load, as [{id, size}] (size = the vector length), default first."""
    try:
        from transformers import AutoConfig
        from transformers.utils import logging
        logging.set_verbosity_error()
    except ImportError:
        return []
    out = []
    for d in sorted(hub_dir().glob("models--*")):
        mid = d.name[len("models--"):].replace("--", "/")
        try:
            cfg = AutoConfig.from_pretrained(mid, local_files_only=True)
        except Exception:  # noqa: BLE001 - a model this version can't read (e.g. a newer architecture) isn't offered
            continue
        if getattr(cfg, "hidden_size", None):
            out.append({"id": mid, "size": cfg.hidden_size})
    out.sort(key=lambda m: (m["id"] != DEFAULT_MODEL, m["id"]))
    return out


class Embedder:
    """One loaded model; `speakers` turns quotes into one vector per speaker."""

    def __init__(self, model=DEFAULT_MODEL):
        """Load the model and its tokenizer from the local cache (raises if the model isn't downloaded or can't be read)."""
        # the fast tokenizer's threads warn on every later fork (the app's "Show in Finder", tests' subprocesses)
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        import torch
        from transformers import AutoModel, AutoTokenizer
        from transformers.utils import logging
        logging.set_verbosity_error()
        self.model_id = model
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)
        self.model = AutoModel.from_pretrained(model, local_files_only=True).eval()

    def quotes(self, texts):
        """One vector per text: the mean of the last layer over its tokens."""
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), BATCH):
                enc = self.tokenizer(texts[i:i + BATCH], padding=True, truncation=True, max_length=MAX_TOKENS, return_tensors="pt")
                last = self.model(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).float()
                out.extend(((last * mask).sum(1) / mask.sum(1).clamp(min=1)).tolist())
        return out

    def speakers(self, quotes_by_speaker):
        """{speaker: [quote texts]} -> {speaker: (vector as a list of floats, number of quotes)}."""
        keys = [k for k, qs in quotes_by_speaker.items() if qs]
        flat = [q for k in keys for q in quotes_by_speaker[k]]
        vecs = self.quotes(flat)
        out, i = {}, 0
        for k in keys:
            n = len(quotes_by_speaker[k])
            part = vecs[i:i + n]
            i += n
            out[k] = ([round(sum(col) / n, 4) for col in zip(*part)], n)
        return out
