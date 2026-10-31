# Focused Python regression repairs — unexecuted

- Watchlist fixture implements refresh_learning_model and the current cooldown
  callback; asserts refresh still occurs. Production learning behavior unchanged.
- Leaderboard/hot-token reader tests now assert the existing disabled-source policy.
  Synthetic injected collectors remain test fixtures; cached Pump web rankings
  cannot add unpinned wallets/candidates or manufacture buy/trade authorization.
  No production data-source policy or reader behavior was relaxed.
- Retained hot blocks require payload SHA-256 = raw_blocks.raw_hash = blocks.raw_hash.
  Invalid compression becomes a validation failure, including during replay audit.
  Tests corrupt each input independently and verify restoration restores replay.
- Confirmed leak paths: SQLite connection context managers do not close connections
  (fixed two fixture connections); PRAGMA/schema failures before existing finally
  blocks could leak store/provider connections; backup-target open failure could
  leak its already-open source. Connections now close on these paths without
  changing commit/rollback or synchronous/WAL requirements. No monolith warning
  traceback was supplied, so other warning origins are not claimed resolved.

No tests/builds/network operations ran. Operator regression commands:

```bash
python -m unittest discover -s tests -p 'test_meme_watchlist.py'
python -m unittest discover -s tests -p 'test_pump_leaderboard.py'
python -m unittest discover -s tests -p 'test_pump_hot_tokens.py'
python -m unittest discover -s tests -p 'test_onchain_hour.py'
python -m unittest discover -s tests -p 'test_sqlite_cleanup.py'
python -W error::ResourceWarning -m unittest discover -s tests -p 'test_selfhost_observer.py'
```
