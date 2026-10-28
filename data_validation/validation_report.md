# Validation report: **PASS**

- period: `[2020-09-01, 2026-10-01)`
- calibration: `[2020-09-01, 2020-12-01)`
- calibration_bars: `131040`
- input_hashes: 3816 entries (see the .json)

| Check | Result | Detail |
|---|---|---|
| D1 funding prints complete | PASS | 3 prints on each of 2221 days |
| D2 mark klines gaps | PASS | 1 gaps over 5 min: 2020-12-17T07:32 (24 min); longest 24 min, fails above 1440 min |
| D3 open interest daily | PASS | 0 of 2221 days without OI |
| D4 T-bill covers the period | PASS | DTB3 prints from 1954-01-04 to 2026-10-26 for [2020-09-01, 2026-10-01) |
| E1 funding total | PASS | ledger 353648.935599247 vs recomputed 353648.935599247 USD over 272 prints |
| E2 rerun identical | PASS | two runs over 131040 bars: identical bytes |
| E3 cash conservation | PASS | largest |residual| 5.37e-09 USD over 131039 bars (tolerance 10) |
