# Choosing your first 8-10 companies on EDGAR

**Goal:** companies where you can (a) read the credit agreement, (b) check your numbers against what the company itself reported, and (c) compute ratios from XBRL data.

> EDGAR details (URLs, rate limits, search behavior) change. Verify against SEC.gov if something doesn't match.

## What a good candidate looks like

| Criterion | Want | Avoid |
|---|---|---|
| Company type | Non-financial US filer (10-K/10-Q) | Banks, insurers, foreign private issuers (20-F) |
| Credit agreement | Filed as an exhibit (Ex. 10.x), clean HTML | PDF/image exhibits, not filed at all |
| Structure | One borrower, revolver and/or term loan | Huge syndicated LBO-style deals with many baskets |
| Covenants | Max leverage, min interest coverage, or min liquidity | Only incurrence-style or "covenant-lite" terms |
| Ground truth | 10-K/10-Q states the required level and the actual ratio | Compliance mentioned with no numbers |
| Amendments | None or 1-2 | A long amendment chain |
| XBRL | Debt, cash, operating income, D&A, interest expense all tagged | Heavily custom tags |

**Target mix across the 8-10:** ~4 leverage, ~3 interest coverage, ~2 liquidity, at least 1 with a step-down schedule, at least 1 with an amendment.

## Step 1: Find candidates with full-text search

Go to **sec.gov/edgar/search** (EDGAR full-text search). Filter form types to **10-K** and **10-Q**, and a date range of the last 1-3 years. Try quoted phrases:

- `"maximum total leverage ratio" "in compliance"`
- `"maximum consolidated leverage ratio"`
- `"consolidated total net leverage ratio" "not to exceed"`
- `"minimum interest coverage ratio" "credit agreement"`
- `"minimum liquidity" "financial covenant"`
- `"leverage ratio of" "required to maintain"`

Prefer hits where the text gives both the requirement and the actual result, for example "required to maintain X, and we were at Y." Open each promising filing and note it in the tracker.

## Step 2: Capture the ground truth from the 10-K/10-Q

In the **Debt** note and **MD&A > Liquidity and Capital Resources**, record:
- Covenant type and required level
- Actual ratio reported and as-of date
- Whether the company describes its calculation (often says "as defined in the credit agreement")

This is what you test your calculator against later.

## Step 3: Find the credit agreement exhibit

1. In the 10-K, open **Item 15 (Exhibits)**. Look for "Credit Agreement" under 10.x.
2. It is often "incorporated by reference to Exhibit 10.1 to the Form 8-K filed on [date]". Follow that filing.
3. Also look for amendments ("First Amendment to Credit Agreement"). They may change thresholds, so note them.
4. Open the exhibit. Confirm it's readable HTML with section numbers and an Article I definitions section.
5. Record the URL and accession number.

## Step 4: Check XBRL coverage

Use the helper script (needs network and your SEC User-Agent):

```bash
export SEC_USER_AGENT="Your Name your.email@example.com"
python scripts/xbrl_tag_check.py TICKER1 TICKER2 TICKER3
```

It reports which candidate us-gaap tags each company uses for cash, debt, EBITDA components, interest, and liquidity, and the latest period reported. Companies that use different tags for the same concept are normal. This is why the metric mapping is per company.

## Step 5: Score and pick

Score each candidate 0-2 per row (0 = bad, 1 = okay, 2 = great):

| Criterion | Score |
|---|---|
| Exhibit is clean HTML | |
| Simple structure | |
| 10-K gives required level | |
| 10-K gives actual ratio | |
| XBRL tags cover the inputs | |
| Few amendments | |

Take the top 8-10, then check the mix target above. Maximum score is 12.

## Step 6: Log everything

Fill `data/company_tracker.csv`, one row per company. Commit it.

## Notes
- Requests to SEC need a descriptive `User-Agent` header (name + email), and SEC asks for a modest request rate (the published limit has been 10 requests/second). The script sleeps between calls.
- The credit agreement text is a legal document. You're parsing it for a portfolio project, not giving legal advice. Say so in the README.
- If you get stuck judging whether a candidate is "simple enough", paste the filing links and I'll help you assess it.
