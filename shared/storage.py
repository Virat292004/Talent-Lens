"""Private local object storage adapter used by the prototype."""
import os
from pathlib import Path

class LocalObjectStore:
    def __init__(self, root: str | None = None):
        self.root=Path(root or os.getenv("STORAGE_DIR","./data/resumes"))
        self.root.mkdir(parents=True,exist_ok=True)

    def path_for(self,key:str)->Path:
        leaf=Path(key).name
        if leaf!=key or leaf in {".",".."}: raise ValueError("Invalid object key")
        return self.root/leaf

    def put(self,key:str,data:bytes)->None:
        self.path_for(key).write_bytes(data)

    def get(self,key:str)->bytes:
        return self.path_for(key).read_bytes()

    def delete(self,key:str)->None:
        self.path_for(key).unlink(missing_ok=True)
