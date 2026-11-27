"""Create relative no-console Windows launchers; never reuse host-bound .lnk files."""
from pathlib import Path
import argparse

EDITIONS = ("v0.24", "v0.24 OCR修改")

def launcher_text(edition):
    if edition not in (*EDITIONS, "v0.25", "v0.26", "v0.27", "v0.28", "v0.29"):
        raise ValueError("Unknown edition")
    return (
        'Option Explicit\r\n'
        'Dim fs, shell, base, app\r\n'
        'Set fs = CreateObject("Scripting.FileSystemObject")\r\n'
        'Set shell = CreateObject("WScript.Shell")\r\n'
        'base = fs.GetParentFolderName(WScript.ScriptFullName)\r\n'
        f'app = fs.BuildPath(base, "{edition}\\Lyria.exe")\r\n'
        'If Not fs.FileExists(app) Then\r\n'
        '  MsgBox "Lyria.exe not found. Move the entire delivery together.", 16, "Lyria"\r\n'
        '  WScript.Quit 2\r\n'
        'End If\r\n'
        'shell.Run Chr(34) & app & Chr(34), 1, False\r\n'
    )

def create(root, editions=None):
    root = Path(root)
    if editions is None:
        editions = tuple(v for v in ("v0.25", "v0.26", "v0.27", "v0.28", "v0.29") if (root / v / "Lyria.exe").is_file()) or EDITIONS
    targets = [(root / ("启动 " + edition + ".vbs"), launcher_text(edition)) for edition in editions]
    if not root.is_dir():
        raise ValueError("Delivery directory does not exist")
    for edition in editions:
        if not (root / edition / "Lyria.exe").is_file():
            raise ValueError("Each requested edition executable must already exist")
    if any(path.exists() for path, _ in targets):
        raise FileExistsError("Refusing to overwrite launchers")
    for path, text in targets:
        with path.open("x", encoding="utf-16", newline="") as handle:
            handle.write(text)
    return [path for path, _ in targets]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("delivery")
    for path in create(parser.parse_args().delivery):
        print(path)
