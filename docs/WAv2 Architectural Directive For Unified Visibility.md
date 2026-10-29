Getting this normalization boundary locked down is exactly what an engine like ck3raven needs to maintain a stable, alias-free state across complex workspace resolutions. Merging those iterative addenda directly into the main text to form a single, authoritative instruction set is absolutely the right call. It removes the cognitive load of cross-referencing and kills the ParsedAddress contradiction entirely.

I have executed a full cleanup pass. All traces of "fuzzy" matching have been purged in favor of "ambiguous candidate match," NormalizedTarget has replaced the contradictory types at the convergence point, and the diagrams and validation batteries have been updated to reflect the final invariants.

Here is the clean, final, implementation-ready architectural directive.

# ---

**Agent Directive — Revision of WAv2 Unified Visibility Design Brief**

**Directive Type:** Architecture Correction

**Target Document:** WAv2 Expansion Design — Unified Visibility Layer

**Action Required:** Revise design brief before implementation proceeds.

This directive corrects several architectural issues in the draft design and clarifies the intended role of World Adapter v2 (WA) as the canonical world visibility resolution engine.

**Implementation work must not proceed until the design brief is updated accordingly.**

## ---

**1\. Core Architectural Clarification**

World Adapter v2 is the single visibility resolution engine for the session world model. It may accept multiple lookup forms, but it must emit **only** canonical resolved outputs.

The revised design must maintain the following core invariant:

WA may accept multiple selector forms (canonical path, mod name, cvid) but must emit only canonical root-based session addresses.

Non-canonical identifiers may be accepted as resolution inputs, but resolve() must emit only canonical root:key/... world-addressed results plus metadata. Non-canonical selector forms must never be emitted back to agents.

This rule prevents alias proliferation and context poisoning.

## **2\. Outcome Signaling & Reply System Compliance**

The canonical Reply system remains mandatory. WAv2 returns Reply objects, not custom response structures.

Visibility outcome is not returned as a separate field. The outcome is already communicated through the canonical Reply object:

| Reply | Meaning |
| :---- | :---- |
| **Success (S)** | Target exists and is visible in the current mode |
| **Informational / Invalid (I)** | Input malformed, not visible, or ambiguous |
| **Denied (D)** | Enforcement-level denial (outside WA responsibility) |
| **Error (E)** | System failure |

**Rules:**

* Visibility outcome must **not** appear as a separate reply field.  
* The reply code itself communicates visibility.  
* Identity resolution metadata must appear inside the Reply.data dictionary.  
* No changes to the canonical Reply schema are permitted.

**Format:**

Python

Reply(  
    reply\_type \= "S" | "I" | "E",  
    code \= "WA-RES-...",  
    data \= {...}  
)

## **3\. Accepted Input Forms**

resolve() may accept three categories of input.

### **3.1 Canonical Address Inputs (existing)**

These remain unchanged as valid inputs:

* root:key/path  
* mod:Mod Name/path

### **3.2 Mod Identity Inputs (new)**

Two new identity selectors are permitted as inputs:

* **Mod name**  
  * Example: resolve("Eclipsed Crown 724AD")  
  * If visible: Return canonical root-based address and metadata.  
* **cvid (content version id)**  
  * Example: resolve(133) or resolve("133")  
  * If visible: Return canonical root-based address and metadata.  
  * *Rule:* Numeric strings are interpreted as CVID selectors only if they do not match any canonical path pattern.

## **4\. Explicitly Prohibited Input Forms**

The design brief must explicitly forbid the following patterns. These create namespace confusion and agent context poisoning.

### **4.1 cvid: address namespace**

**Prohibited:**

* cvid:133  
* cvid:133/common/traits/file.txt

*Reason:* cvid is a database identity, not a path namespace. It must not become a canonical address scheme.

### **4.2 Relative paths combined with cvid**

**Prohibited:**

* resolve("cvid:133/common/file.txt")  
* resolve("133/common/file.txt")

*Reason:* This turns database identity into a pseudo-filesystem namespace. WA must avoid this.

## **5\. Canonical Emitted Results & Session Absolute Path Requirement**

Regardless of input form, successful resolution must emit the same canonical session-absolute output structure. All resolved paths emitted by WA must be canonical session-absolute paths using the root:key/ format.

**Example success result for a mod identity lookup:**

Python

Reply(  
    reply\_type="S",  
    code="WA-RES-S-001",  
    data={  
        "resolved": "root:steam/\<canonical-mod-root\>",  
        "mod\_name": "Eclipsed Crown 724AD",  
        "cvid": 133,  
        "root\_key": "steam"  
    }  
)

**Important rules:**

* NormalizedTarget.session\_abs must already contain the canonical session absolute path using the root:key/... format.  
* resolve() must emit this value directly in Reply.data\["resolved"\].  
* No later stage may convert mod: or cvid namespaces into canonical form. Normalization must occur before the visibility pipeline.  
* Optional metadata fields may include workshop\_id and load\_order. These metadata fields must remain metadata only and must not become alternative address namespaces.

## **6\. Removal of mod\_session\_abs**

The previous draft introduced:

mod\_session\_abs \= "mod:\<mod name\>"

**This must remain fully removed.**

*Reason:* It duplicates address information, introduces aliasing, and undermines canonical path discipline. The reply must contain **one** canonical resolved address. Metadata may contain identifiers such as mod\_name and cvid, but not additional session-absolute address strings.

## **7\. Clarification on ParsedAddress**

ParsedAddress may remain as an intermediate parse product in the canonical path parsing lane if convenient for implementation.

However, ParsedAddress must **not** remain the final normalized structure used by resolve(). All successful resolution paths must ultimately produce a NormalizedTarget before visibility evaluation and Reply construction occur.

## **8\. Identity Resolution Rules**

Identity lookups must follow strict rules.

### **8.1 cvid resolution**

resolve(133):

1. scan session.mods  
2. match ModEntry.cvid  
3. if found → canonical root:key/... mod address  
4. if not found → invalid (I)

Example invalid:

Python

resolve(999999)  
\# Result: Reply(reply\_type="I", code="WA-RES-I-001")

### **8.2 Mod name resolution & Candidate Match Discovery**

When a provided mod name does not match exactly, WA should search for possible matches (candidate discovery). The system must not auto-select the first match.

**Rules:**

* exact match → success  
* zero matches → invalid  
* multiple matches → invalid with candidate list

**Example input (partial):**

resolve("Eclipsed")

**Resolution result:**

Python

Reply(  
    reply\_type="I",  
    code="WA-RES-I-AMBIGUOUS",  
    data={  
        "message": "Ambiguous mod name",  
        "matches": \[  
            "Eclipsed Crown 724AD",  
            "Eclipsed Empire"  
        \]  
    }  
)

The agent can then retry with an exact name.

## **9\. Separation of Responsibilities & Source of Truth**

The design brief must explicitly state:

**WA does not resolve database identities.** Database interaction belongs to the session loading pipeline.

Identity lookup must continue to use session.mods as the single source of truth. Lookup complexity is trivial (≤200 mods).

**No caches are permitted inside WAv2:**

* no \_cvid\_map  
* no \_mod\_index  
* no lookup caches

## **10\. Internal Architecture Revision**

The draft previously modeled cvid: as an address scheme. This must change.

resolve() must first determine whether the input is a canonical path or an identity selector.

Canonical paths are parsed via:

* \_parse\_root()  
* \_parse\_mod()

Identity selectors are resolved via:

* \_resolve\_mod\_name()  
* \_resolve\_cvid()

Both paths produce a NormalizedTarget which then enters the common visibility pipeline.

### **10.5 Internal Normalization Boundary**

Python

@dataclass(frozen=True)  
class NormalizedTarget:  
    """  
    Internal-only immutable normalization object.

    Produced after canonical path parsing or identity lookup.

    Represents a canonically normalized candidate world object  
    prior to visibility evaluation and Reply construction.

    Not a Reply.  
    Not a VisibilityRef.  
    Not exposed outside WAv2.

    Its purpose is to ensure that every successful resolution path  
    converges into one canonical representation before visibility  
    evaluation and token minting occur.  
    """

    session\_abs: str  
    host\_abs: Path  
    root\_key: str  
    rel\_path: str  
    subdirectory: str | None  
    mod\_name: str | None  
    cvid: int | None  
    workshop\_id: str | None \= None  
    load\_order: int | None \= None

**Required rule:**

NormalizedTarget.session\_abs must already contain the canonical root:key/... session path that will later be emitted in Reply.data\["resolved"\]. No later stage may rewrite the path.

## **11\. Visibility Enforcement & VisibilityRef Clarification**

After canonical target resolution:

Plaintext

NormalizedTarget  
     ↓  
check\_visibility()  
     ↓  
token mint  
     ↓  
Reply

Identity resolution **must not bypass visibility gating.** All targets must pass through the same visibility matrix evaluation.

**VisibilityRef Clarification:**

The VisibilityRef token contains the host absolute path inside the token registry. It is returned so that execution tools can access the filesystem when required. Agents must **never** see host absolute paths.

Therefore:

* VisibilityRef → host path lookup  
* Reply.data.resolved → canonical session path

These are separate responsibilities.

## **12\. Logging Requirements**

Structured logging remains desirable but must follow these rules:

* logging belongs to the resolve() lifecycle  
* parser functions remain pure  
* identity lookup helpers must also remain pure  
* logs must not duplicate wrapper lifecycle logs

## **13\. Required Fix in workspace.py**

The prerequisite fix remains valid. In \_apply\_playset():

Python

cvid \= m.get("cvid")

must be included when constructing ModEntry. Without this fix: session.mods\[\].cvid will be None during early resolution.

## **14\. Visibility Matrix Validation**

The implementation must validate that existing matrix conditions correctly gate identity-based resolution. Specifically confirm:

* resolve(cvid)  
* resolve(mod\_name)

do not accidentally bypass conditions like: path\_in\_session\_mods

## **15\. Architectural Invariants (Updated)**

The revised design brief must explicitly preserve these invariants.

* **Final Architectural Rule:** Multiple selector forms may enter WAv2. All successful resolution paths must converge into a single NormalizedTarget. resolve() constructs the canonical Reply exclusively from NormalizedTarget fields.  
* **Single resolution entry point:** resolve()  
* **Canonical emission only:** WA accepts multiple selectors but emits **only** canonical root-based session addresses. WA never emits non-canonical or alias selector forms.  
* **Session state is the source of truth:** session.mods. No shadow indices or caches permitted.  
* **Visibility gating always applied:** Identity resolution does not bypass the matrix.  
* **WA does not query the database:** DB hydration remains outside WA.  
* **Parsers remain pure:** All lifecycle actions occur in resolve().

## **16\. Summary of Required Changes to the Design Brief**

The design brief must be updated to:

1. Replace mod: emission with canonical root:key/... emission.  
2. Remove mod\_session\_abs.  
3. Clarify that resolved always contains the canonical session path.  
4. Explicitly state VisibilityRef hides host absolute paths.  
5. Confirm use of the canonical Reply schema with metadata in Reply.data.  
6. Add candidate match discovery behavior for ambiguous mod names.  
7. Confirm use of session.mods with no internal caching allowed.  
8. Enforce the NormalizedTarget convergence point.  
9. Update validation tests accordingly.

**Only after these revisions are incorporated should implementation proceed.**

# ---

**WAv2 Resolution Architecture & Validation Battery**

## **WAv2 Resolution Architecture — Revised Target Model**

Plaintext

                           ┌─────────────────────────────┐  
                           │         Caller / Tool       │  
                           │  resolve(input\_value, ...)  │  
                           └──────────────┬──────────────┘  
                                          │  
                                          ▼  
                         ┌──────────────────────────────────┐  
                         │          WA v2 resolve()         │  
                         │  single public entry point       │  
                         └──────────────────────────────────┘  
                                          │  
                     ┌────────────────────┼────────────────────┐  
                     │                                         │  
                     ▼                                         ▼  
      ┌──────────────────────────────┐          ┌──────────────────────────────┐  
      │   Canonical path parse lane  │          │    Identity lookup lane      │  
      │                              │          │                              │  
      │  \_parse\_root("root:...")     │          │  \_resolve\_cvid(cvid)         │  
      │  \_parse\_mod("mod:...")       │          │  \_resolve\_mod\_name(name)     │  
      └──────────────┬───────────────┘          └──────────────┬───────────────┘  
                     │                                         │  
                     └────────────────────┬────────────────────┘  
                                          ▼  
                         ┌──────────────────────────────────┐  
                         │        NormalizedTarget          │  
                         │----------------------------------│  
                         │ session\_abs   (canonical only)   │  
                         │ host\_abs                         │  
                         │ root\_key                         │  
                         │ rel\_path                         │  
                         │ mod\_name | None                  │  
                         │ cvid     | None                  │  
                         └──────────────────┬───────────────┘  
                                            │  
                                            ▼  
                         ┌──────────────────────────────────┐  
                         │   classify subdirectory, etc.    │  
                         │   derive visibility inputs       │  
                         └──────────────────┬───────────────┘  
                                            │  
                                            ▼  
                         ┌──────────────────────────────────┐  
                         │       check\_visibility()         │  
                         │ VISIBILITY\_MATRIX driven         │  
                         │ mode \+ root\_key \+ subdirectory   │  
                         └──────────────┬───────────────────┘  
                                        │  
                        visible         │         not visible / malformed  
                         yes            │  
                                        │  
                                        ▼  
                      ┌──────────────────────────┐  
                      │   mint VisibilityRef     │  
                      │  token registry stores   │  
                      │      host absolute       │  
                      └─────────────┬────────────┘  
                                    │  
                                    ▼  
                    ┌─────────────────────────────────────┐  
                    │          Canonical Reply            │  
                    │-------------------------------------│  
                    │ S \=\> visible \+ resolved             │  
                    │ I \=\> malformed, not visible,        │  
                    │      ambiguous                      │  
                    │ E \=\> system failure                 │  
                    │                                     │  
                    │ data.resolved \= root:key/.. only    │  
                    │ data.mod\_name \= metadata            │  
                    │ data.cvid     \= metadata            │  
                    │ data.root\_key \= metadata            │  
                    │ \+ VisibilityRef                     │  
                    └─────────────────────────────────────┘

### **Diagram Notes**

The key discipline is this:

many input selectors → one canonical resolved form

**Not this:**

many input selectors → many emitted aliases

That distinction is the heart of the design.

### **Canonical Behavior Summary**

**Accepted inputs:**

* root:repo/path/to/file  
* mod:Some Mod/path/to/file  
* 133  
* "133"  
* "Eclipsed Crown 724AD"

**Prohibited inputs:**

* cvid:133  
* cvid:133/common/file.txt  
* 133/common/file.txt

**Emitted result shape on success:**

* Reply(reply\_type="S", ...)  
* data.resolved \= canonical root-based session-absolute address only (root:steam/...)  
* data.mod\_name \= metadata only  
* data.cvid \= metadata only  
* VisibilityRef \= opaque token only

*No cvid: or mod: prefixes emitted back. No mod\_session\_abs. No alternate endorsed address strings.*

## ---

**Proposed Validation Battery**

### **A. Canonical Path Resolution Tests**

**A1. root path success**

* **Input:** resolve("root:repo/pyproject.toml")  
* **Expected:** \* Reply.reply\_type \== "S"  
  * data.resolved \== "root:repo/pyproject.toml" (or canonical existing form)  
  * data.cvid is None  
  * data.mod\_name is None  
  * VisibilityRef present

**A2. mod path success**

* **Input:** resolve("mod:Eclipsed Crown 724AD/common/traits/test.txt")  
* **Expected:** \* success  
  * data.resolved \== "root:steam/\<canonical-mod-root\>/common/traits/test.txt"  
  * data.mod\_name \== "Eclipsed Crown 724AD"  
  * data.cvid \== session.get\_mod("Eclipsed Crown 724AD").cvid  
  * VisibilityRef present

**A3. malformed canonical path**

* **Input:** resolve("root:")  
* **Expected:** invalid / informational, no token minted

### **B. cvid Identity Resolution Tests**

*(Assume session contains mod name: "Eclipsed Crown 724AD", cvid: 133\)*

**B1. integer cvid success**

* **Input:** resolve(133)  
* **Expected:** \* success  
  * data.cvid \== 133  
  * data.mod\_name \== "Eclipsed Crown 724AD"  
  * data.resolved \== "root:steam/\<canonical-mod-root\>"  
  * data.resolved does not contain cvid: or mod:  
  * VisibilityRef present

**B2. nonexistent cvid**

* **Input:** resolve(999999)  
* **Expected:** invalid, no token minted

**B3. prohibited cvid namespace**

* **Input:** resolve("cvid:133")  
* **Expected:** invalid (design proves cvid: is not accepted syntax)

### **C. Mod-Name Identity Resolution Tests**

**C1. exact mod name success**

* **Input:** resolve("Eclipsed Crown 724AD")  
* **Expected:** \* success  
  * data.mod\_name \== "Eclipsed Crown 724AD"  
  * data.cvid \== 133  
  * data.resolved \== "root:steam/\<canonical-mod-root\>"  
  * no alternate emitted alias fields

**C2. ambiguous candidate match**

* **Session fixture:** mods include "Eclipsed Crown 724AD" and "Eclipsed Empire"  
* **Input:** resolve("Eclipsed")  
* **Expected:** \* Reply.reply\_type \== "I"  
  * Reply.code \== "WA-RES-I-AMBIGUOUS"  
  * data.matches \== \["Eclipsed Crown 724AD", "Eclipsed Empire"\]  
  * Must not silently choose first match.

### **D. Visibility Tests**

**D1. visible mod by cvid in permissive mode**

* **Fixture:** mode with visibility to the mod’s root/category  
* **Input:** resolve(133)  
* **Expected:** success

**D2. same cvid in restricted mode**

* **Fixture:** mode where that mod should not be visible  
* **Input:** resolve(133)  
* **Expected:** invalid (proves identity lookup does not bypass visibility matrix)

### **E. Canonical Emission Tests**

**E1. success response must not emit cvid or mod namespace**

* **Input:** resolve(133)  
* **Expected:** data.resolved begins with root:, not cvid: or mod:

**E2. Canonical emission test for identity lookup**

* **Input:** resolve("Eclipsed Crown 724AD")  
* **Expected:** \* Reply.reply\_type \== "S"  
  * Reply.data.resolved must begin with "root:"  
  * Reply.data.resolved must NOT begin with "mod:"  
  * Reply.data.resolved must NOT contain "cvid:"

### **F. Session-State Ownership Tests**

**F1. missing session cvid means unresolved**

* **Fixture:** mod exists, but session.mods\[\].cvid is None  
* **Input:** resolve(133)  
* **Expected:** invalid (proves WA depends on session state, not DB fallback)

### **G. Structural / Code-Inspection Tests**

**G1. no WA-owned caches**

* **Search/assert:** no \_cvid\_map, no \_mod\_index, no \_cvid\_to\_mod, no internal caches permitted.

**G2. WA does not query DB**

* **Search/assert:** world\_adapter\_v2.py does not import db query modules for cvid lookup. No DB access functions called from WA.

---

Let me know if there's anything else you need to adjust before handing it off\!