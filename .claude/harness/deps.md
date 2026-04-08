# Dependency Graph: PR 1 — Learning Engine

```
Task 1: Core Module (leaves — no dependencies between them)
  ├── correction_dict.py    (standalone, file-based storage)
  ├── diff_tracker.py       (standalone, pure computation)
  ├── session_history.py    (standalone, file-based storage)
  ├── style_rules.py        (standalone, file-based storage)
  ├── prompt_injector.py    (consumes CorrectionDictionary + StyleRuleEngine)
  └── post_processor.py     (consumes CorrectionDictionary + StyleRuleEngine)

Task 2: __init__.py (composes all Task 1 modules into LearningEngine singleton)
  └── depends on: all Task 1 modules

Task 3: Integration (consumes LearningEngine API)
  ├── subtitle_interface.py → setData() calls engine.record_edit()
  ├── optimize.py → agent_loop() calls engine.get_prompt_context()
  ├── optimize.py → _optimize_chunk() calls engine.post_process()
  └── subtitle_thread.py → run() calls engine.save_session_snapshot()
```

## Contracts

### LearningEngine public API (consumed by Task 3):
```python
class LearningEngine:
    def record_edit(self, old_text: str, new_text: str, source_video: str = "", source: str = "llm_optimize") -> None
    def get_prompt_context(self, limit: int = 50, source: Optional[str] = None) -> str
    def post_process(self, text: str) -> str
    def save_session_snapshot(self, task_id: str, video_path: str, stage: str, asr_json: dict) -> None
    def get_corrections_for_stage(self, source: str) -> dict
```

All methods are safe to call at any time — return no-ops on empty state, wrapped in try/except at call sites.
