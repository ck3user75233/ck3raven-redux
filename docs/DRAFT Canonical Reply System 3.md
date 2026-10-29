The following is a DRAFT reply system 3.0 concept, we will review it in due course.

# CK3Raven Canonical Reply System 3.0

## 1. Purpose

The Canonical Reply System defines how CK3Raven converts internal execution outcomes into externally visible tool replies.

Version 3.0 removes reply construction and reply-class selection from ordinary tool implementations.

The governing principle is:

> **Subsystems determine what happened. The Reply Clearinghouse determines how that outcome is represented to the caller.**

Ordinary tool code must not choose:

- `S / I / D / E`;
- canonical reply codes;
- canonical reply messages;
- ReplyBuilder constructors.

Every externally visible tool result must pass automatically through the central Reply Clearinghouse before reaching the caller.

---

# 2. Canonical Reply Classes

CK3Raven retains four canonical reply classes:

| Type | Meaning |
|---|---|
| `S` | Successful execution or successful retrieval. |
| `I` | Invalid, unavailable, malformed, unresolved or otherwise non-permitted-to-proceed input/state where enforcement has not denied the operation. |
| `D` | Policy or capability denial produced by the enforcement authority. |
| `E` | Unexpected system/execution failure. |

These meanings are architectural.

They are not selected from message wording.

In particular:

- ordinary absence/unresolvability is not automatically `D`;
- malformed input is not automatically `E`;
- an execution exception is not automatically `I`;
- only the designated enforcement authority may originate a policy denial represented as `D`.

The Clearinghouse enforces these ownership rules.

---

# 3. Architecture

The canonical flow is:

```text
MCP caller
    ↓
tool registration/invocation layer
    ↓
AUTOMATIC TOOL WRAPPER
    ↓
tool / service / authority implementation
    ↓
structured domain outcome
    ↓
REPLY CLEARINGHOUSE
    ↓
reply playbook
    ↓
canonical type + code + message + data
    ↓
Reply
    ↓
MCP caller
```

Unexpected exceptions follow the same boundary:

```text
tool implementation
    ↓
uncaught exception
    ↓
automatic wrapper
    ↓
Reply Clearinghouse exception path
    ↓
canonical E reply
```

There must be no normal path by which an MCP tool implementation can return directly to the caller without passing through the Clearinghouse.

---

# 4. Automatic Wrapping

## 4.1 Requirement

Reply-system participation must not depend on a development agent remembering to call a reply helper.

The tool registration layer must automatically wrap every exported tool.

Conceptually:

```python
def register_tool(name, implementation):
    wrapped = make_canonical_wrapper(name, implementation)
    mcp.register_tool(name, wrapped)
```

The wrapper performs approximately:

```python
def make_canonical_wrapper(operation, implementation):
    def wrapped(*args, **kwargs):
        try:
            outcome = implementation(*args, **kwargs)
        except Exception as exc:
            return reply_clearinghouse.from_exception(
                operation=operation,
                exception=exc,
            )

        return reply_clearinghouse.finalize(
            operation=operation,
            outcome=outcome,
        )

    return wrapped
```

A normal implementation therefore remains simple:

```python
def read_file(request):
    return file_service.read(request)
```

The implementation does not explicitly invoke the reply system.

Its return value is intercepted by the wrapper before anything is returned externally.

---

## 4.2 Registration is preferable to decorators

A decorator such as:

```python
@canonical_tool("ck3_file.read")
def read_file(...):
    ...
```

is acceptable internally but must not be the primary compliance mechanism.

A developer can forget a decorator.

The preferred design is:

> **Tool registration itself applies the canonical wrapper.**

This makes Reply System participation structural rather than procedural.

Every exported tool must be registered through the one canonical registration path.

---

# 5. Internal Returns Are Unchanged

The Reply Clearinghouse does not intercept every Python `return`.

Internal code remains ordinary Python.

For example:

```python
def resolve_candidate(...):
    return PathNotFound(...)
```

may be consumed by:

```python
def file_service(...):
    result = resolve_candidate(...)
    return result
```

Nothing is converted into a canonical Reply until the value attempts to cross the external tool boundary.

The invariant is:

> **Every value leaving an exported tool invocation passes through the Reply Clearinghouse exactly once.**

Internal helpers, services and authorities may freely exchange domain objects.

---

# 6. Structured Outcomes

## 6.1 Tool code describes facts, not reply classes

Subsystems should return structured outcomes describing their substantive result.

For example:

```python
@dataclass(frozen=True)
class PathResolved:
    path: Path

@dataclass(frozen=True)
class PathNotFound:
    requested_path: str

@dataclass(frozen=True)
class InvalidPath:
    requested_path: str
    reason: str
```

A resolver might return:

```python
def resolve_path(requested):
    if malformed(requested):
        return InvalidPath(
            requested_path=requested,
            reason="malformed path",
        )

    resolved = resolve(requested)

    if resolved is None:
        return PathNotFound(requested)

    return PathResolved(resolved)
```

The resolver does not know or care that:

```text
PathResolved → S
PathNotFound → I
InvalidPath → I
```

That mapping belongs to the Reply Clearinghouse.

---

## 6.2 Do not merely rename ReplyBuilder

Do not replace:

```python
rb.invalid(...)
```

with:

```python
Outcome(reply_type="I", ...)
```

That preserves the same distributed decision problem under another API.

Feature code should return domain meaning such as:

```text
PATH_NOT_FOUND
MALFORMED_PATH
POLICY_DENIED
SUBPROCESS_FAILED
TAG_CREATED
FILE_READ
```

not:

```text
SUCCESS
INVALID
DENIED
ERROR
```

unless the subsystem itself canonically owns that classification.

---

# 7. Authority Remains Distributed Correctly

The Clearinghouse centralizes **representation**, not substantive authority.

It must not independently determine:

- filesystem visibility;
- path validity or resolution;
- capability possession;
- policy admission;
- Git target effects;
- command validity;
- whether an executor operation succeeded;
- whether an input exists.

Those questions remain with their canonical subsystem owners.

For example:

```text
World Adapter determines:
PATH_NOT_FOUND

Reply Clearinghouse maps:
PATH_NOT_FOUND → I + canonical WA code
```

Similarly:

```text
Enforcement determines:
DENY

Reply Clearinghouse maps:
DENY → D + canonical EN code
```

The Clearinghouse must never inspect prose or reconstruct policy decisions itself.

---

# 8. Reply Playbook

The Clearinghouse uses one declarative playbook mapping:

```text
operation/origin + structured outcome
    ↓
canonical reply rule
```

Illustrative shape:

```python
REPLY_PLAYBOOK = {
    ("wa.resolve", PathResolved): ReplyRule(
        reply_type="S",
        code="WA-RES-S-001",
    ),

    ("wa.resolve", PathNotFound): ReplyRule(
        reply_type="I",
        code="WA-RES-I-001",
    ),

    ("wa.resolve", InvalidPath): ReplyRule(
        reply_type="I",
        code="WA-RES-I-002",
    ),

    ("en.enforce", EnforcementDenied): ReplyRule(
        reply_type="D",
        code="EN-POL-D-001",
    ),
}
```

The exact codes remain owned by the canonical reply-code registry.

The playbook does not duplicate canonical message text.

Its purpose is only to declare:

- which outcome is expected;
- which canonical type it maps to;
- which existing canonical code represents it.

---

# 9. Reply Clearinghouse Responsibilities

The Clearinghouse is the sole component authorized to construct externally visible Replies.

Its responsibilities are limited to:

1. accept a registered operation and structured outcome;
2. locate the corresponding playbook rule;
3. validate outcome/type authority;
4. validate the canonical code against the reply registry;
5. obtain/render the canonical message;
6. extract allowed structured reply data;
7. construct the final Reply;
8. optionally emit the canonical terminal-reply log event.

Conceptually:

```python
def finalize(operation, outcome):
    rule = playbook.lookup(
        operation=operation,
        outcome_type=type(outcome),
    )

    validate_authority(
        operation=operation,
        outcome=outcome,
        rule=rule,
    )

    definition = reply_registry.require(rule.code)

    if definition.reply_type != rule.reply_type:
        raise ReplySystemInvariantError(
            "playbook and reply registry disagree"
        )

    return Reply(
        reply_type=rule.reply_type,
        code=rule.code,
        message=definition.render(outcome),
        data=extract_reply_data(outcome),
    )
```

The Clearinghouse must remain small.

---

# 10. What the Clearinghouse Must Not Do

The Reply Clearinghouse must not become a general application coordinator or business-logic owner.

It must not:

- resolve paths;
- inspect filesystem contents to determine validity;
- evaluate enforcement policy;
- inspect Git state;
- infer command effects;
- retry operations;
- execute subprocesses;
- classify arbitrary exception strings;
- infer outcomes from messages;
- interpret prose produced by tools.

Its function is:

```text
structured outcome
→ validate
→ map
→ construct canonical Reply
```

Nothing more.

---

# 11. Message Text Is Never Authoritative

Canonical message text is presentation.

It must never be used as an input to reply classification.

Prohibited architecture:

```python
message = "Path cannot be found"
reply_type = classify_message(message)
```

or:

```python
if "permission" in message:
    return D
```

The correct direction is:

```text
PATH_NOT_FOUND
    ↓
playbook
    ↓
I
    ↓
canonical code
    ↓
registry-rendered message
```

Never:

```text
free-form message
    ↓
guessed S/I/D/E
```

The canonical code/type decision precedes message rendering.

---

# 12. Success Outcomes

Where an operation has one obvious successful result shape, the tool does not necessarily need a dedicated success-outcome wrapper.

The registration/playbook may define:

```text
normal result of ck3_file.read
→ ck3_file.read success rule
```

For example:

```python
def read_file(request):
    return file_service.read(request)
```

If the returned value is the registered successful domain type, the Clearinghouse maps it directly to that operation's canonical success reply.

Use explicit structured success outcomes only when the operation has multiple materially different successful outcomes that require distinct canonical codes.

Do not create unnecessary outcome classes merely to satisfy the Reply System.

---

# 13. Expected Non-Success Outcomes

Known non-success conditions should normally be represented explicitly as structured outcomes.

Examples:

```python
PathNotFound(...)
MalformedCommand(...)
TagAlreadyExists(...)
UnsupportedOperation(...)
EnforcementDenied(...)
```

These pass through the same normal `finalize()` path.

The expected distinction is:

```text
known domain outcome
    ↓
playbook mapping

unexpected exception
    ↓
exception mapping
```

Routine invalid/unavailable conditions should not need to be expressed as Python exceptions merely to reach an `I` reply.

---

# 14. Unexpected Exceptions

The automatic wrapper catches unexpected exceptions that escape the implementation boundary.

Conceptually:

```python
try:
    outcome = implementation(...)
except Exception as exc:
    return reply_clearinghouse.from_exception(
        operation=operation,
        exception=exc,
    )
```

The exception path normally maps to the appropriate canonical `E` reply for that operation/layer.

The Clearinghouse may preserve safe diagnostic information as defined by the canonical error policy.

It must not infer `D` from an exception.

An enforcement denial should have been returned explicitly by enforcement as its structured outcome.

Likewise, an expected malformed-input condition should normally have been returned as a domain outcome rather than allowed to escape as an implementation exception.

---

# 15. S / I / D / E Ownership Validation

The playbook and Clearinghouse must enforce layer ownership.

At minimum:

### `D`

`D` is reserved for an explicit denial outcome from the enforcement authority.

A World Adapter, executor, tool wrapper or arbitrary service must not originate a `D` outcome.

### `I`

`I` represents known invalid/unavailable/unresolvable conditions owned by the relevant subsystem.

It must not be used to disguise unexpected implementation failures.

### `E`

`E` represents genuine execution/system failure.

It must not be used merely because a developer did not know which canonical invalid outcome to return.

### `S`

`S` represents successful completion of the requested supported operation.

The Clearinghouse should reject an impossible playbook mapping rather than silently normalize it.

---

# 16. Canonical Reply Registry

Reply System 3.0 does not replace the canonical reply-code/message registry.

The registry remains authoritative for:

- code existence;
- code identity;
- code/type agreement;
- canonical message template;
- message rendering rules.

The playbook references canonical codes; it does not duplicate their messages.

Conceptually:

```text
DOMAIN OUTCOME REGISTRY / PLAYBOOK
    owns outcome → reply-code mapping

REPLY CODE REGISTRY
    owns code → type/message definition

CLEARINGHOUSE
    verifies both and constructs Reply
```

This separates semantic mapping from presentation.

---

# 17. Retire ReplyBuilder as a Tool-Author API

Ordinary tool and feature code must no longer call:

```python
rb.success(...)
rb.invalid(...)
rb.denied(...)
rb.error(...)
```

or directly construct:

```python
Reply(...)
ReplyBuilder(...)
```

ReplyBuilder may remain internally if the Clearinghouse uses it as an implementation primitive.

It must cease to be an ordinary development interface.

Feature developers should think in terms of:

```text
What happened?
```

not:

```text
Which reply constructor should I call?
```

---

# 18. Mechanical Enforcement

Reply System compliance must be enforced in code, not only documentation.

Add lightweight architecture checks.

## 18.1 Tool-registration check

Every exported MCP tool must be registered through the canonical registration/wrapping mechanism.

Direct raw registration must fail tests or static checks.

## 18.2 Reply-construction check

Outside the canonical reply subsystem, reject or flag direct use of:

```text
Reply(
ReplyBuilder(
rb.success(
rb.invalid(
rb.denied(
rb.error(
```

Allow explicit exceptions only in the reply-system implementation and focused tests.

## 18.3 Playbook completeness

Every structured outcome that can reach an exported tool boundary must have an explicit valid playbook mapping.

Unknown outcomes must fail closed as an implementation defect.

They must not be guessed into a generic reply.

## 18.4 Registry agreement

Tests must verify:

```text
playbook reply type
==
reply registry code type
```

for every rule.

---

# 19. Logging Integration

Reply System 3.0 and runtime logging should integrate at **one narrow boundary**, but they must remain separate systems.

The Clearinghouse already sees the completed canonical decision:

- operation;
- origin/layer;
- structured outcome;
- reply type;
- reply code;
- final reply data;
- trace/request identity where available.

It should therefore emit one canonical **terminal reply event** to the shared runtime logging facility.

Conceptually:

```python
reply = clearinghouse.finalize(...)

runtime_logging.event(
    "tool_reply",
    operation=operation,
    reply_type=reply.reply_type,
    reply_code=reply.code,
    trace_id=context.trace_id,
)

return reply
```

Logging is observational only.

---

# 20. Logging Must Not Affect Reply Semantics

The following dependency is allowed:

```text
Reply Clearinghouse
    ↓
logging
```

The reverse dependency is prohibited:

```text
logging
    ↓
Reply Clearinghouse
```

Logging must never:

- determine `S / I / D / E`;
- select a reply code;
- alter a Reply;
- prevent a valid Reply from being returned because logging failed;
- be queried to determine what reply should be sent.

If logging fails after the Clearinghouse has produced a valid Reply, reply delivery proceeds.

This preserves a clean separation:

> **Reply System = caller contract.**

> **Logging System = operational evidence.**

---

# 21. Trace and Correlation IDs

Where the tool runtime already has a request/trace identity, the automatic wrapper should make it available to both:

- the Reply Clearinghouse;
- runtime logging.

Do not create another reply-specific correlation system.

A terminal reply log event should make it possible to correlate:

```text
tool invocation
→ subsystem execution
→ enforcement decision if any
→ final reply
```

without making correlation metadata part of reply classification.

---

# 22. Example: Successful File Read

Tool:

```python
def read_file(request):
    return file_service.read(request)
```

Service returns:

```python
FileContents(
    path="root:mod/common/foo.txt",
    text="..."
)
```

Automatic flow:

```text
FileContents
    ↓
playbook:
ck3_file.read + FileContents
    ↓
S / FILE-READ-S-...
    ↓
registry message
    ↓
canonical Reply
```

No `rb.success()` appears in tool code.

---

# 23. Example: Unresolvable Path

World Adapter:

```python
return PathNotFound(
    requested_path=request.path
)
```

Flow:

```text
WA PathNotFound
    ↓
playbook
    ↓
I / WA-...-I-...
    ↓
canonical message
    ↓
Reply
```

The tool does not independently reinterpret the WA result.

The Clearinghouse does not independently inspect the path.

---

# 24. Example: Enforcement Denial

Enforcement:

```python
return EnforcementDenied(
    policy=...,
    capability=...,
)
```

Flow:

```text
EN DENY
    ↓
playbook
    ↓
D / EN-...-D-...
    ↓
canonical message
```

A non-EN component cannot generate `D` simply by returning a similarly worded message.

---

# 25. Example: Unexpected Failure

Tool implementation:

```python
def perform_operation(...):
    return executor.execute(...)
```

Executor unexpectedly raises:

```python
OSError(...)
```

The automatic wrapper catches it.

Flow:

```text
unexpected OSError
    ↓
wrapper
    ↓
clearinghouse.from_exception()
    ↓
registered E mapping
    ↓
canonical Reply
```

Feature code does not need:

```python
except Exception:
    return rb.error(...)
```

unless it is converting a known lower-level exception into a legitimate structured domain outcome.

---

# 26. Migration Strategy

Reply System 3.0 should be introduced as an architectural migration rather than a compatibility layer.

Recommended sequence:

1. identify the single MCP tool registration boundary;
2. add mandatory automatic wrapping there;
3. implement the Reply Clearinghouse;
4. define the declarative outcome playbook;
5. adapt one representative tool family end-to-end;
6. verify that its tool code no longer constructs Replies;
7. migrate remaining tool families incrementally;
8. add static/architecture checks;
9. remove ordinary access to ReplyBuilder constructors once migration is complete.

During migration, explicitly identify remaining legacy direct-reply call sites.

Do not create a permanent dual architecture where tools may freely choose either ReplyBuilder or the Clearinghouse.

The target end state is one canonical path.

---

# 27. Acceptance Criteria

Reply System 3.0 is complete when:

1. every exported MCP tool is automatically wrapped;
2. no ordinary exported tool result can bypass the Reply Clearinghouse;
3. tool/domain implementations return data or structured outcomes rather than final Replies;
4. playbook mappings own outcome → canonical code selection;
5. canonical message text still comes from the reply registry;
6. `D` can originate only from the enforcement authority;
7. expected invalid/unavailable outcomes do not become generic `E`;
8. unexpected exceptions are converted centrally;
9. direct ReplyBuilder use is mechanically prohibited outside the reply subsystem;
10. unknown outcome mappings fail closed rather than being guessed;
11. terminal reply logging occurs centrally when logging is available;
12. logging failure cannot alter the caller reply.

---

# 28. Non-Goals

Reply System 3.0 does not create:

- an AI reply classifier;
- prose-based reply inference;
- a general workflow engine;
- a new policy engine;
- a replacement for World Adapter;
- a replacement for enforcement;
- a replacement for executor error handling;
- a logging framework;
- compatibility aliases for indefinitely supporting old ReplyBuilder call patterns.

The system centralizes the **translation from authoritative internal outcome to canonical external Reply**.

It does not centralize the underlying application decisions.

---

# 29. Canonical Principle

The intended development model is:

> **Feature code says what happened.**

> **The Reply Clearinghouse says how CK3Raven reports it.**

Tool developers should not need to remember which ReplyBuilder method, S/I/D/E class, code or message to select.

That policy should be encoded once at the tool boundary and enforced mechanically thereafter.