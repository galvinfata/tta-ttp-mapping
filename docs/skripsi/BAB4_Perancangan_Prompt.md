# Perancangan Prompt (A6)

Subbab ini menjelaskan perancangan prompt untuk ketiga agen LLM (Tactic Agent, Technique Agent, Reviewer Agent) yang dijalankan pada model lokal **Qwen3-4B** melalui LM Studio (OpenAI-compatible API). Ukuran model yang kecil — 4 miliar parameter dengan context window terbatas — menjadi kendala sekaligus titik tolak perancangan. Prinsip-prinsip yang mendasarinya diuraikan berikut.

Prinsip pertama menyangkut cara instruksi disampaikan. Bsharat et al. (2023), dalam evaluasi empiris terhadap 26 prinsip perancangan prompt, menemukan bahwa instruksi yang terstruktur, afirmatif, dan eksplisit menaikkan kualitas maupun akurasi respons — dan efeknya justru paling besar pada model berukuran kecil. Atas dasar itu, seluruh aturan dalam prompt ditulis sebagai daftar bernomor, bukan narasi. Logika serupa berlaku untuk pemberian contoh. Menyertakan contoh langsung di dalam prompt (*in-context learning*) terbukti menaikkan kinerja tugas tanpa pelatihan ulang parameter (Brown et al., 2020), sehingga setiap prompt memuat dua contoh: satu pemetaan yang benar untuk mendemonstrasikan format dan kepadatan keluaran yang diharapkan, serta satu contoh negatif berupa daftar kosong. Contoh negatif ini tampak sepele, tetapi perannya penting — ia mengajari model bahwa dokumen tanpa deskripsi perilaku penyerang memang seharusnya dijawab kosong, bukan dipaksakan dipetakan.

Dua prinsip berikutnya lahir dari analisis kesalahan sistem ini sendiri, dan keduanya bekerja pada arah yang berlawanan. Di satu sisi, sumber false positive terbesar (650 FP pada evaluasi awal) adalah kecenderungan model menafsirkan penyebutan tool, daftar indikator, atau saran mitigasi sebagai perilaku serangan — pola kesalahan interpretasi yang juga didokumentasikan Fayyazi dan Yang (2023) pada deskripsi serangan yang ambigu. Kriteria keputusan eksplisit ("perilaku yang benar-benar dilakukan, bukan sekadar disebut") berfungsi sebagai rem terhadap kecenderungan ini. Di sisi lain, model 4B cenderung berhenti terlalu cepat. Analisis false negative mencatat 398 kasus *reasoning-miss*: teknik yang benar sudah tersedia di daftar kandidat, tetapi tidak dipilih. Petunjuk kardinalitas dan instruksi memindai laporan kalimat per kalimat menjadi penyeimbangnya — mendorong model menuntaskan pemindaian alih-alih puas dengan beberapa kecocokan pertama.

Kelompok prinsip ketiga menyangkut kontrol atas keluaran. Skema `{"ids": [...]}` dipaksakan melalui parameter `response_format` (JSON schema, `strict: true`) dengan teknik *constrained decoding* yang menjamin validitas sintaksis keluaran (Willard & Louf, 2023). Pembatasan format memang ada harganya; Tam et al. (2024) melaporkan trade-off terhadap kemampuan penalaran bebas. Namun untuk tugas seleksi ID, kepastian parsing lebih bernilai. Pengaturan `temperature=0` dipilih karena kenaikan temperatur tidak terbukti menaikkan akurasi problem-solving (Renze, 2024), sementara determinisme dibutuhkan agar hasil evaluasi dapat direproduksi. Mode *hybrid thinking* Qwen3 juga dinonaktifkan (`enable_thinking: false` ditambah sufiks `/no_think`) melalui mekanisme yang memang disediakan arsitektur model (Qwen Team, 2025) — tanpa ini, anggaran token keluaran habis untuk reasoning internal dan JSON yang dikembalikan kosong.

Prinsip terakhir, dan boleh dibilang paling menentukan, adalah *closed-set selection*. Technique Agent hanya boleh memilih dari daftar kandidat hasil retrieval TF-IDF (top-k=50), bukan dari lebih dari 600 teknik ATT&CK. Pola *retrieval-augmented generation* semacam ini mengurangi halusinasi dengan mengikat keluaran model pada pengetahuan eksternal yang diambil secara eksplisit (Lewis et al., 2020; Ji et al., 2023). Efektivitasnya untuk pemetaan TTP secara khusus juga sudah ditunjukkan: RAG dengan decoder LLM mengungguli baik LLM tanpa retrieval maupun model encoder yang di-fine-tune (Fayyazi et al., 2024). Konsekuensinya menarik. Tugas yang semula *open-ended generation* — rawan halusinasi ID — berubah menjadi *multiple-choice selection*, kelas persoalan yang jauh lebih ramah bagi model 4B.

Sebelum masuk ke rancangan tiap agen, perlu dibedakan dua jenis prompt yang dikirim ke model pada setiap pemanggilan. Antarmuka chat-completion yang digunakan (OpenAI-compatible) menerima pesan dalam dua peran berbeda. **System prompt** adalah pesan berperan `system`: instruksi tingkat tinggi yang menetapkan identitas, batasan, dan format keluaran model sepanjang percakapan — singkat, statis, dan sama untuk setiap laporan yang diproses. **User prompt**, sebaliknya, adalah pesan berperan `user` yang memuat isi tugas aktual: instruksi rinci, contoh few-shot, dan data yang berubah di tiap pemanggilan (kutipan laporan, daftar kandidat, feedback reviewer). Pemisahan ini bukan sekadar konvensi. Model chat dilatih memberi bobot kepatuhan lebih tinggi pada pesan `system`, sehingga aturan paling kritis — "keluarkan hanya JSON" — ditempatkan di sana, sementara detail tugas yang panjang dan dinamis berada di pesan `user`. Pada implementasi ini, sufiks `/no_think` juga ditambahkan ke system prompt karena Qwen3 membaca penanda mode reasoning dari sana.

Setiap prompt terdiri atas empat komponen: **peran** (role), **instruksi/aturan**, **format keluaran JSON**, dan **slot penyisipan** (daftar taktik/kandidat teknik dan kutipan laporan).

---

## 1. Prompt Tactic Agent

**System prompt:**

```
You are an expert CTI analyst. Map the text to MITRE ATT&CK Tactics.
Output ONLY a JSON object {"ids": [...]} of tactic IDs, nothing else. /no_think
```

**User prompt** (slot `{tactic_str}` diisi daftar 14 taktik Enterprise beserta glosarium satu baris; `{report_text}` diisi kutipan laporan, maksimum `LOCAL_LLM_REPORT_MAX_CHARS` karakter):

```
TASK: Identify every MITRE ATT&CK TACTIC (TA####) whose goal is pursued by the
attacker in the report excerpt. Choose ONLY from the list below.

AVAILABLE TACTICS:
- TA0043: Reconnaissance — gathering info about the target before attack
- TA0042: Resource Development — acquiring infrastructure, accounts, or tools
- TA0001: Initial Access — getting into the network (phishing, exploits, valid accounts)
- TA0002: Execution — running attacker code (scripts, commands, user execution)
- TA0003: Persistence — keeping access across restarts (run keys, services, tasks)
- TA0004: Privilege Escalation — gaining higher-level permissions
- TA0005: Defense Evasion — avoiding detection (obfuscation, disabling tools, masquerading)
- TA0006: Credential Access — stealing passwords, hashes, tokens, keys
- TA0007: Discovery — exploring the environment (system, network, account enumeration)
- TA0008: Lateral Movement — moving to other systems in the network
- TA0009: Collection — gathering data of interest (files, screenshots, keylogging)
- TA0011: Command and Control — communicating with compromised systems (C2, tunneling)
- TA0010: Exfiltration — stealing data out of the network
- TA0040: Impact — destroying, encrypting, or disrupting systems and data

DECISION RULES:
1. Include a tactic ONLY if the report describes the attacker actually pursuing
   that goal — not tool capabilities, IOC lists, or defensive recommendations.
2. Scan the WHOLE excerpt sentence by sentence; multi-stage intrusions typically
   involve 4-8 tactics. Do not stop after the first matches.
3. If nothing applies, return {"ids": []}.

EXAMPLE
Report: "Attackers sent spear-phishing emails with malicious attachments, then
used PowerShell to run malware, dumped LSASS memory, and contacted a C2 server."
Output: {"ids": ["TA0001","TA0002","TA0006","TA0011"]}

EXAMPLE (nothing applies)
Report: "This advisory lists file hashes and recommends enabling MFA."
Output: {"ids": []}

REPORT EXCERPT:
"""{report_text}"""

Answer with ONLY the JSON object {"ids": [...]}.
```

**Rasional desain.** Dibanding versi awal yang hanya mencantumkan nama taktik, versi ini menambahkan glosarium satu baris per taktik. Untuk model 4B, nama seperti "Resource Development" atau "Collection" ambigu tanpa definisi — konsisten dengan temuan bahwa istilah taksonomi serangan ditafsirkan berbeda-beda bahkan oleh analis manusia, apalagi oleh LLM (Fayyazi & Yang, 2023); glosarium 10–12 kata per taktik hanya menambah ±150 token tetapi memberi jangkar semantik saat mencocokkan kalimat laporan. Daftar diurutkan sesuai urutan kill-chain agar model "membaca" alur serangan secara natural. Contoh negatif (advisory berisi hash + rekomendasi MFA) memanfaatkan mekanisme in-context learning (Brown et al., 2020) untuk mengajari model bahwa dokumen defensif bukan bukti taktik.

---

## 2. Prompt Technique Agent

**System prompt:**

```
You are an expert CTI analyst. Map the text to MITRE ATT&CK Techniques.
Output ONLY a JSON object {"ids": [...]} of technique IDs from the candidate
list, nothing else. /no_think
```

**User prompt** (slot `{technique_str}` diisi daftar kandidat hasil retrieval TF-IDF — top-k=50, deskripsi dipangkas 120 karakter, total daftar dibatasi 4500 karakter; `{chunk}` diisi potongan laporan hasil chunking, maksimum 3500 karakter per chunk dengan overlap 250):

```
TASK: Select every MITRE ATT&CK technique from CANDIDATES that is described in
the report excerpt. You MUST choose only from CANDIDATES. Never output an ID
that is not in the list.

CANDIDATES:
{technique_str}

DECISION RULES:
1. Select a technique ONLY if the report describes the attacker actually
   performing that behavior. Do NOT select for:
   - security recommendations or mitigations ("enable MFA", "patch systems")
   - tool capabilities that were not observed in use
   - plain IOC lists (hashes, IPs, domains) with no described behavior
2. Prefer the sub-technique (T####.###) when the report is specific about the
   variant; use the parent (T####) only when the description is generic.
3. Scan the WHOLE excerpt sentence by sentence. CTI reports typically describe
   5-15 techniques; do not stop after the first few matches.
4. A behavior can be described without naming the technique — match on meaning
   (e.g., "decoded a base64 payload" = Deobfuscate/Decode Files or Information).
5. If a candidate has no supporting sentence in the excerpt, exclude it.
6. If none match, return {"ids": []}.

EXAMPLE
Report: "The actor sent spear-phishing emails with a malicious ZIP attachment.
When opened by the victim, a PowerShell loader decoded a base64-encoded payload
and created a Run registry key for persistence."
Output: {"ids": ["T1566.001","T1204.002","T1059.001","T1140","T1547.001"]}
(Example IDs are illustrative — your answer must come from CANDIDATES above.)

EXAMPLE (nothing applies)
Report: "Indicators: 5f2b...e91a, 203.0.113.7. We recommend blocking these IPs."
Output: {"ids": []}

REPORT EXCERPT:
"""{chunk}"""

Answer with ONLY the JSON object {"ids": [...]}.
```

**Rasional desain.** Aturan 1 dan 5 adalah "rem" terhadap false positive: model harus bisa menunjuk kalimat pendukung, dan penyebutan tool/IOC/mitigasi tanpa perilaku tidak dihitung — kelas kesalahan interpretasi yang terdokumentasi pada penggunaan LLM untuk deskripsi serangan (Fayyazi & Yang, 2023). Aturan 3 (petunjuk kardinalitas 5–15) adalah "gas" terhadap *reasoning-miss*: tanpa petunjuk ini, model 4B cenderung berhenti setelah 2–3 teknik pertama. Aturan 4 penting karena laporan CTI jarang menyebut nama teknik secara eksplisit — model harus mencocokkan parafrasa perilaku dengan deskripsi kandidat, kesenjangan semantik yang menjadi motivasi utama pendekatan berbasis LLM dibanding pencocokan leksikal (Orbinato et al., 2022). Aturan 2 mengkodifikasi konvensi ATT&CK tentang pemilihan sub-teknik vs teknik induk. Contoh positif sengaja memuat lima teknik dari satu paragraf pendek untuk mendemonstrasikan kepadatan pemetaan yang diharapkan.

Pemecahan laporan panjang menjadi beberapa chunk (3500 karakter, overlap 250) dimotivasi oleh temuan bahwa LLM cenderung mengabaikan informasi di bagian tengah konteks yang panjang (*lost in the middle*), sehingga TTP yang dideskripsikan di akhir laporan berisiko terlewat bila seluruh laporan dikirim sekaligus (Liu et al., 2024).

Pertahanan berlapis di luar prompt tetap berlaku: (i) `response_format` JSON schema memaksa bentuk keluaran melalui constrained decoding (Willard & Louf, 2023), (ii) parser fallback regex mengekstrak pola `T####(.###)?` bila JSON gagal, (iii) validator program membuang ID di luar daftar kandidat/di luar knowledge base ATT&CK — sehingga meskipun model menyalin ID dari contoh few-shot, ID tersebut tersaring bila tidak ada di kandidat.

---

## 3. Prompt Reviewer Agent

**System prompt:**

```
You are a strict MITRE ATT&CK reviewer. Output only a JSON object, nothing else. /no_think
```

**User prompt** (slot `{report_excerpt}` = kutipan laporan; `{tactics_summary}` = daftar taktik terpilih; `{technique_summary}` = daftar teknik terpilih beserta nama, tactic-tags, dan deskripsi singkat):

```
TASK: Review whether the tactics and techniques below are consistent with the
report excerpt.

REPORT EXCERPT:
"""{report_excerpt}"""

SELECTED TACTICS:
{tactics_summary}

SELECTED TECHNIQUES:
{technique_summary}

CHECKS:
1. Every selected technique must correspond to a behavior described in the
   report (not just a tool name, IOC, or recommendation).
2. Every selected technique's tactic should appear in the selected tactics,
   and every selected tactic should be supported by at least one technique
   or an explicit statement in the report.
3. Flag obvious omissions: a clearly described attacker behavior with no
   matching technique selected.

OUTPUT FORMAT:
- If consistent: {"is_valid": true, "feedback": ""}
- If not: {"is_valid": false, "feedback": "<one short sentence: what to ADD or
  REMOVE and why, e.g. 'Remove T1105: no download behavior described. Add
  TA0003: registry Run key persistence is described.'>"}
Output only the JSON object.
```

**Rasional desain.** Pola tinjau-dan-revisi ini mengikuti paradigma *iterative refinement with feedback*: keluaran LLM diperbaiki melalui umpan balik yang kemudian disisipkan kembali ke prompt generator, pendekatan yang terbukti meningkatkan kualitas keluaran tanpa pelatihan tambahan (Madaan et al., 2023). Tiga *checks* memberi rubrik konkret, bukan sekadar "cek konsistensi". Format feedback dipaksa menjadi satu kalimat aksi (ADD/REMOVE + alasan) karena feedback ini disisipkan kembali ke prompt Tactic/Technique Agent pada iterasi revisi (dipotong 600 karakter, `temperature` dinaikkan ke 0.4 agar keluaran bisa berubah); Madaan et al. (2023) menekankan bahwa efektivitas refinement bergantung pada feedback yang spesifik dan dapat ditindaklanjuti (*actionable*), bukan penilaian umum. Feedback yang bertele-tele tidak dapat ditindaklanjuti oleh model 4B.

---

## 4. Ringkasan Teknik Prompt-Engineering yang Digunakan

| Teknik | Penerapan | Masalah yang ditangani |
|---|---|---|
| Role prompting | "expert CTI analyst" / "strict reviewer" di system prompt | Mengaktifkan register domain keamanan |
| Closed-set selection | Teknik hanya dari kandidat top-k retrieval | Halusinasi ID; ruang pilihan 600+ → 50 |
| Glosarium taktik | Definisi satu baris per TA#### | Ambiguitas nama taktik pada model kecil |
| Few-shot (positif + negatif) | 1 contoh pemetaan padat + 1 contoh `{"ids": []}` | Format keluaran; false positive pada dokumen defensif |
| Decision rules bernomor | Aturan perilaku-vs-penyebutan, sub-teknik vs induk | False positive (650 FP; 126 di antaranya ID usang) |
| Petunjuk kardinalitas | "typically 5-15 techniques", "4-8 tactics" | Under-selection / reasoning-miss (398 FN) |
| Structured output | JSON schema `{"ids": [...]}`, `strict: true` | Kegagalan parsing |
| Penonaktifan thinking | `enable_thinking: false` + `/no_think` | Token budget habis untuk reasoning, JSON kosong |
| Determinisme | `temperature=0`, `top_p=1.0` (0.4 saat revisi) | Reproduksibilitas evaluasi |
| Chunking laporan | 3500 char/chunk, overlap 250, maks 3 chunk, hasil di-union | TTP di bagian akhir laporan hilang |

Catatan batasan: analisis kesalahan menunjukkan 78% false negative berkategori *retrieval-miss* (teknik gold tidak masuk 50 kandidat). Kelas kesalahan ini berada di luar jangkauan perancangan prompt dan ditangani pada komponen retrieval; prompt di atas menargetkan sisa kesalahan yang berada dalam kendali LLM, yaitu *reasoning-miss* dan false positive.

---

## 5. Referensi Pendukung per Teknik Prompt

| Teknik pada rancangan | Referensi | DOI / Link |
|---|---|---|
| Few-shot prompting (contoh positif + negatif) | Brown et al. (2020), *Language Models are Few-Shot Learners*, NeurIPS 33 | [arXiv:2005.14165](https://arxiv.org/abs/2005.14165) |
| Instruksi bernomor, role prompting, desain prompt untuk model kecil | Bsharat et al. (2023), *Principled Instructions Are All You Need for Questioning LLaMA-1/2, GPT-3.5/4* | [arXiv:2312.16171](https://arxiv.org/abs/2312.16171) |
| Closed-set selection: retrieval kandidat lalu LLM memilih (retrieval-augmented) | Lewis et al. (2020), *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*, NeurIPS 33 | [arXiv:2005.11401](https://arxiv.org/abs/2005.11401) |
| Alasan membatasi ruang jawaban: mengurangi halusinasi generasi terbuka | Ji et al. (2023), *Survey of Hallucination in Natural Language Generation*, ACM Computing Surveys 55(12) | [10.1145/3571730](https://doi.org/10.1145/3571730) |
| Structured output (JSON schema / constrained decoding) | Willard & Louf (2023), *Efficient Guided Generation for Large Language Models* | [arXiv:2307.09702](https://arxiv.org/abs/2307.09702) |
| Trade-off pembatasan format vs kualitas jawaban | Tam et al. (2024), *Let Me Speak Freely? A Study on the Impact of Format Restrictions on Performance of LLMs*, EMNLP 2024 Industry Track | [arXiv:2408.02442](https://arxiv.org/abs/2408.02442) |
| Loop Reviewer → revisi (iterative self-feedback) | Madaan et al. (2023), *Self-Refine: Iterative Refinement with Self-Feedback*, NeurIPS 36 | [arXiv:2303.17651](https://arxiv.org/abs/2303.17651) |
| `temperature=0` untuk reproduksibilitas (temperatur tidak menaikkan akurasi problem-solving) | Renze (2024), *The Effect of Sampling Temperature on Problem Solving in Large Language Models*, Findings of EMNLP 2024 | [10.18653/v1/2024.findings-emnlp.432](https://aclanthology.org/2024.findings-emnlp.432/) |
| Chunking laporan panjang (informasi di tengah/akhir konteks terabaikan) | Liu et al. (2024), *Lost in the Middle: How Language Models Use Long Contexts*, TACL 12 | [10.1162/tacl_a_00638](https://doi.org/10.1162/tacl_a_00638) |
| Mode *hybrid thinking* Qwen3 (`enable_thinking`, `/no_think`) | Qwen Team (2025), *Qwen3 Technical Report* | [arXiv:2505.09388](https://arxiv.org/abs/2505.09388) |

**Referensi domain (pemetaan CTI → ATT&CK) sebagai pembanding:**

| Karya | Relevansi | DOI / Link |
|---|---|---|
| Orbinato et al. (2022), *Automatic Mapping of Unstructured Cyber Threat Intelligence: An Experimental Study*, ISSRE 2022 | Baseline ML klasik untuk tugas yang sama; sumber dataset CTI-to-MITRE | [10.1109/ISSRE55969.2022.00027](https://doi.org/10.1109/ISSRE55969.2022.00027) |
| Rani et al. (2023), *TTPHunter: Automated Extraction of Actionable Intelligence as TTPs from Narrative Threat Reports*, ACSW 2023 | Ekstraksi TTP berbasis BERT fine-tuned — pembanding pendekatan non-prompting | [10.1145/3579375.3579391](https://doi.org/10.1145/3579375.3579391) |
| Rani et al. (2024), *TTPXHunter*, ACM Digital Threats: Research and Practice | Versi lanjutan TTPHunter | [10.1145/3696427](https://doi.org/10.1145/3696427) |
| Fayyazi & Yang (2023), *On the Uses of Large Language Models to Interpret Ambiguous Cyberattack Descriptions* | Bukti LLM kesulitan pada deskripsi TTP ambigu → memotivasi decision rules eksplisit | [arXiv:2306.14062](https://arxiv.org/abs/2306.14062) |
| Fayyazi et al. (2024), *Advancing TTP Analysis: Harnessing the Power of Encoder-Only and Decoder-Only Language Models with Retrieval Augmented Generation* | Desain paling dekat dengan sistem ini: RAG + decoder LLM untuk pemetaan TTP | [arXiv:2401.00280](https://arxiv.org/abs/2401.00280) |

---

## 6. Daftar Pustaka (siap salin)

Brown, T. B., Mann, B., Ryder, N., Subbiah, M., Kaplan, J., Dhariwal, P., et al. (2020). Language models are few-shot learners. *Advances in Neural Information Processing Systems, 33*, 1877–1901. https://arxiv.org/abs/2005.14165

Bsharat, S. M., Myrzakhan, A., & Shen, Z. (2023). Principled instructions are all you need for questioning LLaMA-1/2, GPT-3.5/4. *arXiv preprint* arXiv:2312.16171. https://arxiv.org/abs/2312.16171

Fayyazi, R., & Yang, S. J. (2023). On the uses of large language models to interpret ambiguous cyberattack descriptions. *arXiv preprint* arXiv:2306.14062. https://arxiv.org/abs/2306.14062

Fayyazi, R., Taghdimi, R., & Yang, S. J. (2024). Advancing TTP analysis: Harnessing the power of encoder-only and decoder-only language models with retrieval augmented generation. *arXiv preprint* arXiv:2401.00280. https://arxiv.org/abs/2401.00280

Ji, Z., Lee, N., Frieske, R., Yu, T., Su, D., Xu, Y., et al. (2023). Survey of hallucination in natural language generation. *ACM Computing Surveys, 55*(12), 1–38. https://doi.org/10.1145/3571730

Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *Advances in Neural Information Processing Systems, 33*, 9459–9474. https://arxiv.org/abs/2005.11401

Liu, N. F., Lin, K., Hewitt, J., Paranjape, A., Bevilacqua, M., Petroni, F., & Liang, P. (2024). Lost in the middle: How language models use long contexts. *Transactions of the Association for Computational Linguistics, 12*, 157–173. https://doi.org/10.1162/tacl_a_00638

Madaan, A., Tandon, N., Gupta, P., Hallinan, S., Gao, L., Wiegreffe, S., et al. (2023). Self-Refine: Iterative refinement with self-feedback. *Advances in Neural Information Processing Systems, 36*. https://arxiv.org/abs/2303.17651

Orbinato, V., Barbaraci, M., Natella, R., & Cotroneo, D. (2022). Automatic mapping of unstructured cyber threat intelligence: An experimental study. *Proceedings of the IEEE 33rd International Symposium on Software Reliability Engineering (ISSRE)*, 181–192. https://doi.org/10.1109/ISSRE55969.2022.00027

Qwen Team. (2025). Qwen3 technical report. *arXiv preprint* arXiv:2505.09388. https://arxiv.org/abs/2505.09388

Rani, N., Saha, B., Maurya, V., & Shukla, S. K. (2023). TTPHunter: Automated extraction of actionable intelligence as TTPs from narrative threat reports. *Proceedings of the 2023 Australasian Computer Science Week (ACSW)*, 126–134. https://doi.org/10.1145/3579375.3579391

Rani, N., Saha, B., Maurya, V., & Shukla, S. K. (2024). TTPXHunter: Actionable threat intelligence extraction as TTPs from finished cyber threat reports. *Digital Threats: Research and Practice*. https://doi.org/10.1145/3696427

Renze, M. (2024). The effect of sampling temperature on problem solving in large language models. *Findings of the Association for Computational Linguistics: EMNLP 2024*, 7346–7356. https://aclanthology.org/2024.findings-emnlp.432/

Tam, Z. R., Wu, C.-K., Tsai, Y.-L., Lin, C.-Y., Lee, H.-y., & Chen, Y.-N. (2024). Let me speak freely? A study on the impact of format restrictions on performance of large language models. *Proceedings of EMNLP 2024: Industry Track*. https://arxiv.org/abs/2408.02442

Willard, B. T., & Louf, R. (2023). Efficient guided generation for large language models. *arXiv preprint* arXiv:2307.09702. https://arxiv.org/abs/2307.09702
