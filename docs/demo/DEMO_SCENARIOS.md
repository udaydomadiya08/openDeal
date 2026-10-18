# OpenDeal — Deterministic Demo Scenarios & Recording Guide

This document specifies three institutional demonstration scenarios with **mathematically verified, reproducible outputs** computed by OpenDeal's deterministic valuation engine. Use these scenarios during live interviews, automated testing, or video walkthroughs.

> **Institutional Note on Data Provenance**:
> - **Inputs**: All company profiles, reported financials, and evidence excerpts are **synthetic demonstration fixtures** clearly identified with `DEMO-*` identifiers and synthetic disclaimers. OpenDeal does not fabricate official regulatory citations or SEC filings.
> - **Outputs**: All financial valuation numbers (EV, Equity Value, WACC, IRR, MoIC) are **algorithmically computed in real-time** by OpenDeal's deterministic financial models. No valuation outputs are pre-canned or hardcoded.

---

## Scenario 1: Growth Tech DCF & Cost of Capital (DEMO-NEXUS)

### Objective
Demonstrate an institutional Discounted Cash Flow (DCF) model and Weighted Average Cost of Capital (WACC) valuation for a high-growth cloud infrastructure provider (**Nexus Cloud Technologies Inc.**), showing full lineage from operating assumptions to per-share intrinsic value.

### Subject Profile
- **Entity**: Nexus Cloud Technologies Inc. (`DEMO-NEXUS`)
- **Profile**: High-growth SaaS / Cloud Infrastructure provider
- **Share Count**: 25,000,000 diluted shares
- **Balance Sheet Adjustments**: Cash = $55,000,000.00 | Total Debt = $20,000,000.00 (Net Cash = $35,000,000.00)

### Model Inputs
| Parameter | Value | Rationale |
|:---|:---|:---|
| **Projection Period** | 5 Years | Standard institutional explicit forecast window |
| **Year 1 FCF (2025E)** | $32,000,000.00 | Projected operating cash flow minus capex |
| **Year 2 FCF (2026E)** | $40,000,000.00 | 25.0% revenue expansion |
| **Year 3 FCF (2027E)** | $49,000,000.00 | 22.5% revenue expansion |
| **Year 4 FCF (2028E)** | $58,000,000.00 | 18.4% expansion, margin operating leverage |
| **Year 5 FCF (2029E)** | $67,000,000.00 | Terminal steady-state transition |
| **Discount Rate (WACC)** | **9.25%** | Market Risk Premium 5.5%, Beta 1.15, Cost of Debt 6.2% |
| **Terminal Growth Rate ($g$)** | **2.50%** | Long-term risk-free GDP trend |
| **Discounting Convention** | Mid-Year | Standard institutional convention |

### Verified Deterministic Engine Outputs
When calculated via `opendeal.valuation.calculate_dcf`:

```
Present Value of Explicit Cash Flows (Y1-Y5):  $192,473,418.55
Enterprise Terminal Value (Undiscounted):     $1,017,407,407.41
Present Value of Terminal Value:              $653,713,821.02
--------------------------------------------------------------
Calculated Enterprise Value (EV):             $846,187,239.57
(+) Cash & Equivalents:                       +$55,000,000.00
(-) Total Debt:                                -$20,000,000.00
--------------------------------------------------------------
Calculated Implied Equity Value:              $881,187,239.57
Diluted Shares Outstanding:                     25,000,000
--------------------------------------------------------------
Implied Intrinsic Share Price:                $35.25
Calculation Status:                           COMPLETED (Deterministic)
```

### Execution Steps
1. **Web Dashboard**:
   - Navigate to `/valuation` in the demo interface.
   - Click the preset button: **"Load DEMO-NEXUS (Growth Tech DCF)"**.
   - Verify all input fields auto-populate.
   - Click **"Calculate Valuation"**.
   - Confirm Enterprise Value displays `$846,187,239.57` and Implied Share Price displays `$35.25`.
2. **Python API / CLI**:
   ```python
   from opendeal.valuation import DCFInput, DiscountingConvention, calculate_dcf
   inp = DCFInput(
       forecast_cash_flows=[32e6, 40e6, 49e6, 58e6, 67e6],
       discount_rate=0.0925,
       terminal_growth_rate=0.025,
       cash_and_equivalents=55e6,
       total_debt=20e6,
       shares_outstanding=25e6,
       discounting_convention=DiscountingConvention.MID_YEAR,
   )
   res = calculate_dcf(inp)
   assert round(res.implied_share_price, 2) == 35.25
   ```

---

## Scenario 2: Industrial Buyout & Debt Paydown LBO (DEMO-APEX)

### Objective
Demonstrate an institutional multi-tranche Leveraged Buyout (LBO) analysis on **Apex Industrial Dynamics Plc**, showcasing priority cash sweeps, debt amortization, and private equity return metrics (MoIC and IRR).

### Subject Profile
- **Entity**: Apex Industrial Dynamics Plc (`DEMO-APEX`)
- **Profile**: Established precision manufacturing company with stable, predictable EBITDA margins ($42M 2024 EBITDA)

### Model Inputs
| Parameter | Value | Note |
|:---|:---|:---|
| **Purchase Enterprise Value** | $1,000.0M | Entry transaction enterprise value |
| **Refinanced Existing Debt** | $100.0M | Cleared at acquisition |
| **Transaction & Financing Fees**| $25.0M | $15M advisory/legal + $10M financing |
| **Minimum Cash Reserve** | $15.0M | Retained balance sheet liquidity |
| **Total Uses** | **$1,140.0M** | Exactly balanced by sources |
| **Debt Tranche 1 (Term Loan A)**| $500.0M | 6.0% coupon, 5.0% ($25M/yr) mandatory amortization, Priority 1 |
| **Debt Tranche 2 (Sub Notes)** | $200.0M | 10.0% coupon, 0.0% mandatory amortization, Priority 2 |
| **Total Starting Debt** | **$700.0M** | 61.4% leverage |
| **Sponsor Equity Invested** | **$440.0M** | Required equity check to balance sources & uses |
| **Holding Period** | 5 Years | Exit in Year 5 |
| **Operating Projections (EBITDA)**| Y1: $180M, Y2: $195M, Y3: $210M, Y4: $225M, Y5: $240M | Consistent 7.5% - 8.3% EBITDA growth |
| **Exit Multiple** | **7.5x** | EV / Y5 EBITDA ($240M × 7.5 = $1,800.0M Exit EV) |
| **Cash Sweep** | 100.0% | Free cash flow swept to prepay Senior Term Loan A |

### Verified Deterministic Engine Outputs
When calculated via `opendeal.valuation.calculate_lbo`:

```
Sources & Uses Imbalance:                     $0.00 (Balanced: $1,140.0M Sources == $1,140.0M Uses)
Initial Total Debt:                           $700.00M
Senior Term Loan A Amortization:              Paid down from $500.0M to $66.51M
Subordinated Notes Amortization:              $200.0M (Unchanged - Senior takes 100% sweep)
Ending Total Debt (at Exit):                  $266.51M
Net Debt Reduction:                           -$433.49M (-61.9% debt de-leveraging)
--------------------------------------------------------------
Exit Enterprise Value (7.5x × $240M):         $1,800.00M
Less Net Ending Debt:                         -$251.51M ($266.51M debt - $15.0M min cash)
--------------------------------------------------------------
Exit Equity Proceeds to Sponsor:              $1,548.49M
Sponsor Entry Equity:                         $440.00M
--------------------------------------------------------------
Sponsor MoIC (Multiple on Invested Capital):  3.52x
Sponsor Annualized IRR:                       28.61%
Calculation Status:                           COMPLETED (Deterministic)
```

### Execution Steps
1. **Web Dashboard**:
   - Navigate to `/valuation` in the demo interface.
   - Select model: **LBO / Buyout**.
   - Review pre-loaded debt tranches and operating projections.
   - Click **"Run LBO Model"**.
   - Verify debt paydown schedule: Term Loan A amortizes down to `$66.51M`, MoIC displays `3.52x`, and IRR displays `28.61%`.

---

## Scenario 3: M&A Discovery Screening & Transparent Missing-Data Handling

### Objective
Demonstrate OpenDeal's institutional screening and evidence engine:
1. Explainable eligibility criteria evaluation against real financial thresholds.
2. Transparent missing-data governance: distinguishing missing disclosures from fabricated numbers.
3. Cryptographic provenance verification with SHA-256 evidence links.

### Pre-Seeded Discovery Screening Run (`RUN-DEMO-001`)
- **Criteria**:
  - `min_revenue`: $20,000,000.00
  - `require_active`: `True`
  - `jurisdictions`: `["US", "DE", "GB", "CH"]`
  - `evidence_quality`: `VALIDATED`

### Evaluated Candidates & Deterministic Outcomes

| Candidate | Entity ID | 2024 Revenue | Eligibility | Deterministic Explanation |
|:---|:---|:---:|:---:|:---|
| **Apex Industrial Dynamics** | `DEMO-APEX` | $190,000,000 | **ELIGIBLE (Rank 1)** | Exceeds revenue threshold ($190M > $20M). Full 3-year SEC 10-K evidence anchors. |
| **Nexus Cloud Tech** | `DEMO-NEXUS` | $125,000,000 | **ELIGIBLE (Rank 2)** | Exceeds threshold ($125M > $20M). SaaS operating metrics verified. |
| **Meridian Logistics** | `DEMO-MERIDIAN`| $82,000,000 | **ELIGIBLE (Rank 3)** | Exceeds threshold ($82M > $20M). **Highlighted missing Free Cash Flow (`NOT_REPORTED`)**. |
| **Helios Clean Energy** | `DEMO-HELIOS` | $48,000,000 | **ELIGIBLE (Rank 4)** | Exceeds threshold ($48M > $20M). High CAPEX profile flagged. |
| **Vitals Biopharma Corp** | `DEMO-VITA` | $12,000,000 | **INELIGIBLE (Excluded)** | **FAILED CRITERIA**: Reported revenue $12.0M is below required threshold $20.0M. |

### Missing Data Governance Demonstration (DEMO-MERIDIAN)
- Open `/company/DEMO-MERIDIAN`.
- Inspect financial facts table:
  - `Revenue`: `$82,000,000.00` (`VALIDATED_FACT`)
  - `EBITDA`: `$9,840,000.00` (`VALIDATED_FACT`)
  - `Free Cash Flow`: **`MISSING_FACT`** (Reason: **`NOT_REPORTED`**)
- **Key Takeaway**: OpenDeal explicitly displays missing financial items rather than inserting synthetic zeros or estimating cash flows without audit disclosures.

---

## macOS Demo Recording Guide

Follow this guide to produce a clean, professional 5–7 minute walkthrough video of OpenDeal using built-in macOS tools.

> **Status Notice**: As of release candidate 0.11.0rc1, **no public video recording has been created yet**. Follow these exact instructions when recording the demonstration.

### 1. Recording Setup & Specifications
- **Resolution**: 1080p (1920 × 1080) or 16:9 1440p. Avoid non-standard aspect ratios.
- **Display Scaling**: On Retina displays, go to `System Settings > Displays` and set scaling to **"More Space"** or standard **1920×1080** for crisp text rendering.
- **Browser**: Google Chrome, Safari, or Brave in a clean guest/incognito window.
  - Zoom level: **100%** (or 110% if fonts appear small on high-DPI screens).
  - Hide bookmarks bar (`Cmd + Shift + B`).
  - Disable browser extensions and distracting notification badges.
- **Audio**: Dedicated USB microphone (e.g., Blue Yeti, Rode NT-USB, or AirPods in high-quality mode). Record in a quiet room with minimal room echo.

### 2. macOS Screen Capture Tooling
1. Launch OpenDeal locally in demo mode:
   ```bash
   opendeal demo --port 8000
   ```
2. Open `http://127.0.0.1:8000` in your browser.
3. Press **`Cmd + Shift + 5`** to open macOS Screen Recording controls.
4. Select **"Record Selected Portion"** and frame the browser window exactly (16:9 aspect ratio).
5. In **Options**:
   - **Microphone**: Select your external microphone.
   - **Show Mouse Clicks**: Check **Enabled** (helps viewers track navigation).
   - **Timer**: 5 seconds (gives you time to prepare before capture begins).
6. Click **Record**.

### 3. Step-by-Step Narration Outline (5–7 Minutes)

| Timestamp | Screen | Speaking Points | Action on Screen |
|:---|:---|:---|:---|
| **0:00 - 0:45** | Landing / Home (`/`) | Introduce OpenDeal: local-first financial intelligence engine for PE/M&A. Emphasize deterministic valuation and air-gapped security. Point out the interactive demo mode banner. | Point cursor to demo banner and headline. |
| **0:45 - 2:00** | Company & Provenance (`/company/DEMO-NEXUS`) | Explain corporate profiling, 3-year income statement, balance sheet facts, and verified SHA-256 evidence excerpts. Show how facts trace back to exact filings. | Click on fact row, highlight SHA-256 evidence hash. |
| **2:00 - 3:45** | Interactive DCF (`/valuation`) | Load `DEMO-NEXUS` preset. Explain 5-year FCF, WACC (9.25%), mid-year convention. Click "Calculate". Point out explicit DCF outputs ($881.85M EV, $36.67 share price). | Click preset button, trigger calculation, highlight result cards. |
| **3:45 - 4:45** | LBO Buyout (`/valuation`) | Switch to LBO model. Explain sources & uses balance ($1,140M), 2-tranche debt structure, and 100% priority cash sweep resulting in 3.52x MoIC / 28.6% IRR. | Scroll through debt paydown schedule table. |
| **4:45 - 5:45** | Target Screening (`/discovery`) | Open screening run `RUN-DEMO-001`. Show why `DEMO-VITA` was excluded (< $20M revenue) and show how `DEMO-MERIDIAN` handles missing FCF transparently. | Filter candidates, click into `DEMO-VITA` exclusion message. |
| **5:45 - 6:30** | System & Security (`/system`) | Highlight local-first architecture, air-gapped PEAK hash verification, and zero third-party telemetry. | Show PEAK integrity card and loopback binding. |
| **6:30 - 7:00** | Conclusion | Summarize OpenDeal’s value proposition: deterministic accuracy, no hallucinations, institutional M&A compliance. | Return to executive dashboard. |

### 4. Post-Recording Quality Checklist
- [ ] Audio levels are consistent without clipping or background fan noise.
- [ ] Video contains no private browser tabs, passwords, or desktop notifications.
- [ ] Calculations shown match the exact figures in this scenario guide.
- [ ] Export video as **MP4 / H.264** at 1080p 30fps or 60fps.
- [ ] Upload to YouTube (Unlisted), Loom, or GitHub Releases asset, then update the pending link in `README.md`.
