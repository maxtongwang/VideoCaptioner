# Sprint Contract: PR 1 — Learning Engine

## Goal
A modular learning engine that captures user subtitle edits, builds a correction dictionary, and applies learned corrections in future optimization runs.

## Context
- Python 3.12 + PyQt5 project
- Module: `videocaptioner/core/learning/`
- Verify: `.venv/bin/python -c "from videocaptioner.core.learning import get_learning_engine; print('OK')"`
- Run app: `./run.sh`

## Success Criteria

1. **DONE when:** `CorrectionDictionary` persists to `LEARNING_PATH/corrections.json`, supports add/lookup/apply_corrections, is thread-safe, prunes entries >90 days or >5000 entries, creates `.bak` before overwrite, recovers from corrupted JSON.

2. **DONE when:** `DiffTracker.compute_corrections(original, edited)` extracts word-level diffs. Handles CJK character grouping and Latin word-boundary expansion. Filters whitespace-only and punctuation-only changes.

3. **DONE when:** `SessionHistory` saves timestamped snapshots to `LEARNING_PATH/sessions/`, lists sessions, loads sessions, auto-prunes >30 days or >100 sessions.

4. **DONE when:** `StyleRuleEngine` stores rules in `LEARNING_PATH/style_rules.json`, detects recurring patterns from corrections (e.g., repeated punctuation removal → auto-learn rule), returns prompt directives.

5. **DONE when:** `PromptInjector.build_context()` returns formatted `<learned_corrections>` and `<style_directives>` blocks. Returns empty string when no corrections exist.

6. **DONE when:** `PostProcessor.apply()` performs CJK exact substring matching and Latin word-boundary matching with case preservation. Applies dictionary corrections then style rules.

7. **DONE when:** `SubtitleTableModel.setData()` captures old value before overwrite and calls `get_learning_engine().record_edit()` with source attribution ("asr", "llm_optimize", "llm_split", "human"). Tags edited row as "human" source.

8. **DONE when:** `SubtitleOptimizer.agent_loop()` injects learned corrections into user prompt via `get_prompt_context(source="llm_optimize")`. `_optimize_chunk()` applies post-processing via `post_process()`.

9. **DONE when:** `SubtitleThread.run()` saves session snapshots at post_word_split, post_split, post_optimize, post_translate stages.

10. **DONE when:** All learning engine calls are wrapped in try/except — a failure in the learning module never crashes the main subtitle processing workflow.

11. **DONE when:** App launches without errors, existing subtitle editing workflow is unbroken, all imports resolve.

## Architectural Criteria

12. **Self-contained module:** All learning logic lives in `core/learning/`. No learning-specific state in UI classes beyond the hook in `setData()`.

13. **Zero-cost when empty:** When no learning data exists, all methods return no-ops (empty strings, empty lists, input unchanged). No performance impact.

14. **No new dependencies:** Uses only stdlib + existing project dependencies.
