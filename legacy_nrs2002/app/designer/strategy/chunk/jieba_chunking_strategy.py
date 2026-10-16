import logging
from typing import List, Dict

import jieba
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from app.exceptions.rag_exception import RAGException

logger = logging.getLogger("JiebaChunkingStrategy")
# Jieba分片策略实现
class JiebaChunkingStrategy:
    """基于Jieba中文分词的分片策略"""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 100):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # 初始化Jieba
        self._init_jieba()

    def _init_jieba(self):
        """初始化Jieba分词器"""
        # 可以在这里添加自定义词典
        # jieba.load_userdict("custom_dict.txt")
        logger.info("Jieba分词器初始化完成")

    def split_document(self, docs: List[Document], doc_path: str, minio_metadata: Dict) -> List[Document]:
        """使用Jieba进行中文文档分片"""
        if len(docs) == 0:
            raise RAGException(500, f"文档为空：{doc_path}")

        # 获取文档内容
        content = docs[0].page_content
        original_metadata = docs[0].metadata

        # 使用Jieba进行分词
        logger.info("开始使用Jieba进行中文分词")
        words = jieba.cut(content)

        # 将分词结果重新组合为文本
        segmented_text = " ".join(words)

        # 使用LangChain的分片器进行分片
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n## ", "\n### ", "\n\n", "\n", "。", "！", "？", "；", "，"],
            length_function=len
        )

        # 创建临时文档对象
        temp_doc = Document(page_content=segmented_text, metadata=original_metadata)
        chunks = splitter.split_documents([temp_doc])

        # 过滤过短的分片
        valid_chunks = [c for c in chunks if len(c.page_content.strip()) >= 50]

        if len(valid_chunks) == 0:
            raise RAGException(500, f"无有效分片（需至少50字符）：{doc_path}")

        logger.info(f"Jieba分片完成：{doc_path}（有效分片数：{len(valid_chunks)}）")
        return valid_chunks