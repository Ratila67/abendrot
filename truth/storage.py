"""Archivage optionnel des images dans un dataset Hugging Face (un seul commit par exécution)."""
import tempfile
from pathlib import Path

from .common import env


class ImageStore:
    def __init__(self):
        self.token, self.repo = env("HF_TOKEN"), env("HF_DATASET")
        self.enabled = bool(self.token and self.repo)
        self.dir = Path(tempfile.mkdtemp(prefix="abendrot-img-")) if self.enabled else None
        self.count = 0

    def add(self, path_in_repo, data):
        if not self.enabled:
            return ""
        p = self.dir / path_in_repo
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self.count += 1
        return path_in_repo

    def flush(self, message):
        if not self.enabled or not self.count:
            return
        from huggingface_hub import HfApi
        api = HfApi(token=self.token)
        api.create_repo(self.repo, repo_type="dataset", private=True, exist_ok=True)
        api.upload_folder(folder_path=str(self.dir), repo_id=self.repo, repo_type="dataset", commit_message=message)
        print(f"  {self.count} image(s) envoyée(s) sur huggingface.co/datasets/{self.repo}")
