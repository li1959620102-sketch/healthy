from typing import Protocol, List, Dict
from langchain_core.documents import Document


class MetadataStrategy(Protocol):
    """元数据处理策略接口"""

    def process_metadata(self, chunks: List[Document], minio_metadata: Dict) -> List[Document]:
        ...