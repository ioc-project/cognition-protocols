# CONCORD — Configuration

CONCORD takes two user-level inputs. Everything else — agents, schema, context,
anchors — the initiator derives from the task. Receivers need no parameters.

| Input | Flag | Default |
|---|---|---|
| Satisfaction threshold | `--satisfaction-threshold` | `0.6` |
| Round budget | `-B` / `--budget` | `10` |

**Persistent — in `AGENTS.md` or `CLAUDE.md`:**

```markdown
## L9-CONCORD
- Use `--satisfaction-threshold 0.8` and `-B 6`.
```

**Per task — in the prompt:**

```
Use l9-concord-initiator with threshold 0.8 and budget 6.
```
