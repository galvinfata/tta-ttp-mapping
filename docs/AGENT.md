# AGENT.md - CTI-to-MITRE ATT&CK TTP Mapping System

## Project Purpose

Sistem untuk **memetakan Cyber Threat Intelligence (CTI) reports ke MITRE ATT&CK framework** menggunakan Local LLM. Setiap report dianalisis untuk mengidentifikasi tactics (TA####) dan techniques (T####) yang sesuai dengan attacker behavior yang dijelaskan.

**Key Outcome:** 
- Input: CTI report (text)
- Output: STIX 2.1 bundle dengan mapped tactics & techniques

---

## Domain Knowledge: CTI & MITRE ATT&CK

### Apa itu MITRE ATT&CK?

**Framework berbasis pengalaman real-world** yang mendokumentasikan tactic & technique yang digunakan adversaries:

- **Tactic (TA####)**: High-level attack phase/objective
  - Contoh: TA0001 (Initial Access), TA0006 (Credential Access), TA0011 (C2)
  - Total: 14 tactics dalam enterprise-attack.json
  - Format ID: TA0001-TA0043

- **Technique (T####)**: Spesifik action yang attacker lakukan
  - Contoh: T1566 (Phishing), T1055 (Process Injection), T1059 (Command & Scripting Interpreter)
  - Bisa punya subtechniques: T1566.002 (Phishing - Spearphishing Link)
  - Total: 750 attack-patterns dalam enterprise-attack.json
  - Format ID: T#### atau T####.###

### Mapping Task

Report text seperti:
> "Attacker mengirim phishing email dengan attachment Excel yang mengeksekusi PowerShell..."

Harus di-map ke:
```json
{
  "tactics": ["TA0001", "TA0002"],
  "techniques": ["T1566.001", "T1059.1", "T1547.008"]
}
```

**Challenge:** Report panjang, banyak noise, LLM bisa error JSON parsing.

---

## System Architecture

### Pipeline Flow (LangGraph)

```
Report Input
    ↓
[_input_report_node] → Extract report_id, text, ground_truth
    ↓
[_tactic_extraction_node] → tactic_agent.identify_tactics()
    ↓
[_technique_extraction_node] → technique_agent.extract_techniques()
    ↓
[_post_process_node] → reconcile_results() → validate_techniques() → build_stix_bundle()
    ↓
STIX Output
```

### Agent Components

#### 1. **Tactic Agent** (`src/tactic_agent.py`)

**Peran:** Identify TA#### (14 tactics) dari report text

**Key Functions:**
- `create_tactic_agent(model_name=None)`: Initialize OpenAI client
- `identify_tactics(agent, report_text, attck_tactics)`: Main inference
- `_extract_json_array()`: Parse JSON dari model response
- `_extract_tactic_ids_from_text()`: Regex fallback (\bTA\d{4}\b)

**Model Output Format (Dijanjikan):**
```json
["TA0001", "TA0043", "TA0006"]
```

**Retry Logic:**
- MAX_RETRIES_PER_MODEL = 3
- Exponential backoff: 1s, 2s, 4s
- Fallback: Regex extraction jika JSON parsing fail

#### 2. **Technique Agent** (`src/technique_agent.py`)

**Peran:** Identify T#### (750 techniques) dengan semantic relevance

**Key Functions:**
- `create_technique_agent(model_name=None)`: Initialize OpenAI client
- `extract_techniques(agent, report_text, attck_techniques)`: Main inference
- `_retrieve_candidate_techniques()`: TF-IDF retrieval (top-k filtering)
- `_build_technique_document()`: Index building untuk TF-IDF

**Workflow:**
1. TF-IDF vectorizer fits pada semua technique descriptions
2. Compute cosine_similarity dengan report_text
3. Retrieve top-k candidates (default: 10) → pass ke LLM
4. LLM select dari candidates → output T####

**Why Retrieval?** 
- Reduce hallucination (LLM dipandu dengan relevant candidates)
- Faster inference (750 techniques → 10 candidates)
- Better accuracy

**Model Output Format (Dijanjikan):**
```json
["T1566.001", "T1059.1", "T1195.003"]
```

#### 3. **Data Loader** (`src/data_loader.py`)

**Auto-converts PDF → JSON** sebelum processing:
- Detects `.pdf` files
- Extracts text via `pypdf`
- Creates `filename__pdf.json` 
- Skip jika JSON sudah exist & newer

#### 4. **Reconciler** (`src/reconciler.py`)

**Memperbaiki technique IDs** yang invalid:
- Mapping dari subtechnique → parent technique jika needed
- Deduplication
- Filtering based on tactics

#### 5. **Validator** (`src/validator.py`)

**Final validation step:**
- Check setiap technique ada di enterprise-attack.json
- Categorize: valid vs invalid
- Return only valid techniques

#### 6. **STIX Builder** (`src/stix_builder.py`)

**Output STIX 2.1 bundle:**
- Relationship objects linking techniques
- Proper STIX format untuk integration
- Timestamp + metadata

### Data Flow Summary

```
enterprise-attack.json (750 techniques, 14 tactics)
         ↓
     [TF-IDF Index]
         ↓
    Report Text
    ↓ (tactic_agent)
    Tactics → [TA0001, TA0006]
    ↓
    ↓ (technique_agent)
    Candidate Retrieval (top-10)
    LLM Selection
    ↓ Techniques → [T1566.001, T1059.1]
    ↓ (reconciler)
    ↓ Reconciled → [T1566.001, T1059.001]
    ↓ (validator)
    ↓ Valid ✓
    ↓ (stix_builder)
    STIX Bundle → Output
```

---

## Configuration & Environment Variables

### Critical Variables (Required)

```ini
# LM Studio Connection
LOCAL_LLM_BASE_URL=http://100.100.211.39:1234
LOCAL_LLM_MODEL=qwen/qwen3-4b  # Default model for both agents

# Optional: Per-Agent Model Override
TACTIC_LLM_MODEL=qwen/qwen3-4b       # Override for tactic agent
TECHNIQUE_LLM_MODEL=qwen/qwen3-4b    # Override for technique agent

# Optional: API Key (jika server butuh auth)
LOCAL_LLM_API_KEY=
```

### Tuning Variables (Performance)

```ini
# Context Window & Token Limits
LOCAL_LLM_REPORT_MAX_CHARS=400          # Report excerpt length (smaller = faster, less error)
LOCAL_LLM_MAX_TOKENS_TACTIC=80          # Max tokens untuk tactic agent
LOCAL_LLM_MAX_TOKENS_TECHNIQUE=80       # Max tokens untuk technique agent
LLM_REQUEST_TIMEOUT_SECONDS=300         # Request timeout

# Retrieval Tuning
LOCAL_LLM_CANDIDATE_TOP_K=10            # Top-k candidates untuk TF-IDF retrieval
TECHNIQUE_CANDIDATE_TOP_K=40            # Fallback candidate count
TECHNIQUE_INCLUDE_SUBTECHNIQUES=true    # Include T1234.001 format

# Debugging
DEBUG_AGENT=false                        # Set true untuk verbose logging
```

### Workflow: Model Switching

**Scenario 1: Both agents same model** (Current)
```ini
LOCAL_LLM_MODEL=qwen/qwen3-4b
# Kedua agent gunakan qwen
```

**Scenario 2: Runtime override** (Programmatic)
```python
from src.tactic_agent import create_tactic_agent
from src.technique_agent import create_technique_agent

tactic_agent = create_tactic_agent(model_name="qwen/qwen3-4b")
technique_agent = create_technique_agent(model_name="qwen/qwen3-4b")
```

---

## Known Constraints & Assumptions

### 1. **Model Reliability** ⚠️

**qwen/qwen3-4b characteristics:**
- ✅ Fast (inference < 5s)
- ✅ Compact (4B params)
- ❌ JSON compliance ~95% (need fallback parser)
- ❌ Context sensitivity (1500 chars → errors, 400 chars → stable)
- ❌ Inconsistent output format

**Implication:** 
- ALWAYS have fallback regex parser active
- NEVER rely on JSON parse only
- Validate output dengan regex extraction

### 2. **Context Window Sensitivity**

**Finding dari optimization:**
- Original: 1500 chars report excerpt → frequent timeouts & empty responses
- Current: 400 chars → 95% success rate
- Rationale: Small models get confused dengan large context

**Recommendation:** Keep LOCAL_LLM_REPORT_MAX_CHARS ≤ 400

### 3. **TF-IDF Retrieval Quality**

**Current setup:**
- TfidfVectorizer(stop_words="english", ngram_range=(1,2), max_features=20000)
- Cosine similarity scoring
- Top-k=10 passed to LLM

**Tradeoff:**
- top-k=10: Faster, less noise
- top-k=20+: More coverage, risk hallucination

**Known issue:** Ground truth technique bisa not in top-10 (recall issue)

### 4. **Dataset Format Assumptions**

**TRAM II Reports:**
```json
{
  "id": "report_name",
  "text": "CTI report text...",
  "techniques": ["T1566", "T1059"]  // Ground truth for evaluation
}
```

**enterprise-attack.json:**
- STIX 2.1 bundle format
- attack-pattern objects dengan external_id (T####)
- x-mitre-tactic objects dengan external_id (TA####)

**Auto-conversion:**
- PDF → JSON (filename__pdf.json)
- Assumes PDF has extractable text (not scanned image)

### 5. **Retry & Fallback Strategy**

**Implemented:**
1. Try JSON parse with retries (3 attempts, exponential backoff)
2. If fails, fallback regex extraction
3. If regex empty, return empty list (vs exception)

**Philosophy:** Better incomplete result than crash

---

## Implementation Standards

### 1. **Environment Variables Only (No Hardcoding)**

```python
# ✅ GOOD
model = os.getenv("LOCAL_LLM_MODEL", "qwen/qwen3-4b")

# ❌ BAD
model = "qwen/qwen3-4b"  # Hardcoded!
```

### 2. **Always Support DEBUG Mode**

```python
DEBUG_MODE = os.getenv("DEBUG_AGENT", "false").lower() == "true"

if DEBUG_MODE:
    print(f"[DEBUG] Model output (first 200 chars): {response[:200]}")
```

### 3. **Fallback Parser Required for JSON**

```python
# ✅ Pattern: Try JSON, fallback regex
try:
    ids = json.loads(response)
except json.JSONDecodeError:
    ids = _extract_ids_from_text(response)  # Regex fallback
```

### 4. **Transient Error Detection**

```python
# Retry jika error tergolong transient
if _is_transient_error(str(error)):
    # Retry dengan backoff
else:
    # Fatal, propagate
```

### 5. **Type Hints & Documentation**

```python
def identify_tactics(
    agent: dict, 
    report_text: str, 
    attck_tactics: dict
) -> list[str]:
    """
    Identify MITRE ATT&CK tactics dari report.
    
    Args:
        agent: Tactic agent dict dengan 'client' & 'model'
        report_text: Full CTI report text
        attck_tactics: Dict dari TA#### → tactic name
    
    Returns:
        List of tactic IDs: ["TA0001", "TA0043"]
    
    Raises:
        ValueError: If all retries exhausted
    """
```

---

## Debugging Guide

### Issue: "Expecting value: line 1 column 1 (char 0)" Error

**Root Cause:** Model returned empty or non-JSON response

**Diagnosis:**
```bash
DEBUG_AGENT=true python main.py
```
Look untuk `[DEBUG] Model output` messages

**Solution:**
1. Check LM Studio connection: `curl http://100.100.211.39:1234/v1/models`
2. Reduce context: `LOCAL_LLM_REPORT_MAX_CHARS=300`
3. Simplify prompt (sudah dilakukan)
4. Check model availability at LM Studio

### Issue: Empty Tactics/Techniques

**Possible Causes:**
1. Model output empty
2. Regex fallback not matching IDs
3. Validation filtering all out

**Debug:**
```python
# Add logging di orchestrator.py
print(f"Raw tactic output: {tactics}")
print(f"Raw technique output: {techniques}")
```

### Issue: Low Metrics (P, R, F1)

**Likely Causes:**
1. TF-IDF retrieval missing ground truth techniques (recall issue)
2. Model not sensitive enough to CTI wording
3. Prompt too generic

**Investigation:**
```bash
python scripts/run_full_pipeline.py
# Check results_all_*.json metrics
# Compare precision vs recall
```

### Verify Setup

```bash
# 1. Check LM Studio
curl http://100.100.211.39:1234/v1/models

# 2. Check enterprise-attack.json load
python -c "import json; d=json.load(open('data/mitre_cti/enterprise-attack.json')); print(f'Objects: {len(d[\"objects\"])}')"

# 3. Check dataset
ls -la data/tram_ii/*.json | head -5

# 4. Run smoke test
python main.py  # Should process first 5 reports without errors
```

---

## File Structure

```
tta-ttp-mapping/
├── main.py                      # Entry point (first 5 reports)
├── src/
│   ├── tactic_agent.py         # Tactic extraction (TA####)
│   ├── technique_agent.py       # Technique extraction (T####)
│   ├── orchestrator.py          # LangGraph pipeline
│   ├── data_loader.py           # Load TRAM II + auto-convert PDF
│   ├── reconciler.py            # Technique reconciliation
│   ├── validator.py             # Final validation
│   ├── stix_builder.py          # STIX 2.1 output
│   └── evaluator.py             # Metrics (P, R, F1)
├── data/
│   ├── mitre_cti/
│   │   └── enterprise-attack.json  # Knowledge base (750 techniques)
│   └── tram_ii/                    # CTI reports (151 files)
├── results/
│   └── metrics/
│   └── predictions/
│       └── results.json            # Output predictions
├── .env                            # Config (gitignored)
├── .env.example                    # Template
├── requirements.txt                # Dependencies
├── README.md                       # Quick start
├── SETUP_GUIDE.md                 # Detailed setup
├── AGENT.md                        # This file
└── [tests]
    └── test_github_models.py       # Smoke test
```

---

## Performance Metrics & Baselines

### Current Baseline (After Optimization)

**From 5-report test run:**
- Processing time: ~5-8 seconds per report
- Tactic extraction success: 100% (via fallback parser)
- Technique extraction success: 95% (1-2 JSON errors recovered)
- Fallback parser activation rate: ~10% per agent

**From full dataset run (baseline):**
- Precision: 0.0807 (before optimization)
- Recall: 0.0118
- F1: 0.0206

*Note: Low metrics indicate room for improvement in retrieval tuning & prompt engineering*

### Expected After Model Switching

Perlu run full pipeline dengan different model combinations untuk establish new baseline.

---

## Future Enhancement Areas

1. **Prompt Engineering:** Lebih specific instructions per domain
2. **Retrieval Tuning:** Optimize top-k selection & TF-IDF parameters
3. **Multi-Model Ensemble:** Vote antara beberapa model yang tersedia
4. **Confidence Scoring:** Return confidence level per technique
5. **Domain Fine-tuning:** Smaller models fine-tuned on CTI data

---

## Security Notes

⚠️ **Keep .env file gitignored**
- Never commit API keys or tokens
- Use .env.example untuk template
- Rotate keys regularly jika exposed

---

## References

- **MITRE ATT&CK:** https://attack.mitre.org/
- **Enterprise-attack.json:** STIX 2.1 bundle format
- **TRAM II Dataset:** 151 CTI reports untuk training/evaluation
- **LM Studio:** Local LLM server dengan OpenAI-compatible API
- **LangGraph:** Python orchestration framework untuk agentic workflows
