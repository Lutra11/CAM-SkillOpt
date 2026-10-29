| Task             | Target | Method   | Seed | Test hard | A/Soft/R/Skip | Steps |     Tokens | 进度                              | 状态  | 验收 |
|:-----------------|:-------|:---------|-:-----|-:----------|-:--------------|-:------|-:-----------|:--------------------------------|:----|:---|
| spreadsheetbench | qwen   | SkillOpt |   42 |    0.5321 |       1/0/7/0 |     8 | 18,192,623 | step 8/8 · selection_eval 40/40 | 完成  | OK |
| spreadsheetbench | qwen   | SkillOpt |   43 |         — |             — |     — |          — | step 1/8 · rollout 39/40        | 运行中 | 待定 |
| spreadsheetbench | qwen   | CAM-Full |   42 |    0.5321 |       1/7/0/0 |     8 | 18,174,410 | step 8/8 · selection_eval 40/40 | 完成  | OK |
| spreadsheetbench | qwen   | CAM-Full |   43 |         — |             — |     — |          — | step 1/8 · rollout 39/40        | 运行中 | 待定 |

状态：完成=有最终 summary；运行中=最近 60 分钟内有产物更新（含重试）；卡住=无汇总且超 60 分钟无更新；— = 数值缺失，详见 reports/matrix_summary.json
