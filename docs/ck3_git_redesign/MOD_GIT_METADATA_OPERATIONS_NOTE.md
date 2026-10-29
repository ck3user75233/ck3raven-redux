# Mod Git metadata — operations note

## 1. Rule

For mods located under `root:user_docs/mod/`, Git metadata is stored separately from the mod directory. Repository tooling must support and preserve that arrangement. A `.git` directory or `.git` file, including a Git-directory pointer file, must not be placed inside those mods.

This is a narrowly-scoped repository-layout requirement. It does not:

- Cover every repository in the workspace.
- Authorize deletion, migration, or relocation of any existing repository data.
- Reinstate a repository-existence precheck.
- Establish a blanket metadata-denial rule.
- Justify a Git-local visibility exception, host-path inspector, or metadata-directory schema alias.

## 2. Current location facts

`~/.ck3raven/artifacts/mod_git/<Mod Name>.git/` is the current external Git metadata directory used for mods held under `root:user_docs/mod/`. Verified on-disk 2026-09-08: one such directory currently present at that location.

The location is a current implementation fact, not fixed by canonical policy as a hard-coded path. A future implementation may resolve it differently through the shared interfaces provided the rule in §1 is upheld.

## 3. Invocation form

`git` invocations against a mod whose metadata is stored separately use the standard external-Git-directory form:

```
git \
  --git-dir=<resolved metadata directory> \
  --work-tree=<resolved mod folder> \
  <subcommand> [args...]
```

Environment-variable equivalent:

```
GIT_DIR=<resolved metadata directory>
GIT_WORK_TREE=<resolved mod folder>
git <subcommand> [args...]
```

## 4. Boundary between tool and agent

The **tool** supplies the resolved metadata directory and work-tree paths after WA2 resolution. The **agent request** does not carry `--git-dir`, `--work-tree`, `-C`, `-c`, or other Git-global repository or configuration redirection. This distinction — tool-supplied resolved execution parameters versus arbitrary agent-supplied repository redirection — is the invariant to preserve.

Session/canonical addressing, WA2 visibility, and enforcement authority remain unchanged by this note. The rule in §1 is the substantive addition; every other decision continues to use the canonical mechanisms defined in the surrounding architecture.

## 5. Ordinary Git errors

If the metadata directory does not exist, or is not a Git repository, `git` reports its ordinary error. That error returns through the canonical result machinery after authorization. No repository-existence precheck is added.

## 6. Out of scope for this note

No repository detector, migration tool, setup automation, or metadata-directory configuration policy is commissioned by this note. Historical operational examples and prior governance records are held in the owner archive, not linked from here.
