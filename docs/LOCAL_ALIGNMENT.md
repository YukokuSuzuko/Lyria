# dev5 local alignment and transcription (2026-11-12)

This is the current source-level guide for the recovered dev5 implementation (historical provenance notes: `PHASE5_LOCAL_MODELS_20261112.md` and `DEV5_TEST_SCOPE_20261112.md`). Historical dev4 results are separated at the end; they are not acceptance of rebuilt source, the final integrated application or a portable release.

## Current workflow

- **4A, supplied original lyrics:** choose project audio and complete sung original text, either main timeline text or explicitly linked original lyrics. Translations are not a substitute. The standard new-audio entry can import original TXT as `source_import` main rows without manufacturing Translation records. Missing required associations stop rather than guessing by row position.
- Every automatic alignment performs fixed UVR separation first. A file named vocals, `skip_separation`, a trusted-stem option or an old cache does not bypass separation. Input audio is read-only; derived stems have source/output hashes and exact sample counts. Project time uses `offset_seconds + stem_sample / sample_rate`.
- **4B, no original lyrics:** independent Qwen ASR produces an unreviewed draft bound to document revision, source and range. Listen, correct words/line breaks, and explicitly confirm before alignment, which performs separation again. ASR is not the aligner. Standard new candidates use `source_asr`, stable IDs and no fake translations.
- Timing output is validated before candidates are offered. Missing/invalid words and sentences remain untimed; no proportional fill, fabricated offsets or positional repeated-chorus matching. Preview and explicit apply/undo retain revision, source and lock protections. A complete editable-main-draft workflow for every untimed 4B line is still incomplete.
- Opening the editor/dialog, selecting a download source or inspecting status never installs, downloads, infers or calls an online AI service. Models and inference remain local; there is no implicit source fallback.

## Environment, model acquisition and lifecycle

Creating a new runtime requires an **already installed 64-bit Python 3.12 base interpreter**. This release does not download Python itself. Dependencies may require several GB of downloads, and the installer requires at least 12 GiB free before it starts; model cache space is separate.

Select an independent 64-bit Python 3.12 environment and a separate cache directory. Installation/repair creates a **new** environment only; it never upgrades the editor environment, system Python or an existing target. It uses official PyTorch CPU/cu128 and PyPI indexes, fixed top-level dependencies and binary wheels only. A failed install retains its incomplete marker. Dependencies do not inherit a model's license, and the resolved inventory is not a complete transitive hash lock.

Paths are user-selected. For a workspace-local example, use `runtime/python312/Scripts/python.exe` and `cache/models-v1` beneath a chosen workspace, not a developer's machine-specific validation paths. These examples do not imply that either directory already exists or is ready. The runtime probe checks the unique explicit false system-site flag, actual selected package origins, device operation and required packages before readiness.

| Model | Supported source | Pinned total |
| --- | --- | --- |
| Qwen3-ForcedAligner-0.6B-hf | Official Hugging Face, official ModelScope fixed snapshot, or offline import | 1,847,235,286 bytes |
| UVR-MDX-NET-Inst_HQ_3 | Official UVR GitHub release, or offline import | 66,759,214 bytes |
| Qwen3-ASR-0.6B-hf | Official Hugging Face, or offline import | 1,576,379,761 bytes |

The source selector affects the aligner URL only; it does not turn ModelScope into a global mirror for UVR or ASR. Every file must pass its pinned size and SHA-256 before activation. Aligner HF revision is `c07281df297b9905d24a508279258cccf987a064`; its ModelScope revision is `053506a54ad1c9deea6105050330d7c500b9f098`. Do not substitute one provider's revision into another URL. Complete manifests are in `lyricsync/local_models.py`.

Downloads are application-owned. Closing the dialog leaves the same task running; reopening observes it. Explicit pause/resume retains model/source/cache identity. Speed counts new bytes, and unknown ETA stays unknown. Incremental reads and owned-socket cancellation cover slow-drip/stalled-body tests; DNS/connect/header phases may still wait for network/OS timeouts. No millisecond cancellation guarantee applies universally.

Windows worker/install tasks start suspended, join an unnamed kill-on-close Job before any venv redirector can run, then resume and release their command gate. Cleanup targets owned tasks only. Assignment/resume failure does not run the payload; an unconfirmed cleanup remains an error, not successful readiness.

## Fresh recovery verification, with limits

These observations are separate receipts, not a final aggregate or foreground acceptance:

- Rebuilt source passed mandatory-separation to Qwen alignment/candidate checks on synthetic speech. The standard original-TXT Bridge flow completed in 17.031 s, retained all three `source_import` seeds, offered two timed rows, kept the middle row untimed, and passed explicit ready-row apply plus exact undo.
- Actual ASR output followed by explicit **test-only** confirmation and alignment completed in 15.921 s to new unbound candidates, without mutating the project or inventing the missing middle timing. This does not constitute owner confirmation or singing-quality acceptance.
- Fresh GPU/CPU separation and English/Japanese inference receipts are listed in the model checkpoint. Actual selected-environment package origins, CUDA matrix operations and pip check passed. One real CPU-model cancellation returned 0.062 s after the request; this measurement is not a general cancellation bound.
- The suspended-Job fix passed 57 current core safety tests. Independent review additionally passed 10 real benign-process scenarios, including forced 0.3 s delayed Job assignment, held descendant handles, cancellation, parent-exit pipe deadline, normal completion, assignment/resume failure and an unaffected separate sentinel. Earlier failed counterexamples remain recorded.
- On frozen integration `fdb8534`, the **production UVR downloader/task** performed real official HTTPS pause/resume from byte 13,780 with HTTP 206, downloaded all 66,759,214 bytes and verified the pinned SHA-256. Offscreen close/reopen observed the same task. This proves that tested route and lifecycle, not native visual acceptance or every CDN/network.
- On the same integration, official HF and ModelScope selection each fetched and SHA-256 verified only the aligner's pinned 9,419-byte README. Each test intentionally stopped before the next file and published no usable model. This is not a new full Qwen download or large-file resume pass. Earlier complete-model acquisition receipts remain historical evidence.

Receipts: `recovery-standard-original-import-result.json`, `recovery-real-4b-confirmed-result.json`, `recovery-final-runtime-probe.json`, `recovery-real-separation-cancel.json`, `recovery-official-uvr-task-result.json`, and `recovery-official-small-downloads.json`. They are retained in the separate validation evidence, not bundled with private paths or sample output in the source package.

## Packaging and licensing

The GUI excludes Torch/Transformers and uses the separately selected runtime. External-worker data must include `lyricsync/local_alignment_worker.py`, `lyricsync/local_separation_worker.py`, `lyricsync/local_transcription_worker.py` and `lyricsync/local_models.py`, plus the required docs/requirements from `tools/portable_runtime_manifest.json`. Build/runtime assets in `resources/` are external to tracked source and must pass the manifest audit. Validate packaged workers from a non-repository working directory; source tests do not prove packaged execution.

Qwen models declare Apache-2.0; ONNX Runtime uses MIT; soynlp 0.0.493 uses GPLv3. UVR weight-use attribution follows the official statement, not an inferred license from an unrelated library. See [UVR attribution](UVR_MODEL_ATTRIBUTION.md). Full public-redistribution compliance is a separate gate.

## Remaining acceptance gates

Final integrated aggregate results, final packaged-worker execution and native Windows/multi-DPI/foreground acceptance are not declared passed here. Representative multilingual singing, human onset/end labels, separation listening quality, Chinese/Korean song samples and long-song segmentation still require their own evidence. The aligner's approximately 80 ms timestamp step is not frame-accurate acoustic timing. No real online AI request was sent in this phase, and its remote `read_timeout` root cause remains unproved.

## Historical dev4 checkpoints (2026-11-12, not current dev5 acceptance)

The earlier dev4 source recorded a complete seven-file official ModelScope aligner download with SHA-256 checks, one fresh official CUDA environment installation, and synthetic-English-speech GPU alignment with two timed sentences and one missing sentence. Its 116-test backend result, earlier 125-test Qt-inclusive result, and active-PID-tree cancellation result apply only to those recorded revisions. They cannot validate the later reconstructed implementation or its process-ownership fix.

At that historical point, 4B, built-in separation, Japanese inference and real model-download resume had not been implemented or validated. Those statements are superseded only by the specific current recovery results above. The old installer failed a wheel download timeout, a coordinator-aborted retry and a validation-console/protocol issue before a separate clean install succeeded; failures and incomplete directories were preserved. Original receipts remain in separate phase4 validation evidence. No private project description or machine-specific paths are required to reproduce the current guide.
