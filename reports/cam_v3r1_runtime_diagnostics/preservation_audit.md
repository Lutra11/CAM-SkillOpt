# CAM v3r1 preservation audit

Audit time (UTC): 2026-10-19T07:51:27.881914+00:00

This read-only audit checked the old v3 snapshot against the old freeze manifest itself. It did not validate the old freeze against current live source. No credential files or environment variables were inspected; no model calls were made.

- Old freeze entries: 1378; snapshot files: 1378; hashes matched: 1378.
- Prechange prompt copies: 21/21 match both their registered hashes and the old prompt observation byte-for-byte.
- Current live prompt differences from that observation: meta_skill.md, slow_update.md.
- cam-20 status: PAUSED.

The prompt observation records that historical prompt contents were not proven; its later timestamp does not establish which bytes earlier requests used. The comparison only establishes prechange-copy agreement and identifies the two current prompt contract additions.

Checks: all passed.
