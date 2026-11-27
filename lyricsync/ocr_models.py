"""Pinned, offline-only OCR model identities. No downloader is called here."""
from pathlib import Path
from .storage import file_hash

MODEL_SOURCE_REVISION = "RapidAI/RapidOCR ModelScope v3.10.0"
V6_MODELS = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_medium.onnx": "eef444829dbbe18d7fea59a3f6eb75647518d2b3a9568d27c92e42940204894b",
}


def verified_v6_models(root):
    root = Path(root)
    result = {}
    for name, expected in V6_MODELS.items():
        path = root / name
        if not path.is_file():
            raise RuntimeError("缺少增强 OCR 离线模型：" + name + "。请使用完整测试包，或在 OCR 设置中选择兼容模型（PP-OCRv4）；不会自动下载。")
        actual = file_hash(path)
        if actual != expected:
            raise RuntimeError("增强 OCR 模型校验失败：" + name + "。请重新取得完整测试包或选择兼容模型，不会静默切换。")
        result[name] = actual
    return result
