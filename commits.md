# Commit Review Log

## `52695d7` — chore: remove unused imports and fix bare f-string literals

**What it does:** Removes unused imports and fixes f-strings that have no interpolation (e.g., `f"hello"` → `"hello"`) across 169 files.

**Good:** Massive lint cleanup touching environments, tools, tests, scripts, gateway platforms, and honcho integration. All changes are purely cosmetic — no behavioral impact. Reviewed every removed import to confirm none were actually used at runtime.

**Bad:** Nothing. Clean commit.

**Action taken:** None needed.

---

## `066fb13` — feat: add thread safety to SessionDB, client cache, and delegate tool

**What it does:** Adds `threading.Lock` to `SessionDB`, a `_client_cache_lock` with double-check locking to `auxiliary_client.py`, refactors `delegate_tool.py` to build child agents in the main thread (SSL-safe), and adds `_active_children_lock` to `AIAgent`.

**Good:**
- `_client_cache_lock` uses proper double-check locking pattern — resolves clients outside the lock, then re-checks before caching.
- `_build_child_agent` split is correct: httpx/SSL client construction is not thread-safe, so building in the main thread before handing to worker threads is the right call.
- `_active_children_lock` properly protects the interrupt propagation list.

**Bad:**
- `delegate_tool.py` had defensive fallback paths (`if lock: ... else: append without lock`) that were unnecessary since `AIAgent` always has `_active_children_lock`. This created a lock-free code path that could race with `interrupt()`.
- `_pending_model_switch` field added without any lock (low-risk since the control API is new and unused, but technically a data race).

**Action taken:** Fixed in `853852a` — removed lock-free fallback paths, always use the lock directly.

---

## `7abefea` — feat: add switch_model and smart_model tools with runtime model switching

**What it does:** Adds two new tools for runtime model switching — `switch_model` (explicit provider:model) and `smart_model` (preset-based). Registers them as toolsets and adds `SWITCH_MODEL_GUIDANCE` to the prompt builder.

**Good:** Clean tool registration pattern, follows existing conventions.

**Bad:**
- `smart_model_tool.py` hardcodes model IDs in presets with no validation that they exist or are available.
- Schema doesn't enforce that `provider`/`model` are only valid with `preset='custom'`.
- Tool handlers are stub lambdas in the registry — actual dispatch is hardcoded in `run_agent.py`. This is a pattern inconsistency but matches how other "agent-internal" tools work.

**Action taken:** None — these are design decisions for the tool author, not bugs.

---

## `25e494c` — feat: add local control API for external tool integration

**What it does:** Adds a localhost-only HTTP server (`ControlAPI`) for external tools to interact with the gateway, plus a client library (`hermes_control_client.py`).

**Good:** Correctly binds to `127.0.0.1` only (no external exposure). Non-fatal startup (gateway continues if control API fails). Clean async lifecycle (start/stop wired into gateway).

**Bad:**
- `control_api.py` accesses `_running_agents` dict without synchronization while the gateway thread modifies it concurrently. Agent could be deleted between lookup and use.
- `hermes_control_client.py` doesn't handle `JSONDecodeError` if an error response contains invalid JSON.

**Action taken:** None — these are real but low-risk since the control API is new and not yet actively used. Noted for future hardening.

---

## `9309c4f` — refactor: simplify CLI and remove dead code from tools_config

**What it does:** Removes ~196 lines of unused configurator UI from `tools_config.py`, cleans up CLI imports, adds `shutil.which("git")` for git path resolution.

**Good:** Dead code removal in `tools_config.py` is correct — the removed blocks (`CONFIGURABLE_TOOLSETS`, `PLATFORMS`, `_prompt_yes_no`, `TOOL_CATEGORIES`) were not referenced anywhere.

**Bad:**
- **`_run_cleanup()` bug:** Changed `pass` to `return None` in exception handlers, causing early exit from cleanup. If terminal cleanup throws, browser and MCP cleanup are skipped entirely. This is a real bug that could leave resources leaked.
- `shutil.which("git") or "git"` pattern repeated 9 times is noise — if git isn't in PATH, the fallback to bare `"git"` still fails identically.

**Action taken:** Fixed the `_run_cleanup()` bug in `853852a` — reverted `return None` back to `pass`.

---

## `8058d57` — docs: add desloppify agent config to AGENTS.md

**What it does:** Appends desloppify agent configuration block to AGENTS.md.

**Good:** Config block is well-formed.

**Bad:** Three identical copies of the desloppify config block were appended instead of one. Parser behavior with duplicates is undefined.

**Action taken:** Fixed in `853852a` — removed 2 duplicate blocks, kept one.

---

## `33f8d77` — fix: remove thread-unsafe redirect_stdout from delegate_tool

**What it does:** Removes `contextlib.redirect_stdout`/`redirect_stderr` from `_run_single_child`, which was causing segfaults when 3 child agents started concurrently in batch delegation.

**Good:** Correct diagnosis — `redirect_stdout` mutates the global `sys.stdout`, and 3 worker threads racing on it while the spinner thread also writes to stdout caused a C-level segfault. The child already runs with `quiet_mode=True`, so the redirect was redundant.

**Bad:** Nothing.

**Action taken:** None needed.

---

## `853852a` — fix: restore cleanup flow, dedupe AGENTS.md, simplify delegate locking

**What it does:** Fixes issues found during review of the previous commits.

**Fixes:**
1. `cli.py:_run_cleanup()` — reverted `return None` back to `pass` so all cleanup blocks run
2. `AGENTS.md` — removed 2 duplicate desloppify config blocks
3. `delegate_tool.py` — removed defensive lock-free fallback paths; always use `_active_children_lock` directly

---

## `8a7ce57` — refactor: replace _pending_model_switch with generic control queue

**What it does:** Replaces the single-purpose `_pending_model_switch` field with an extensible control queue (`collections.deque` + `threading.Lock` + handler dispatch table). The agent loop drains queued commands at safe points between iterations.

**Good:**
- Clean generalization — adding new control commands is now just adding a handler to the dict, no loop changes needed.
- Thread-safe: `deque` + lock for enqueue, snapshot-and-clear for drain (no holding lock during handler execution).
- Fixes a real bug: `active_system_prompt` (local variable in the main loop) was not refreshed after compact-via-drain mutated `_cached_system_prompt` through `_compress_context`. Next API call would use the stale pre-compaction prompt.
- Correctly removes redundant `_cached_system_prompt = new_sys` from compact handler — `_compress_context` already sets it at line 3209.

**Bad:** Nothing significant. The pre-loop drain at line 4166 doesn't refresh `active_system_prompt` but doesn't need to — compact can't fire there (no messages passed), and the local is re-read from `_cached_system_prompt` before loop entry anyway.

**Action taken:** Committed as-is.

---

## `94e73d1` — feat: add generic /control endpoint with command validation

**What it does:** Adds `POST /sessions/{key}/control` for dispatching arbitrary control commands. Validates command names against `agent._control_handlers` before enqueuing — unknown commands return 400 with available commands list. Extracts `_resolve_agent` helper. Updates client with `control()` and `compact_context()`.

**Good:**
- Command validation at the API boundary prevents the "silent 200 then error in logs" problem.
- `_resolve_agent` DRYs up `_any` resolution across endpoints.
- Client's `switch_model()` stays on the dedicated `/switch-model` endpoint, preserving reason logging and model-specific validation.
- `_drain_control_queue` warning for unknown commands stays as defense-in-depth for direct `enqueue_control` callers.

**Bad:**
- The original draft rerouted the client's `switch_model()` through the generic `/control` endpoint, which would have lost the reason logging from the dedicated endpoint. Fixed before committing.
- Test file has an ugly `importlib.util.spec_from_file_location` workaround because `gateway/__init__.py` has a broken import (`SessionResetPolicy`). This is a pre-existing issue — all gateway tests are currently broken by it. Acceptable workaround.

**Action taken:** Fixed client `switch_model()` to keep using `/switch-model`. Committed.

---

## `e642132` — refactor: add external flag to control handlers, decouple API from internals

**What it does:** Changes `_control_handlers` from `{name: fn}` to `{name: {fn, external}}`. Adds `external_control_commands` property. API validates against that property instead of reading the internal handler dict directly.

**Good:**
- Clean separation: internal commands (e.g. future `_reset_state`) can be enqueued by code but aren't reachable via HTTP.
- API layer no longer coupled to `_control_handlers` structure — only sees the property.
- Adding a new command is still just one dict entry; the `external` flag is the only new thing to think about.

**Bad:** Nothing. Minimal change, solves a real extensibility concern.
