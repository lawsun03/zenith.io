import httpx

url = "https://discord.com/api/webhooks/1507436855535337663/keWbIaSAXksqJvxaMzGsMAOM1u3s959AE297-V3kjNVnWI7vS_B2xB1Cd66rqy-ZMDKU"
msg = (
    "**[research-loop] B56-orb-align ORBxiFVG alignment gate -- REJECTED**\n\n"
    "Phase 1 GO was valid (A+D/B+C ratio 1.48x > 1.4, 5/5 years). Phase 2 implemented "
    "TDD (7 tests, 698/698 suite green) + full 5y pipeline benchmark under B42 Phase A "
    "(deployed config, 42 passes, $568 reset/funded).\n\n"
    "Gate: suppress ORB when no prior same-direction iFVG has fired that day "
    "(groups B+C = 42.2% of ORB trades removed, PF=0.963 -> not taken).\n\n"
    "**Results (5y excl 2022, B42 Phase A + B56-aligned Phase B):**\n"
    "- B56 Phase B: 52 accounts, 51 busts, avg 18.7d/acct, $1,808/acct (vs ref 14 accts, 13 busts, 73.5d, $3,131/acct)\n"
    "- Pipeline sust: 0.82x (FAIL -- unsustainable, busts > passes)\n"
    "- Pipeline $/mo: $602 (misleading -- 51/52 accounts bust before payout)\n"
    "- Reference B42: $549/mo, sust 3.23x (unchanged)\n\n"
    "**Root cause:** ORB already fires <=1 signal/day (+ 1 reentry). Removing 42.2% of "
    "trading days leaves funded accounts with ~3 trades/month -- too sparse to compound to "
    "payout before normal MLL drawdown hits. Same failure pattern as B47 (over-filtering) "
    "and B55 Silver Bullet (volume starvation). Lesson 109 added.\n\n"
    "Feature ships default-off (orb_ifvg_alignment_required=False). "
    "7 TDD tests, 698/698 suite green. Next: B57 (new grader, Lawrence-priority)."
)
r = httpx.post(url, json={"content": msg})
print(r.status_code, r.text[:200])
