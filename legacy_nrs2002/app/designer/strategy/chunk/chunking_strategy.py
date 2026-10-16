from typing import Protocol, List, Dict
from langchain_core.documents import Document

# 策略接口定义
class ChunkingStrategy(Protocol):
    """分片策略接口"""

    def split_document(self, docs: List[Document], doc_path: str, minio_metadata: Dict) -> List[Document]:
        ...