# UVR model attribution and source record

Lyria credits **Ultimate Vocal Remover (UVR), its developers and model contributors** for the UVR-MDX-NET-Inst_HQ_3 weight and its published model parameters. Lyria's fixed DSP adapter is an independent implementation; no SUG, UVR application or audio-separator source was copied.

## Weight and official statement

- File: `UVR-MDX-NET-Inst_HQ_3.onnx`, 66,759,214 bytes.
- SHA-256: `317554b07fe1ea5279a77f2b1520a41ea4b93432560c4ffd08792c30fddf9adc`.
- Official release: https://github.com/TRvlvr/model_repo/releases/tag/all_public_uvr_models ; asset ID 112310332.
- Official UVR README, fixed revision `a5f88453bfb2b38b05a965bcf67727243e0cbf19`: https://github.com/Anjok07/ultimatevocalremovergui/blob/a5f88453bfb2b38b05a965bcf67727243e0cbf19/README.md#license
- Saved README SHA-256: `393f5bc0c5152607b1b21b518309a8844cc9c7c1bb6c35d17797315e04dd3834`.

The official README says:

> The **Ultimate Vocal Remover GUI** code is [MIT-licensed](https://github.com/Anjok07/ultimatevocalremovergui/blob/master/LICENSE).
>
> **Please Note:** For all third-party application developers who wish to use our models, please honor the MIT license by providing credit to UVR and its developers.

This explicit model-use statement is the basis recorded for local validation and attribution. The release repository itself has no standalone LICENSE, and the pinned README's relative LICENSE link returned 404 during research. We do not manufacture a weight license file or infer it solely from the MIT license of audio-separator. These limitations remain visible for any later redistribution review.

Fixed official metadata for this weight: instrumental primary stem, FFT 6144, frequency bins 3072, time frames 256, compensation 1.022. UVR's final-10,240,000-byte MD5 identifier is `55657dd70583b0fedfba5f67df11d711`. SHA-256, not this identifier, gates activation. Parameters are embedded; inference never fetches mutable JSON or silently falls back to another downloader/source.

## Runtime and other models

- ONNX Runtime 1.23.2: MIT. CUDA wheel from official PyPI, 244,508,327 bytes, SHA-256 `fe925a84b00e291e0ad3fac29bfd8f8e06112abc760cdc82cb711b4f3935bd95`.
- Qwen forced aligner and independent Qwen ASR model cards: Apache-2.0, independently pinned revisions and file hashes in `local_models.py`.
- Transformers Apache-2.0; Torch BSD-3-Clause; NumPy BSD plus bundled notices; librosa ISC; nagisa MIT; **soynlp 0.0.493 GPLv3**. Runtime inventories are saved with validation evidence. This is not a claim that every dependency is MIT or that a full public-distribution compliance audit is complete.
- audio-separator was researched, not installed or used. Its MIT license does not replace the official model statement above.
- NextFire karaoke Japanese model remains disabled: CC-BY-NC-SA-4.0, unimplemented adapter and unverified singing quality. Qwen is not asserted to be equivalent.

Saved pinned official model-data evidence SHA-256: `4f19319a49c9051093aaf6691ac74524b987548a3ad6cb1334bec679be49da51`. Lyria embeds only the fixed numeric parameters above; this JSON is research evidence, not a runtime network dependency.

## Dev6: second selectable separation model (2026-11-13)

The same explicit official UVR model-use statement above also covers the official
UVR-MDX-NET-Inst_HQ_1 release. Credit remains with Ultimate Vocal Remover (UVR),
its developers and model contributors. This is NOT a new claim that every dependency
is MIT, and does not complete public redistribution review.

- Official release asset: 101350754, `UVR-MDX-NET-Inst_HQ_1.onnx`, 66,759,214 bytes.
- Source: https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR-MDX-NET-Inst_HQ_1.onnx
- Locally calculated full SHA-256 of the official HTTPS download: `38a045c4ded87e3bf97b609ec5be7910e8a7cecec455f507227ab12b5e29f7f9`.
- The release metadata did not provide a digest. The hash above is the developer-verified pin, not an upstream published signature.
- Fixed official numeric metadata: https://github.com/Anjok07/ultimatevocalremovergui/blob/a5f88453bfb2b38b05a965bcf67727243e0cbf19/models/MDX_Net_Models/model_data/model_data.json
- Last-10,240,000-byte MD5 identity: `291c2049608edb52648b96e27eb80e95`; instrumental primary stem, FFT 6144, frequency bins 3072, frames 256, compensation 1.035.
- No upstream DSP implementation was copied. Lyria uses its existing independent overlap/add adapter with the verified model-specific coefficient.
- HQ3 remains the default. HQ1 is explicitly selectable; selecting it does not automatically download either model or silently fall back to HQ3. Qwen/NextFire forced alignment models are not the two separation choices.
- Both actual models ran on the existing Windows CUDA runtime against a synthetic English fixture. This proves loading/inference and sample-count contracts, not Japanese singing quality or audible superiority.
