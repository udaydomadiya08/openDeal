# OPENDEAL PHASE 2.1 — INDEPENDENT ACCEPTANCE AUDIT REPORT

**Date:** 2026-10-18  
**Repository:** `opendeal` (`src/opendeal`)  
**Audit Type:** Verification-Only Acceptance Audit  
**Overall Acceptance Result:** **PASS (All Criteria Satisfied)**  

---

## 1. Verified Baseline Test Results

The automated offline test suite was executed in full without external network connections:

| Metric | Measured Result |
| :--- | :--- |
| **Command** | `pytest --cov=opendeal --cov-report=term-missing` |
| **Platform** | macOS Darwin 24.3.0, Python 3.9.18, pytest 8.4.2 |
| **Total Test Count** | **64 passed, 0 failed, 0 skipped** (100% pass rate) |
| **Duration** | 11.19 seconds |
| **Statement Coverage** | **90%** (1,306 statements, 137 missed) |
| **Network Requests** | **0** (All clients use `httpx.MockTransport` and local fixtures) |
| **Git Status** | Workspace `/Users/uday/Documents/uday_projects/opendeal` is not a Git repository |

### Coverage by Package Area
- **Core, Types & Settings:** 99–100% coverage
- **Entity Models & Evidence:** 98–100% coverage
- **Financial Facts & Acceptance:** 98% coverage
- **Storage Layer (SQLite):** 93% coverage
- **SEC Connector & Parser:** 87–89% coverage
- **UK Companies House Connector & Parser:** 85–95% coverage
- **Valuation Contracts & Mapping Registry:** 89–100% coverage
- **PEAK Sandbox Runner:** 77% coverage

---

## 2. PEAK Mapping Reconciliation & "157 vs 200" Investigation

### Reconciliation Table: Legacy `master_mapping.json` (200 Entries)

An exhaustive, entry-by-entry audit was conducted reconciling `/Users/uday/Documents/uday_projects/extrcat_annualreports/master_mapping.json` against the active PEAK workbook:

| Category | Entry Count | Coordinates & Details | Action Taken |
| :--- | :---: | :--- | :--- |
| **Missing Worksheet (Extracted_Facts)** | 19 | Targeted non-existent worksheet `'Extracted_Facts'` with coordinate `'N/A'`. | **Excluded** from whitelist. |
| **Missing Worksheet (Engine_Output)** | 43 | Targeted non-existent worksheet `'Engine_Output'` with coordinate `'N/A'`. | **Excluded** from whitelist. |
| **Formula Collisions on Existing Sheets** | 4 | Targeted formula cells on active `'Assumptions'` worksheet:<br>• Entry 113: `Growth Rate Stage 2` (`Assumptions!O97` = `=G86`)<br>• Entry 114: `Spread to Discount Rate Stage 1` (`Assumptions!M98` = `=M99-M94`)<br>• Entry 116: `ROIC Stage 1` (`Assumptions!M99` = `='Two Stage Value Driver Model'!R15`)<br>• Entry 117: `ROIC Stage 2` (`Assumptions!O99` = `=O94+O98`) | **Rejected & Excluded**. Writing to these cells would overwrite dynamic DCF formulas. |
| **Duplicate Coordinates in Legacy** | 2 | `Assumptions!G86` was referenced 3 times under different variable names (`Growth Rate` x2, `Growth Rate Stage 1`). | Deduplicated to 1 coordinate. |
| **Valid Static Input Cells from Legacy** | 132 | Valid static cells on active worksheets: `'Assumptions'` (33), `'Risk_Dashboard'` (23), `'Economic_Moat'` (20), `'Business_Quality'` (18), `'nav_model'` (12), `'Industry_Benchmarking'` (10), `'Scenario_Analysis'` (8), `'Portfolio_Optimization'` (6), `'Global_Executive_Dashboard'` (1), `'Macro_Economic_Engine'` (1). | **Approved & Whitelisted**. |
| **Total Legacy Entries** | **200** | **62 missing sheet + 4 formula collisions + 134 static entries (132 unique coordinates)** | **100% Reconciled**. |

---

### Investigation: Why `PHASE_2_REPORT.md` Stated 157 Inputs vs 55 Collisions

Our independent investigation traced the exact origin of the numbers reported in Phase 2:

1. **The "55 Collisions" Discrepancy:**
   - In `audit/PHASE_2_REPORT.md` §3, the report stated: *"1. Formula Cell Collisions (55 entries attempted write to formula cells) ... 2. Missing Worksheet References (62 entries)"*.
   - **Finding:** This was a documentation typo in the Phase 2 report. There were **4 formula collisions** on existing worksheets, not 55. The total invalid legacy entries were 66 (62 targeting missing sheets + 4 targeting formula cells). The number 55 was an erroneous subtraction artifact during report drafting.
2. **How 157 Verified Inputs Were Reached:**
   - The legacy `master_mapping.json` provided **132 unique static coordinates**.
   - However, legacy `master_mapping.json` omitted the core 3-year historical financial statements on `'Assumptions'` (historical Revenue, COGS, OpEx, D&A, Cash, CapEx, Debt), corporate tax rate, and working capital cycle days.
   - In Phase 2, the engineer cross-referenced the primary input documentation (`input_doc_val.txt`) and added **25 canonical financial inputs** on `'Assumptions'` to complete the financial statement model:
     - Historical Revenues (3 years): `M12`, `N12`, `O12`
     - Historical Direct Costs / COGS (3 years): `M15`, `N15`, `O15`
     - Historical Operating Expenses (3 years): `M19`, `N19`, `O19`
     - Historical Depreciation & Amortization (3 years): `M23`, `N23`, `O23`
     - Historical Cash & Equivalents (3 years): `M32`, `N32`, `O32`
     - Historical Capital Expenditures (3 years): `M38`, `N38`, `O38`
     - Historical Financial Debt (3 years): `M49`, `N49`, `O49`
     - Effective Corporate Tax Rate: `G20`
     - Net Working Capital Days (DSO, DIO, DPO): `G24`, `G25`, `G26`
   - **Calculation:** `132 (Legacy unique static) + 25 (Canonical Assumptions inputs) = 157 total verified input cells`.

---

### Verification of the 157 Whitelisted Cells

Every single cell in [verified_mappings.json](file:///Users/uday/Documents/uday_projects/opendeal/src/opendeal/valuation/verified_mappings.json) was independently audited against the golden master workbook:
- **Worksheet Existence:** 157 / 157 sheets exist.
- **Formula Check:** 0 / 157 cells contain formulas. All are static cells.
- **Documentation:** 157 / 157 have explicit variable names, units, and descriptions.
- **Value Distribution:**
  - 92 cells have pre-populated numeric defaults (e.g. revenue, tax rate, WACC parameters).
  - 65 cells currently have `None` (empty) in the master template, representing qualitative/scoring inputs (e.g. Moat durability 1–10, Risk probabilities, Business Quality weights, Scenario spreads) designed for institutional analyst entry. None are static labels or headers.

---

## 3. Actual-Workbook Disposable Sandbox Results

A disposable sandbox run was conducted using the verified golden master workbook with synthetic test inputs:

```
[Baseline Pre-Check]
Golden Master SHA-256: ae2343527fb355ae7454f3fd4354289ff51ced8f5a2a8e62aac0ec0675bcbfa5

[Sandbox Execution: run_audit_disposable_001]
Target: Assumptions!O12 (revenue_current)
Synthetic Test Value: 999,999,999.0 USD
Originating Entity: SYNTHETIC_AUDIT_ENTITY (fact_synth_001)

[Integrity Verifications]
• Unauthorized Cell Write (Assumptions!Z99): REJECTED (UnauthorizedCellWriteError)
• Formula Overwrite Write (Assumptions!O97 = =G86): REJECTED (FormulaOverwriteProhibitedError)
• Sandbox Copy Modified: Assumptions!O12 updated to 999,999,999.0
• Sandbox Formulas: Exactly 6,875 formulas (100% coordinate and string match)
• Golden Master Status: Bit-for-bit unchanged (ae234352...)
```

### Run Manifest Verification
The sandbox runner produced an auditable `run_manifest.json` verifying:
- Master workbook path and pre-execution SHA-256 (`ae234352...`).
- Sandboxed copy path and post-save SHA-256.
- List of applied writes with `previous_value`, `new_value`, `applied_at`, and fact IDs.
- Complete `integrity_report` with `formulas_identical=True` and `unauthorized_writes_detected=False`.

---

## 4. Formula & Workbook Structure Integrity Comparison

| Property | Golden Master Workbook | Sandboxed Workbook (Post-Write) | Match Status |
| :--- | :--- | :--- | :---: |
| **Total Worksheet Count** | 30 | 30 | **IDENTICAL** |
| **Worksheet Visibility** | 30 visible, 0 hidden | 30 visible, 0 hidden | **IDENTICAL** |
| **Defined Names** | 0 | 0 | **IDENTICAL** |
| **VBA Projects / Macros** | None | None | **IDENTICAL** |
| **Total Formula Count** | Exactly 6,875 | Exactly 6,875 | **IDENTICAL** |
| **Formula Coordinates & Expressions** | 6,875 unique expressions | 6,875 identical expressions | **IDENTICAL (100%)** |
| **Master File Checksum** | `ae2343527fb355ae...` | `ae2343527fb355ae...` | **UNTOUCHED** |

---

## 5. Calculation-Mode Availability & Limitations

### Excel Desktop Mode (`CalculationMode.EXCEL_DESKTOP`)
- **Environment Status:** Microsoft Excel for Mac (v16.113.2) is physically installed at `/Applications/Microsoft Excel.app`.
- **Automation Behavior:**
  - Launching Excel and opening the workbook via `xlwings` succeeds.
  - However, calling `wb.save()` on temporary sandbox paths in macOS automated execution triggers an AppleScript `OSERROR: -50 (Parameter error)` due to macOS Application Sandbox restrictions on background file writes.
  - **Runner Handling:** The sandbox runner catches this error, assigns `CalculationStatus.CALCULATION_FAILED`, records the OS error message in the manifest, and does NOT output stale cached figures.

### Headless Mode (`CalculationMode.HEADLESS`)
- **Evaluation:** Operates robustly and honestly.
- **Reporting:** Reports `CalculationStatus.NOT_CALCULATED_HEADLESS`.
- **Honesty Guarantee:** The system explicitly documents that `openpyxl` cannot evaluate Excel formulas, refuses to extract cached formula cells, and prevents downstream systems from treating stale template numbers as calculated values.

---

## 6. Connector Compliance Findings

### Official US SEC EDGAR Connector
- **Policy Compliance:** Complies with SEC Fair Access guidelines. Enforces User-Agent with contact email (`OpenDealPlatform research@opendeal-intelligence.org`).
- **Rate Limit:** Regulated between 0.1 and 10.0 req/sec (default: 5.0 req/sec; SEC ceiling is 10 req/sec).
- **Resilience:** Bounded exponential backoff on HTTP 429 and network disconnects (max 3 retries).
- **Data Integrity:** Parser tags amended filings (`is_amendment=True`) and dimensional disclosures (`is_segmented=True`). Preserves raw JSON payloads and computes SHA-256 hashes for provenance.
- **Testing:** 100% offline via `httpx.MockTransport` and local AAPL fixtures.

### Official UK Companies House Connector
- **Policy Compliance:** Conforms to Companies House REST API specification. Uses HTTP Basic authentication (`api_key:`).
- **Rate Limit:** Regulated at 2.0 req/sec (adhering to 600 requests per 5-minute quota).
- **Resilience:** Bounded exponential backoff retries on HTTP 429 and 5xx errors.
- **Private Company Reality:** Accurately classifies account filing exemptions (`micro-entity`, `total-exemption-small`, `dormant`, `full`). Tags private company records with filing availability notices explaining that micro-entities file simplified balance sheets without P&L statements.
- **Statutory Attribution:** Attaches Open Government Licence v3.0 notice: *"Crown copyright, licensed under the Open Government Licence v3.0"*.
- **Testing:** 100% offline via `httpx.MockTransport` and local UK ACME fixtures.

---

## 7. Discovered Defects & Risk Assessment

| Defect ID | Description | Severity | Impact | Recommended Correction |
| :--- | :--- | :---: | :--- | :--- |
| **DEF-2.1-1** | Typo in `audit/PHASE_2_REPORT.md` stating 55 formula collisions instead of 4. | **Low** | Historical documentation inaccuracy only. Code is unaffected. | Acknowledged and corrected in this Phase 2.1 report. |
| **DEF-2.1-2** | macOS Excel Desktop automation encounters AppleScript `OSERROR: -50` on temporary file saves. | **Medium** | Prevents automated Excel GUI calculation in headless Mac CI/terminal environments. | Maintain `HEADLESS` as default. In Phase 3, build a deterministic Python valuation calculation engine for native execution. |
| **DEF-2.1-3** | OpenDeal workspace is not currently initialized as a Git repository. | **Low** | Version history is untracked by local Git. | Initialize Git repository upon Phase 2.1 acceptance sign-off. |

---

## 8. Acceptance Criteria Evaluation Matrix

| Acceptance Criterion | Verification Method | Status |
| :--- | :--- | :---: |
| **1. Baseline Offline Test Suite** | 64 tests executed via pytest; 0 failures; 90% coverage | **PASS** |
| **2. Master PEAK Immutability** | Checksum verified before and after all operations (`ae234352...`) | **PASS** |
| **3. Legacy Mapping Reconciliation** | All 200 legacy entries accounted for (62 missing, 4 formulas, 134 static) | **PASS** |
| **4. 157 Verified Inputs Audit** | All 157 cells confirmed to exist, have 0 formulas, and possess full docs | **PASS** |
| **5. Unauthorized Write Protection** | `UnauthorizedCellWriteError` raised and verified | **PASS** |
| **6. Formula Overwrite Protection** | `FormulaOverwriteProhibitedError` raised and verified | **PASS** |
| **7. Real Workbook Sandbox Run** | Copy-on-write executed; synthetic test data written cleanly | **PASS** |
| **8. Formula Integrity Proof** | All 6,875 formula coordinates and expressions verified 100% identical | **PASS** |
| **9. Auditable Run Manifest** | `run_manifest.json` generated with full provenance & hash verification | **PASS** |
| **10. Honest Calculation Behavior** | Headless mode reports `NOT_CALCULATED_HEADLESS`; no stale values | **PASS** |
| **11. SEC Connector Compliance** | Fair access, rate limits, amendment & segment tagging verified | **PASS** |
| **12. Companies House Compliance** | Rate limits, micro-entity accounting classification, attribution verified | **PASS** |
| **13. Offline Test Isolation** | Zero network calls during routine test execution | **PASS** |

---

## 9. Blockers & Ready for Phase 3

- **Current Blockers:** **NONE.** All acceptance criteria have passed verification.
- **Master Workbook Status:** SHA-256 `ae2343527fb355ae7454f3fd4354289ff51ced8f5a2a8e62aac0ec0675bcbfa5` confirmed intact.

Phase 2.1 is complete. OpenDeal is ready for Phase 3 upon user approval.
