"""Instrumentasi anggaran context window (prompt budget) untuk agen LLM.

Latar belakang: model di LM Studio dimuat dengan context window terbatas
(n_ctx). Prompt yang dikirim = system prompt + user prompt, dan server masih
harus menyisakan ruang untuk keluaran (max_tokens). Bila totalnya melampaui
n_ctx, server memotong prompt secara diam-diam (atau menolak request) —
kejadian yang selama ini TIDAK tercatat di manapun.

Modul ini menyediakan:
- estimasi jumlah token dari string (lihat catatan akurasi di bawah);
- check_budget(): rincian token per komponen + status aman/melampaui;
- akumulator statistik per-agen untuk dilaporkan sekali di akhir run
  (bukan dicetak tiap panggilan, karena terlalu berisik);
- trim_to_budget(): pemangkasan daftar kandidat agar prompt muat.

CATATAN AKURASI TOKENIZER
Secara default jumlah token DIESTIMASI dari panjang karakter dibagi rasio
PROMPT_BUDGET_CHARS_PER_TOKEN (default 3,5 — nilai empiris untuk teks Inggris
teknis pada tokenizer keluarga Qwen/BPE). Estimasi ini TIDAK eksak: penulisan
angka, ID teknik ("T1566.001"), dan tanda baca padat cenderung memakai lebih
banyak token per karakter. Bila tokenizer asli tersedia secara OFFLINE, set
PROMPT_BUDGET_TOKENIZER ke path direktori tokenizer lokal (mis. hasil unduhan
HuggingFace) — modul akan memakainya dan menandai hasil sebagai eksak
(field "estimated": false). Modul ini tidak pernah mengunduh apapun dari
jaringan dan tidak menambah dependensi wajib.

Semua perilaku aktif bersifat opt-in: tanpa PROMPT_BUDGET_ENFORCE=true, modul
hanya MENCATAT dan tidak pernah mengubah prompt.
"""
import math
import os
import threading

from dotenv import load_dotenv

# Konstanta dibaca saat import (pola yang sama dengan agents/retrieval.py).
load_dotenv()


# Context window model yang sedang dimuat di server LLM. Ini TIDAK bisa dibaca
# otomatis dari LM Studio lewat API OpenAI-compatible, jadi harus dideklarasikan
# manual dan ikut dicatat di run manifest agar angka run bisa ditelusuri.
LLM_N_CTX = int(os.getenv("LLM_N_CTX", "4096"))
# false = hanya mencatat (perilaku identik dengan sebelum instrumentasi ini).
# true  = pangkas daftar kandidat bila prompt melampaui n_ctx.
PROMPT_BUDGET_ENFORCE = os.getenv("PROMPT_BUDGET_ENFORCE", "false").lower() == "true"
# Rasio karakter per token untuk estimasi (lihat CATATAN AKURASI di docstring).
CHARS_PER_TOKEN = float(os.getenv("PROMPT_BUDGET_CHARS_PER_TOKEN", "3.5"))
# Path direktori tokenizer lokal (opsional, offline). Kosong = pakai estimasi.
TOKENIZER_PATH = os.getenv("PROMPT_BUDGET_TOKENIZER", "").strip()

_TOKENIZER_STATE: dict = {"tried": False, "tokenizer": None}


def _load_tokenizer():
    """Muat tokenizer asli bila tersedia OFFLINE; None bila tidak.

    Sengaja hanya menerima path direktori lokal (bukan nama model) agar tidak
    pernah ada percobaan unduh ke jaringan saat pipeline berjalan.
    """
    if _TOKENIZER_STATE["tried"]:
        return _TOKENIZER_STATE["tokenizer"]
    _TOKENIZER_STATE["tried"] = True

    if not TOKENIZER_PATH or not os.path.isdir(TOKENIZER_PATH):
        return None

    tokenizer = None
    try:  # paket `tokenizers` (ringan) lebih dulu
        from tokenizers import Tokenizer  # type: ignore

        tokenizer_file = os.path.join(TOKENIZER_PATH, "tokenizer.json")
        if os.path.isfile(tokenizer_file):
            loaded = Tokenizer.from_file(tokenizer_file)
            tokenizer = lambda text: len(loaded.encode(text).ids)  # noqa: E731
    except Exception:
        tokenizer = None

    if tokenizer is None:
        try:  # fallback: transformers, tetap dipaksa offline
            from transformers import AutoTokenizer  # type: ignore

            loaded = AutoTokenizer.from_pretrained(TOKENIZER_PATH, local_files_only=True)
            tokenizer = lambda text: len(loaded.encode(text))  # noqa: E731
        except Exception as exc:
            print(f"Warning: tokenizer lokal gagal dimuat ({str(exc)[:80]}) — pakai estimasi karakter.")
            tokenizer = None

    _TOKENIZER_STATE["tokenizer"] = tokenizer
    return tokenizer


def tokens_are_estimated() -> bool:
    """True bila jumlah token dihitung dengan pendekatan karakter (bukan tokenizer asli)."""
    return _load_tokenizer() is None


def estimate_tokens(text: str) -> int:
    """Jumlah token sebuah string.

    Eksak bila tokenizer lokal tersedia; selain itu ESTIMASI = ceil(len/rasio).
    """
    if not text:
        return 0
    tokenizer = _load_tokenizer()
    if tokenizer is not None:
        try:
            return int(tokenizer(text))
        except Exception:
            pass  # tokenizer bermasalah di tengah run -> jatuh ke estimasi
    return int(math.ceil(len(text) / CHARS_PER_TOKEN))


def check_budget(
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    n_ctx: int | None = None,
) -> dict:
    """Rincian anggaran context window untuk satu panggilan LLM.

    Returns dict:
        system_tokens, user_tokens, prompt_tokens : token per komponen
        max_output_tokens, n_ctx                  : cadangan keluaran & kapasitas
        total_tokens                              : prompt + cadangan keluaran
        remaining                                 : n_ctx - total_tokens (bisa negatif)
        overflow                                  : total_tokens > n_ctx
        prompt_overflow                           : prompt saja sudah > n_ctx
        estimated                                 : True bila token diestimasi
    """
    n_ctx = LLM_N_CTX if n_ctx is None else n_ctx
    system_tokens = estimate_tokens(system_prompt)
    user_tokens = estimate_tokens(user_prompt)
    prompt_tokens = system_tokens + user_tokens
    total_tokens = prompt_tokens + max_output_tokens

    return {
        "system_tokens": system_tokens,
        "user_tokens": user_tokens,
        "prompt_tokens": prompt_tokens,
        "max_output_tokens": max_output_tokens,
        "n_ctx": n_ctx,
        "total_tokens": total_tokens,
        "remaining": n_ctx - total_tokens,
        "overflow": total_tokens > n_ctx,
        "prompt_overflow": prompt_tokens > n_ctx,
        "estimated": tokens_are_estimated(),
    }


def trim_to_budget(
    lines: list[str],
    overhead_tokens: int,
    max_output_tokens: int,
    n_ctx: int | None = None,
) -> tuple[list[str], int]:
    """Pangkas daftar baris (kandidat) dari EKOR agar prompt muat di n_ctx.

    overhead_tokens: token seluruh bagian prompt SELAIN baris-baris ini
    (template instruksi, system prompt, potongan laporan). Potongan laporan
    sengaja tidak pernah dipangkas di sini — yang dikorbankan lebih dulu adalah
    kandidat berperingkat terbawah, karena kandidat teratas jauh lebih mungkin
    benar (lihat catatan TECHNIQUE_ACCEPT_TOP_N di technique_agent).

    Returns: (baris_yang_dipertahankan, jumlah_baris_dibuang). Minimal satu
    baris selalu dipertahankan — prompt tanpa kandidat sama sekali tidak ada
    gunanya, dan kasus itu tetap tercatat sebagai overflow.
    """
    n_ctx = LLM_N_CTX if n_ctx is None else n_ctx
    allowed = n_ctx - max_output_tokens - overhead_tokens
    if allowed <= 0:
        return lines[:1], max(0, len(lines) - 1)

    kept: list[str] = []
    used = 0
    for line in lines:
        cost = estimate_tokens(line + "\n")
        if kept and used + cost > allowed:
            break
        kept.append(line)
        used += cost
    return kept, len(lines) - len(kept)


# --- Akumulator statistik run ------------------------------------------------
# Statistik dikumpulkan PER THREAD. web_app menjalankan tiap batch/job di worker
# thread tersendiri, dan LangGraph mengeksekusi node di thread pemanggil
# (terverifikasi), sehingga satu bucket per thread = satu bucket per run — job
# satu-laporan yang berjalan bersamaan tidak mencemari statistik run batch.
_STATS_LOCK = threading.Lock()
_STATS: dict[int, dict[str, dict]] = {}


def _empty_agent_stats() -> dict:
    return {
        "calls": 0,
        "prompt_tokens_sum": 0,
        "prompt_tokens_max": 0,
        "total_tokens_max": 0,
        "overflow_calls": 0,
        "prompt_overflow_calls": 0,
        "trimmed_calls": 0,
        "candidates_dropped": 0,
    }


def record_prompt(agent: str, budget: dict, candidates_dropped: int = 0) -> None:
    """Catat satu panggilan LLM ke statistik run (tidak mencetak apapun)."""
    with _STATS_LOCK:
        bucket = _STATS.setdefault(threading.get_ident(), {})
        stats = bucket.setdefault(agent, _empty_agent_stats())
        stats["calls"] += 1
        stats["prompt_tokens_sum"] += budget["prompt_tokens"]
        stats["prompt_tokens_max"] = max(stats["prompt_tokens_max"], budget["prompt_tokens"])
        stats["total_tokens_max"] = max(stats["total_tokens_max"], budget["total_tokens"])
        if budget["overflow"]:
            stats["overflow_calls"] += 1
        if budget["prompt_overflow"]:
            stats["prompt_overflow_calls"] += 1
        if candidates_dropped:
            stats["trimmed_calls"] += 1
            stats["candidates_dropped"] += candidates_dropped


def reset_prompt_stats(all_threads: bool = False) -> None:
    """Kosongkan statistik thread ini (atau seluruh thread bila all_threads)."""
    with _STATS_LOCK:
        if all_threads:
            _STATS.clear()
        else:
            _STATS.pop(threading.get_ident(), None)


def get_prompt_stats() -> dict:
    """Ringkasan statistik prompt per agen + agregat, untuk thread ini."""
    with _STATS_LOCK:
        bucket = _STATS.get(threading.get_ident(), {})
        per_agent = {}
        totals = _empty_agent_stats()
        for agent, stats in bucket.items():
            calls = stats["calls"] or 1
            per_agent[agent] = {
                "calls": stats["calls"],
                "prompt_tokens_avg": round(stats["prompt_tokens_sum"] / calls, 1),
                "prompt_tokens_max": stats["prompt_tokens_max"],
                "total_tokens_max": stats["total_tokens_max"],
                "overflow_calls": stats["overflow_calls"],
                "prompt_overflow_calls": stats["prompt_overflow_calls"],
                "trimmed_calls": stats["trimmed_calls"],
                "candidates_dropped": stats["candidates_dropped"],
            }
            for key in totals:
                totals[key] = (
                    max(totals[key], stats[key]) if key.endswith("_max")
                    else totals[key] + stats[key]
                )

        all_calls = totals["calls"] or 1
        return {
            "n_ctx": LLM_N_CTX,
            "enforce": PROMPT_BUDGET_ENFORCE,
            "tokens_estimated": tokens_are_estimated(),
            "chars_per_token": CHARS_PER_TOKEN if tokens_are_estimated() else None,
            "per_agent": per_agent,
            "total_calls": totals["calls"],
            "prompt_tokens_avg": round(totals["prompt_tokens_sum"] / all_calls, 1),
            "prompt_tokens_max": totals["prompt_tokens_max"],
            "overflow_calls": totals["overflow_calls"],
            "candidates_dropped": totals["candidates_dropped"],
        }


def format_prompt_stats(stats: dict | None = None) -> str:
    """Ringkasan statistik prompt siap cetak (satu blok, akhir run)."""
    stats = stats or get_prompt_stats()
    mode = "ENFORCE" if stats["enforce"] else "catat-saja"
    token_src = (
        f"estimasi ~{stats['chars_per_token']} char/token"
        if stats["tokens_estimated"] else "tokenizer asli"
    )
    lines = [
        f"=== STATISTIK PROMPT (n_ctx={stats['n_ctx']}, mode={mode}, {token_src}) ===",
        f"{'agen':<12} {'panggilan':>9} {'tok rata2':>10} {'tok maks':>9} {'overflow':>9} {'kandidat dibuang':>17}",
    ]
    for agent, s in sorted(stats["per_agent"].items()):
        lines.append(
            f"{agent:<12} {s['calls']:>9} {s['prompt_tokens_avg']:>10.1f} "
            f"{s['prompt_tokens_max']:>9} {s['overflow_calls']:>9} {s['candidates_dropped']:>17}"
        )
    lines.append(
        f"{'TOTAL':<12} {stats['total_calls']:>9} {stats['prompt_tokens_avg']:>10.1f} "
        f"{stats['prompt_tokens_max']:>9} {stats['overflow_calls']:>9} {stats['candidates_dropped']:>17}"
    )
    return "\n".join(lines)
