# Lyria 0.2 phase 1: project core

This is an engineering foundation, not completion of the later three-entry or
candidate-comparison product. No new models, provider calls or dependencies.

## One owner and timeline

`ProjectDocument` owns one `Project`. Its `main_timeline` and
`Project.main_timeline` are aliases of the existing `mappings` list, never a
second editable copy. Controller replacement operations must validate with
`document.replace_project(project, touch=False)` and rebind `editor.project`.
The controller owns the single revision notification for a completed action.
The document ID survives replacement and undo; saved/exported revisions remain
separate from editing revisions. Candidate signatures still invalidate stale
read-only proposals through their existing checked path.

## Stable sentence identity

`Mapping.id` is the canonical sentence ID (`sentence_id` is a read-only alias).
Text/time/style edits, list reorder, save/reopen and undo/redo do not derive a
new identity from text, timing or row position. Explicit split/merge creates
new IDs and records original/ancestor IDs in `lineage_ids`. The existing AI
structural-acceptance path uses the same lineage contract. Repeated sentences
must get a new ID, never reuse the original. Imported text/OCR reference IDs
remain separate provenance and are not overwritten by human text edits.

## Common clock

`timebase.py` defines media-relative PTS seconds with half-open [start,end)
intervals. A missing boundary is `None`, never zero as a sentinel. The persisted
clock contract is validated; unsupported units cannot silently become seconds.
`FrameIndex` uses actual decoded timestamps and one-based display frame numbers,
not average FPS. It validates finite nonnegative stamps/duration and owns its
input list. The project clock also applies when no video has been loaded.

The outer `.lyria` envelope is format version 2 and requires the clock contract.
Old format-1 projects are not migrated, consistent with the 0.2 scope decision.
Old files/builds are preserved. A legacy file receives an unsupported-version
error rather than being silently rewritten.

## Reversible commands

Existing single edits retain the shared History. `History.transaction` yields
a detached draft, validates and commits it as one undo item. Exceptions,
invalid drafts and no-ops leave the live project and history untouched.
`Editor.set_times` applies ID-keyed timing batches atomically, validates locks
and boundaries, and sends exactly one change notification. A no-op preserves
redo. Undo/redo validates before consuming either stack and restores recorded
IDs and selection context; it does not generate IDs again.

## Verification scope

`tests/test_foundation_core.py` covers single ownership, stable identity through
edit/save/reopen, split/merge/razor ancestry, reversible AI-acceptance ancestry,
atomic batches and failure/no-op behavior, invalid restore, VFR boundaries,
clock/schema rejection, document replacement and detached snapshots.
No live external API is called. Aggregate Windows and packaged checks belong to
the coordinator's final frozen run; these tests alone do not establish native
playback/drag smoothness, complete video accuracy or user acceptance.
