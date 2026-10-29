# ck3chronicle Log Split and Taxonomy

This note documents the Phase 1 parsing split for ck3chronicle.

## Phase 1 taxonomy

The core issue taxonomy is:

- `category`
- `error_type`
- `tags`

The emitted issue record also carries:

- `severity`
- `confidence`
- `engine_source`
- `primary_file` / `primary_line` when available
- `referenced_symbols`
- `referenced_objects`
- `extra_json`

The canonical flow is:

```text
raw CK3 log line
  -> timestamped log block
  -> extractor
  -> IssueDraft
  -> normalize()
  -> canonical issue row
  -> occurrence row(s)
```

## Shared block parsing

`ck3chronicle` uses one shared block splitter for timestamped CK3 log lines. The splitter does not decide category; it only preserves the raw block boundaries and source tag.

A timestamped block may be in either of these forms:

- `[HH:MM:SS][source_tag]: ...`
- `[HH:MM:SS][LEVEL][source_tag]: ...`

This means the splitter can be reused across log files, while the extractors decide how to classify the block.

## Log-type split

`cmd_parse()` currently walks every ingested file with `kind == 'log'`, so the parser is not limited to `error.log`.

The intended modular split is:

- `error.log` family: runtime errors, script errors, localization errors, database-reference warnings, and similar triage signals
- `debug.log` family: startup, mod loading, duplicate-definition warnings, descriptor / data-loading diagnostics, and other engine diagnostics
- `game.log` family: runtime warnings and startup/playback issues that appear in the game log

The important rule is that the shared splitter stays generic, while the extractor layer is log-aware.

## Why this matters

Some messages that look like “error handling” are actually debug-log diagnostics. For example, a line such as:

```text
[12:19:39][W][gamedatabase.h:321]: Overriding entry 'x' for database 'common/scripted_effects' in 'file: ...'
```

is not an `error.log` parser concern. It belongs in the log-specific extractor layer, with its own category and normalization rules.

That keeps the architecture modular:

- one shared block splitter
- per-log extractor families
- shared normalization / clustering underneath

## Practical treatment of repeated variants

When multiple messages differ only by the specific key, file, or line token, the extractor should template those volatile parts into the `message_template` and store the concrete values in occurrence metadata.

That way, the canonical row represents the class of issue, while the occurrence rows preserve the exact evidence.
