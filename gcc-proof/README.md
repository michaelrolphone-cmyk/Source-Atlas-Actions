# Bounded GCC-native indexing proof

This isolated proof observes one exact released Runtime C unit, then one C++ unit. It does not upload an index or qualify all 68 Runtime units. It never uses the Clang-adapted compile database.

The runner verifies the original release archive and original 930-entry compiler database, exact Runtime/Product/SDK pins and source/toolchain hashes. It selects only `esp_privileged_os_cpu.c` and `serial/Files.cpp`, preserves their original cwd and arguments, redirects only `-o` to evidence, then appends observation-plugin options. No target macros, GCC flags, headers or target sources are changed. Each unit compiles uninstrumented, instrumented, and instrumented again, sequentially. Raw baseline/instrumented object hashes and their identity result are always retained. The selected-unit gate separately requires fail-closed provenance equivalence described below; raw identity is never relabeled. Source hashes and clean pinned checkouts are checked again.

The common GCC plugin uses FINISH_DECL, FINISH_TYPE, PRE_GENERICIZE and FINISH. It serializes metadata at the callback rather than retaining unrooted trees. The plugin version must exactly match its headers. Source locations, spelling locations, expression ranges and macro status are retained. Direct calls require GCC's resolved FUNCTION_DECL. Indirect calls remain unresolved. DECL_UID is artifact/process-local only. Type renderings and qualified contexts are diagnostic callback-local text, not persistent identities.

Controls test C direct/indirect calls and C++ overload/member resolution, callsite versus declaration locations, unchanged object bytes and repeatable normalized records. Exact-source gates inspect 18 C handwritten definitions, selected callsites and negative weak/address cases, Files method qualification, distinct same-line calls and unresolved callbacks. Compiler success and bounded fidelity gates are separate from complete semantic coverage.

Unsupported or unqualified: cross-TU/commit/compiler symbol identity; exact variable/member read/write/address-use locations; all redeclarations; uninstantiated templates; inactive/folded-away references; complete C++ traversal, constructors, destructors and lambdas; full type-use/inheritance; macro-reference equivalence; indirect target resolution; Clang equivalence. GCC expression locations are not assumed to be identifier tokens. The common walker inventory is diagnostic, not proof of exhaustive traversal.

The workflow retains the pinned IDF environment qualification and release input restoration, builds the plugin with the container host compiler against exact target GCC plugin headers, runs controls and selected units, then uploads evidence only. No index upload credentials are referenced. Xylem's current production schema has no GCC engine; production support needs a separately validated schema/adapter contract.

## Pinned host header correction

The first isolated run39177170204 verified released inputs but the pinned IDF container lacked host gmp.h, so neither target unit ran. The successor extracts only the official Ubuntu noble amd64 libgmp-dev 2:6.3.0+dfsg-2ubuntu6 package through signed APT metadata. It installs no package and changes no target input. Package/header hashes and APT log are retained; the extra include applies only to host plugin compilation. The failed run remains preserved.

## Provenance-aware equivalence, separately named

Run 39183383694 failed raw object identity before the C++ unit: all three released C compilations succeeded and selected C source/symbol/reference gates passed, but GCC records the plugin options (including each output filename) in DW_AT_producer. The retained raw hashes and failed historical run remain unchanged.

`object_equivalence.py` accepts only ELF32 little-endian Xtensa relocatables with one DWARF4 compilation unit. It parses abbreviation and DIE attribute forms and permits differences only in the exact ordered observation-plugin suffix on the sole root DW_AT_producer, its complete debug-string entry, and derived DW_FORM_strp offsets/layout. Every strp operand resolves to the same attribute/string after that sole producer replacement. The entire NUL-terminated string pool is compared with multiplicity, including unreferenced strings. Unknown forms/layouts fail.

All other section contents are exact, including executable/data, all relocation/symbol/string sections, abbreviation/line/location/range data. Section flags, alignment, linkage and sizes remain exact except the expected debug-string size. Nonempty physical offsets must follow the same packed aligned layout; NULL is canonical and NOBITS/empty offsets remain exact. No object is stripped or rewritten, and no target flag or source changes. Raw object identity and provenance equivalence are distinct report fields.

Negative controls reject changed lines, type sizes/references, names, unknown/duplicate/unreferenced strings, producer aliases, strp retargeting, unexpected producer options, code/data/relocation/symbol bytes, line tables, flags/alignment, NULL/NOBITS/empty offsets, unknown forms and truncation. This qualifies only narrow object equivalence and selected source-observation gates, not exhaustive semantic/debug correctness or complete reference coverage. The synthetic non-debug controls still require raw object identity.

The successor also retains the official Ubuntu archive keyring, InRelease and uncompressed noble/main amd64 Packages. gpgv checks the signature, then SHA256/size checks bind Packages to InRelease and the pinned libgmp-dev package to Packages; exact package version/architecture and extracted header hash are checked before plugin compilation. These are host-header inputs only.

## Ordered C++ COMDAT layout

Run 39184818970 verified the signed host-header chain and compiled both selected units three times. All selected source-observation gates passed, but the checker conservatively rejected the C++ object's 21 same-named .group sections. Its 273-section ordered layout differs only in .debug_info/.debug_str payloads.

The bounded successor retains each SHT_GROUP .group section by its original section index. It compares all group headers, signature symbol indices, symbol-table bytes, membership payloads, section links and relocation targets exactly. Membership/signature ambiguity, orphan group flags, invalid encodings and section reordering fail. Other duplicate section names remain unsupported. This changes only structural recognition; the sole producer/debug-string exception and separate raw-identity result are unchanged.
