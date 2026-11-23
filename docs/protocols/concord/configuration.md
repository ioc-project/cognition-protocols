# CONCORD — Configuration

CONCORD takes a few user-level inputs. Everything else — agents, schema, context,
anchors — the initiator derives from the task. Receivers need no parameters.

| Input | Flag | Default |
|---|---|---|
| Satisfaction threshold | `--satisfaction-threshold` | `0.6` |
| Round budget | `--budget` | `8` |
| Timeout: how long an agent waits for a message it is owed before it asks again, in seconds | `--timeout` | `300` |
| Retries: how many times an agent asks again before it aborts the run | `--retries` | `3` |

**Persistent — in `AGENTS.md` or `CLAUDE.md`:**

```markdown
## CONCORD
- Use `--satisfaction-threshold 0.8` and `--budget 6`.
```

**Per task — in the prompt:**

```
Use concord-initiator with threshold 0.8 and budget 6.
```
