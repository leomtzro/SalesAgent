---
type: source-summary
status: active
date: 2026-11-02
source: X post by @coldemailchris (Christian), bookmarked by Leo
tags: [dept-sales, dept-marketing, cold-outreach, gtm, lists, deliverability, automation]
relevance: high
---
Takeaway: a cold outreach operation is 12 systems, not one tool. Most apply to our clinic outreach in simplified form.

The 12 systems (author's list, numbers are his and unverified):
1. **Owned TAM map.** Every company in the market scraped from Google Maps, Apollo and directories into one table we store ourselves. Bought lists are the same rows competitors already have.
2. **Qualify before enrich.** An AI column summarizes each company, an ICP agent defines what the ICP is and is not, cut anything below 80% match. Raw scrapes land 50-60% fit, qualifying first claims 90-95%.
3. **Enrichment waterfall.** 3-5 email finders in a row, verify every address. One source covers 50-60%, a waterfall 85%+.
4. **Signal scoring.** Weighted score for hiring, funding, new exec, complaints, ads, tech stack. Signals say when, firmographics say who. Re-score every 90 days.
5. **ESP router.** Split contacts by mail provider (Google, Microsoft, security gateway). Gateway contacts go to phone.
6. **GTM skill.** A Claude Code skill that takes a domain and returns ICP, TAM estimate, offers to test, angles, first lists. Reads a context file built from onboarding form, call transcripts and top closed accounts.
7. **Transcript pain extractor.** Feed 20 sales call transcripts, get the 3 repeated pains in the prospect's own words plus disqualifiers.
8. **Reply router.** Webhook, intent classification (interested, more info, wrong person, cost, out of office), Slack alert with context and a named owner. Cites 1-minute response lifting conversion.
9. **Inbox rotation dashboard.** Flag domains due for replacement. Any domain under 1% reply rate gets replaced.
10. **Re-engagement pipeline.** Re-run old lists through the waterfall, drop anyone who booked, new angle. Claims 11.75% reply on 1,532 ghosted prospects.
11. **Variant testing.** 15-30 variants at once, 1,000 sends each before judging, kill losers after 2 weeks. The offer is the biggest variable.
12. **Email and dialer sync.** Same list to email and a dialer, block anyone in an active thread, call every positive reply the same day. Example: 41,150 emails booked 20 meetings, 32,577 dials booked 33.

## Clinic angle
- For Tijuana clinics the TAM map is Google Maps listings plus Doctoralia and Instagram. See [[Clinic outreach by Google Maps and WhatsApp]].
- Systems 2, 6, 7, 8 and 10 fit our setup now. Phone and WhatsApp matter more than email in this market (assumption, to check with real replies).
- System 11: the offer is the variable that moves results most. Matches [[Grand Slam offer]].
- Volume maths: [[TAM and cold email volume]].

## Related
[[Warm outbound playbook (ColdIQ)]], [[Speed to lead]]
