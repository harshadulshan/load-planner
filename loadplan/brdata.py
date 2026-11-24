"""
The Bischoff & Ratcliff (1995) and Davies & Bischoff (1999) container loading test problems BR0-BR15: 100 problems
each, a 587 x 233 x 220 cm container and 1 (BR0) to 100 (BR15) box types, with for every box dimension whether it may
stand vertical. Format as in OR-Library (thpack): problem number and seed, container, number of types, then per type:
id, length, upright?, width, upright?, height, upright?, count.

Downloaded on first use from a pinned commit of the Metasolver repository (Araya et al.) and checked by SHA-256;
kept in data_cache/br/ (not part of this repository).
"""

from __future__ import annotations

import hashlib
import os
import urllib.request

import numpy as np

from .engine import Cargo, Vehicle

ROOT = os.environ.get("LP_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data_cache", "br")
URL = ("https://raw.githubusercontent.com/NicoFuentese/Metasolver/79c8e67ee16e960aaeaa8606667ffa92ac709a55/"
       "problems/clp/benchs/BR/BR{k}.txt")
SHA256 = {
    0: "f2d77ef65e2d1ff6a49da436f8660d8fabc463037576ce6e5b67a963fae06202",
    1: "5d9eb434f06ca408fc2738d7d200f4f699c590e7fbcb2ee670c0cee3cfca8917",
    2: "cc763b3c3f460624ad8d1d240dd6fd21e99e1cb9ad6ed2cd4522135ee74d018a",
    3: "0a02c49e94158a02693d22cf52776b94919705e0e272a18435a522b37d2e24bf",
    4: "bb1de1a8256b33be3989f538a415a6e0cc25c2e484c40869fb6952a05188dee4",
    5: "d7debf833ef56c2fa4c26e1f014306b869c715235fd8a9f29069ce074ce17509",
    6: "51ba71a092113296d0dadf630cee30acd8342a5684dfc27758893f605c4ab0c7",
    7: "1bf0128243805361cc280d6d6c50a50cbfe08760be19786cc2a533342cb5e715",
    8: "448fbc1fba12a1ff061257fbe9a9f853ef696841e518c86153d789ab60591a41",
    9: "0b7fae14c83669229e497a74cecf3618a0d73ab1d453401bcb28c7a3a2213ced",
    10: "8097a2f5d82470f7c5d7f196e16447f140048dd51f5a7c22dbc282ddfe759b02",
    11: "7bd31f975a0460ca1051e4e28f649fdb42eac2fb0c732869e03fdfa81241ce04",
    12: "c7d0e32666c176b015b77e27e0d555ce6d5d1a88691bd869b53b78c106dda54d",
    13: "d2a454cf60198d4ed1acf6b9a613f6f9f94f610bb3bc7e00e1c52f84528bc121",
    14: "bd036b3ddabaa47a4692c67419717e5980d1684f9a1f8b0ed6c9d50fb92bb90f",
    15: "598dc5d805000c856b2c802296633a11ff90e7bdb849c72d00212272cc3a2f6f",
}


def fetch(k):
    path = os.path.join(CACHE, f"BR{k}.txt")
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        urllib.request.urlretrieve(URL.format(k=k), path + ".part")
        os.replace(path + ".part", path)
    got = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if got != SHA256[k]:
        raise ValueError(f"BR{k}.txt: checksum {got[:12]}... is not {SHA256[k][:12]}...; delete {path} and run again")
    return path


def parse(text):
    """[(problem number, Cargo, Vehicle)] from thpack-format text."""
    tok = text.split()
    p = 0

    def nxt():
        nonlocal p
        p += 1
        return int(tok[p - 1])
    out = []
    for _ in range(nxt()):
        num = nxt()
        nxt()                                             # seed
        L, W, H = nxt(), nxt(), nxt()
        n = nxt()
        dims, up, cnt = [], [], []
        for _ in range(n):
            _id, a, fa, b, fb, c, fc, q = (nxt() for _ in range(8))
            dims.append((a, b, c))
            up.append((fa, fb, fc))
            cnt.append(q)
        out.append((num, Cargo(np.array(dims), np.array(up, bool), np.array(cnt)), Vehicle("container", L, W, H)))
    return out


def load(k):
    return parse(open(fetch(k)).read())
