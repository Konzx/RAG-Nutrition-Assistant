"""Execute and save the analysis notebook with the current Python interpreter."""
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
runtime = root / ".notebook-runtime"
for variable, folder in {
    "JUPYTER_PATH": "jupyter",
    "JUPYTER_RUNTIME_DIR": "jupyter-runtime",
    "IPYTHONDIR": "ipython",
    "MPLCONFIGDIR": "matplotlib",
    "HF_HOME": "huggingface",
}.items():
    path = runtime / folder
    path.mkdir(parents=True, exist_ok=True)
    os.environ[variable] = str(path)
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
kernel = runtime / "jupyter" / "kernels" / "python3"
kernel.mkdir(parents=True, exist_ok=True)
(kernel / "kernel.json").write_text(json.dumps({
    "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
    "display_name": "Python 3", "language": "python",
}), encoding="utf-8")

import nbformat
from nbclient import NotebookClient

path = root / "chunk_size_analysis.ipynb"
notebook = nbformat.read(path, as_version=4)
nbformat.validator.normalize(notebook)
nbformat.validate(notebook)
print("Executing notebook against the project PDF...", flush=True)
client = NotebookClient(notebook, timeout=600, kernel_name="python3",
                        resources={"metadata": {"path": str(root)}})
try:
    client.execute()
finally:
    nbformat.write(notebook, path)
print((root / "chunk_analysis_results" / "findings.md").read_text(encoding="utf-8"))
