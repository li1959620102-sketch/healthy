import logging
from datetime import datetime
from typing import List, Dict, Any

from langchain_core.documents import Document

logger = logging.getLogger("MinIOMetadataStrategy")


# 元数据处理策略实现
class MinIOMetadataStrategy:
    """MinIO文档元数据处理策略"""

    def process_metadata(self, chunks: List[Document], minio_metadata: Dict) -> List[Document]:
        """处理MinIO文档的元数据"""
        current_time = datetime.now().isoformat()
        processed_chunks = []

        for idx, chunk in enumerate(chunks, 1):
            # 基础元数据
            base_meta = {
                "file_id": minio_metadata.get("file_id", "unknown"),
                "file_name": minio_metadata.get("file_name", "unknown"),
                "minio_path": minio_metadata.get("minio_path", "unknown"),
                "chunk_id": f"{minio_metadata.get('file_id', 'unknown')}_chunk_{idx:04d}",
                "created_at": current_time,
                "chunk_length": len(chunk.page_content),
                "business_scene": "NRS2002",
                "chunk_index": idx,
                "total_chunks": len(chunks)
            }

            # 合并元数据
            combined_meta = {**chunk.metadata, **base_meta}
            final_meta = self._validate_metadata(combined_meta)
            chunk.metadata = final_meta
            processed_chunks.append(chunk)

        logger.info(f"元数据处理完成：{len(processed_chunks)}个分片")
        return processed_chunks

    def _validate_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """校验元数据"""
        valid_meta = {}
        for key, value in metadata.items():
            if not isinstance(key, str):
                continue

            if isinstance(value, (str, int, float, bool)):
                if isinstance(value, str):
                    value = value.replace("{", "").replace("}", "").strip()
                valid_meta[key] = value
            elif value is None:
                valid_meta[key] = "None"
            else:
                valid_meta[key] = str(value).replace("{", "").replace("}", "").strip()

        # 确保必需字段存在
        required_fields = ["file_id", "chunk_id", "file_name", "minio_path"]
        for field in required_fields:
            if field not in valid_meta:
                valid_meta[field] = f"missing_{field}"

        return valid_meta