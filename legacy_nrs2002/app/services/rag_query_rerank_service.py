from datetime import datetime
from typing import Dict, Optional, List, Any, Literal
import logging
import json
import re
import time
import requests
from abc import ABC, abstractmethod

# 第三方依赖
try:
    import jieba
    from rank_bm25 import BM25Okapi
except ImportError as e:
    raise ImportError(f"请安装所需依赖：pip install jieba rank-bm25")

# LangChain核心依赖
from langchain_milvus import Milvus
from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from langchain_core.documents import Document
from langchain_community.retrievers import BM25Retriever
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.retrievers import BaseRetriever

# Milvus依赖
from pymilvus import Collection, connections, utility


# 异常处理类
class RAGException(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class RAGQueryException(RAGException):
    def __init__(self, code: int, message: str):
        super().__init__(code, message)


# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - NRS2002-RAG-Query - %(levelname)s - %(message)s"
)
logger = logging.getLogger("RAGQueryService")


class RAGQueryService:
    def __init__(
            self,
            # 原有参数保持不变
            milvus_host: str = None,          # 2026-09-12：默认取 settings，不再硬编码
            milvus_port: str = None,
            milvus_token: Optional[str] = None,
            collection_name: str = "nrs2002",
            embedding_model: str = "bge-m3:latest",
            embedding_model_2: str = "nomic-embed-text:latest",  # 第二路向量模型
            llm_model: str = "qwen3:4b",
            ollama_base_url: str = "http://localhost:11434",
            dim: int = 1024,
            dim_2: int = 768,  # 第二路向量维度
            require_data: bool = True,
            bm25_k: int = 5,
            hybrid_vector_weight: float = 0.6,
            hybrid_bm25_weight: float = 0.4,
            # 新增重排参数
            rerank_model: str = "bge-reranker-v2-m3:latest",  # 使用Ollama模型名称
            rerank_top_n: int = 5,  # 重排后保留的文档数
            multi_retrieval_sources: List[str] = ["vector1", "bm25"]  # 多路召回源，默认只使用vector1和bm25
    ):
        # 原有初始化逻辑
        logger.info(f"Ollama基础URL: {ollama_base_url}")

        self.milvus_host = milvus_host
        self.milvus_port = milvus_port
        self.milvus_token = milvus_token
        self.collection_name = collection_name
        self.embedding_model = embedding_model
        self.embedding_model_2 = embedding_model_2
        self.llm_model = llm_model
        self.ollama_base_url = ollama_base_url
        self.dim = dim
        self.dim_2 = dim_2
        self.require_data = require_data

        # 检索配置参数
        self.bm25_k = bm25_k
        self.hybrid_vector_weight = hybrid_vector_weight
        self.hybrid_bm25_weight = hybrid_bm25_weight
        if round(self.hybrid_vector_weight + self.hybrid_bm25_weight, 2) != 1.0:
            raise RAGQueryException(400, "混合检索权重之和必须为1.0（如0.6+0.4）")

        # 新增重排配置
        self.rerank_model = rerank_model
        self.rerank_top_n = rerank_top_n
        self.multi_retrieval_sources = multi_retrieval_sources

        # 初始化顺序：基础组件 → 检索组件 → QA链
        self.embeddings = self._init_embeddings()
        self.embeddings_2 = self._init_embeddings(use_second_model=True)
        self.vector_store = self._init_vector_store()

        # 第二路向量存储初始化（带降级处理）
        self.vector_store_2 = self._init_vector_store(use_second_model=True)
        self.llm = self._init_llm()
        self.bm25_retriever = self._init_bm25_retriever()
        self.reranker = self._init_reranker()  # 修改为重排器初始化
        self.qa_chain = self._init_qa_chain()
        self.hybrid_qa_chain = self._init_hybrid_qa_chain()
        self.multi_retrieval_qa_chain = self._init_multi_retrieval_qa_chain()

    def _init_embeddings(self, use_second_model: bool = False) -> OllamaEmbeddings:
        """初始化嵌入模型"""
        try:
            model_name = self.embedding_model_2 if use_second_model else self.embedding_model
            embeddings = OllamaEmbeddings(
                model=model_name,
                base_url=self.ollama_base_url,
                client_kwargs={"timeout": 60.0}
            )
            logger.info(f"嵌入模型加载: {model_name}")

            # 测试嵌入模型
            test_emb = embeddings.embed_query("NRS2002营养风险筛查")
            if not (isinstance(test_emb, list) and len(test_emb) > 0 and isinstance(test_emb[0], float)):
                raise RAGQueryException(503, f"嵌入模型{model_name}返回无效向量")

            # 验证维度
            target_dim = self.dim_2 if use_second_model else self.dim
            actual_dim = len(test_emb)
            if actual_dim != target_dim:
                logger.warning(f"嵌入维度不匹配，配置维度: {target_dim}, 实际维度: {actual_dim}")

            logger.info(f"嵌入模型就绪：{model_name}（向量维度：{actual_dim}）")
            return embeddings
        except Exception as e:
            raise RAGQueryException(500, f"嵌入模型初始化失败：{str(e)}")

    def _check_collection_data(self, collection: Collection, coll_name: str, use_second_model: bool = False) -> bool:
        """改进的集合数据检查逻辑"""
        try:
            # 确保集合已加载
            if not collection.is_empty:
                logger.info(f"正在加载集合: {coll_name}")
                collection.load()
                time.sleep(1)

            # 多次尝试获取实体数量，避免延迟问题
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    entity_count = collection.num_entities
                    logger.info(f"集合 '{coll_name}' 实体数量检查 (尝试 {attempt + 1}/{max_retries}): {entity_count}")

                    if entity_count > 0:
                        logger.info(f"集合 '{coll_name}' 包含 {entity_count} 条数据，检查通过")
                        return True

                    if attempt < max_retries - 1:
                        time.sleep(2)

                except Exception as e:
                    logger.warning(f"检查集合 '{coll_name}' 数据时出错: {str(e)}")
                    if attempt < max_retries - 1:
                        time.sleep(2)

            # 对于第二路向量存储，如果数据检查失败但集合存在，尝试执行一次查询验证
            if use_second_model:
                try:
                    # 使用实际存在的字段名进行查询
                    test_result = collection.query(
                        expr="file_id != ''",  # 使用存在的字段
                        output_fields=["file_id"],  # 使用实际存在的字段
                        limit=1
                    )
                    if test_result:
                        logger.info(f"通过查询验证确认集合 '{coll_name}' 有数据")
                        return True
                except Exception as query_error:
                    logger.warning(f"集合 '{coll_name}' 查询验证失败: {str(query_error)}")

            logger.warning(f"集合 '{coll_name}' 数据检查失败，可能无数据或数据不可访问")
            return False

        except Exception as e:
            logger.error(f"集合数据检查过程出错: {str(e)}")
            return False

    def _create_dummy_vector_store(self, use_second_model: bool = False):
        """创建占位向量存储，用于降级处理"""

        class DummyVectorStore:
            def __init__(self, model_type):
                self.model_type = model_type
                self.collection_name = "dummy_collection"

            def similarity_search(self, query, k=5):
                logger.warning(f"使用占位向量存储 ({self.model_type})，返回空结果")
                return []

            def similarity_search_with_score(self, query, k=5):
                return []

            def as_retriever(self, **kwargs):
                class DummyRetriever(BaseRetriever):
                    def _get_relevant_documents(self, query):
                        return []

                return DummyRetriever()

        model_type = "second_vector" if use_second_model else "first_vector"
        return DummyVectorStore(model_type)

    def _init_vector_store(self, use_second_model: bool = False) -> Milvus:
        """修复：初始化向量存储 - 改进数据检查逻辑"""
        try:
            # 断开现有连接
            if connections.has_connection("default"):
                connections.disconnect("default")

            # 连接Milvus
            connections.connect(
                host=self.milvus_host,
                port=self.milvus_port,
                token=self.milvus_token,
                alias="default",
                pool_type="CPU"
            )

            # 集合名称区分
            coll_name = f"{self.collection_name}_v2" if use_second_model else self.collection_name

            # 检查集合存在性
            if not utility.has_collection(coll_name):
                if use_second_model:
                    logger.warning(f"第二路向量集合 '{coll_name}' 不存在，将跳过此路检索")
                    return self._create_dummy_vector_store(use_second_model)
                else:
                    raise RAGQueryException(404, f"Milvus集合'{coll_name}'不存在")

            # 加载集合
            collection = Collection(coll_name)

            # 改进的数据检查逻辑
            data_check_passed = self._check_collection_data(collection, coll_name, use_second_model)

            if not data_check_passed and use_second_model:
                logger.warning(f"集合 '{coll_name}' 数据检查未通过，将跳过此路检索")
                return self._create_dummy_vector_store(use_second_model)

            # 初始化向量存储
            embedding_func = self.embeddings_2 if use_second_model else self.embeddings
            vector_store = Milvus(
                embedding_function=embedding_func,
                connection_args={
                    "host": self.milvus_host,
                    "port": self.milvus_port,
                    "token": self.milvus_token,
                    "enable_async": False
                },
                collection_name=coll_name,
                drop_old=False,
                auto_id=True
            )

            logger.info(f"Milvus向量库就绪：{coll_name}")
            return vector_store

        except RAGQueryException:
            raise
        except Exception as e:
            if use_second_model:
                logger.warning(f"第二路向量存储初始化失败，将降级处理: {str(e)}")
                return self._create_dummy_vector_store(use_second_model)
            else:
                raise RAGQueryException(500, f"Milvus初始化失败：{str(e)}")

    def _init_llm(self) -> OllamaLLM:
        """初始化LLM"""
        try:
            llm = OllamaLLM(
                model=self.llm_model,
                base_url=self.ollama_base_url,
                temperature=0.1,
                client_kwargs={"timeout": 60.0}
            )

            # 测试LLM响应
            test_resp = llm.invoke("仅返回'pong'")
            if "pong" not in test_resp.strip().lower():
                raise RAGQueryException(503, f"LLM测试失败：返回'{test_resp[:30]}'")

            logger.info(f"LLM就绪：{self.llm_model}")
            return llm
        except Exception as e:
            error_msg = str(e)
            if "connection refused" in error_msg.lower():
                error_msg = f"Ollama服务连接失败（地址：{self.ollama_base_url}）"
            elif "model not found" in error_msg.lower():
                error_msg = f"LLM模型不存在（需执行'ollama pull {self.llm_model}'）"
            raise RAGQueryException(503, error_msg)

    def _init_bm25_retriever(self) -> BM25Retriever:
        """初始化BM25检索器"""
        try:
            # 从Milvus获取文档
            coll = Collection(self.collection_name)
            if not coll.is_empty:
                coll.load()
            total_count = coll.num_entities
            logger.info(f"Milvus集合总实体数: {total_count}")

            all_docs_data = coll.query(
                expr="",
                output_fields=["text", "file_id", "file_name", "chunk_id", "source"],
                limit=total_count + 100
            )

            # 转换为Document格式
            all_docs = []
            doc_content_set = set()
            raw_documents = []
            for doc_data in all_docs_data:
                content = doc_data.get("text", "").strip()
                if not content or content in doc_content_set:
                    continue
                doc_content_set.add(content)
                raw_documents.append(content)
                all_docs.append(Document(
                    page_content=content,
                    metadata={
                        "file_id": doc_data.get("file_id", "unknown"),
                        "file_name": doc_data.get("file_name", "unknown"),
                        "chunk_id": doc_data.get("chunk_id", "unknown"),
                        "source": doc_data.get("source", "unknown")
                    }
                ))

            # 处理空文档场景
            if not all_docs:
                logger.warning("BM25检索器初始化：创建占位检索器")
                placeholder_doc = Document(page_content="NRS2002营养风险筛查规则文档", metadata={})
                raw_documents = [placeholder_doc.page_content]

                def default_tokenizer(text: str) -> List[str]:
                    return [token.strip() for token in jieba.cut(text) if token.strip()]

                self.bm25_tokenizer = default_tokenizer

                # 构建BM25模型
                tokenized_docs = [self.bm25_tokenizer(doc) for doc in raw_documents]
                self.bm25_model = BM25Okapi(tokenized_docs)

                bm25_retriever = BM25Retriever.from_documents(
                    [placeholder_doc],
                    tokenizer=self.bm25_tokenizer
                )
                bm25_retriever.k = self.bm25_k
                return bm25_retriever

            # 中文分词器
            def jieba_tokenizer(text: str) -> List[str]:
                return [token.strip() for token in jieba.cut(text) if token.strip()]

            self.bm25_tokenizer = jieba_tokenizer

            # 构建BM25模型
            tokenized_docs = [self.bm25_tokenizer(doc) for doc in raw_documents]
            self.bm25_model = BM25Okapi(tokenized_docs)

            # 初始化BM25检索器
            bm25_retriever = BM25Retriever.from_documents(
                all_docs,
                tokenizer=self.bm25_tokenizer
            )
            bm25_retriever.k = self.bm25_k

            logger.info(f"BM25检索器初始化完成：加载{len(all_docs)}条文档")
            return bm25_retriever

        except Exception as e:
            logger.error(f"BM25检索器初始化失败：{str(e)}")

            def fallback_tokenizer(text: str) -> List[str]:
                return text.split()

            self.bm25_tokenizer = fallback_tokenizer

            placeholder_text = "NRS2002营养风险筛查 BMI 体重 疾病 年龄 评分规则"
            self.bm25_model = BM25Okapi([self.bm25_tokenizer(placeholder_text)])

            placeholder_doc = Document(page_content=placeholder_text, metadata={})
            bm25_retriever = BM25Retriever.from_documents(
                [placeholder_doc],
                tokenizer=self.bm25_tokenizer
            )
            bm25_retriever.k = self.bm25_k
            return bm25_retriever

    def _init_reranker(self):
        """初始化Ollama重排模型"""
        try:
            # 设置Ollama API端点
            self.ollama_rerank_url = f"{self.ollama_base_url}/api/generate"

            # 测试重排模型是否可用
            test_payload = {
                "model": self.rerank_model,
                "prompt": "test",
                "stream": False
            }

            try:
                response = requests.post(self.ollama_rerank_url, json=test_payload, timeout=30)
                if response.status_code == 200:
                    logger.info(f"Ollama重排模型初始化成功：{self.rerank_model}")
                    return "ollama_reranker"
                else:
                    logger.warning(f"Ollama重排模型测试失败: {response.text}")
            except Exception as e:
                logger.warning(f"Ollama重排模型连接测试失败: {str(e)}")

            # 如果重排模型不可用，返回降级方案
            logger.warning("重排模型不可用，将使用默认排序")
            return "default_reranker"

        except Exception as e:
            logger.warning(f"重排模型初始化失败，将使用默认排序: {str(e)}")
            return "default_reranker"

    def _get_nrs2002_prompt(self) -> PromptTemplate:
        """NRS2002评分提示模板"""
        return PromptTemplate(
            template="""
任务：根据NRS2002营养风险筛查规则，基于参考上下文计算患者评分，输出JSON（含评分和依据说明）。

NRS2002核心规则（必须严格遵守）：
1. 营养状态受损（0-3分）：
   - 0分：BMI≥20.5且体重无下降且无进食困难
   - 1分：BMI18.5-20.4/近3月体重降3%-5%/进食减25%-50%
   - 2分：BMI＜18.5/近3月体重降5%-10%/进食减50%-75%
   - 3分：近3月体重降＞10%/进食减＞75%/BMI＜18.5+重病
2. 疾病严重程度（0-3分）：
   - 0分：良性疾病+正常进食（如稳定期慢性病）
   - 1分：慢病急性发作+需卧床（如COPD急性加重）
   - 2分：大手术/中风/ICU+需人工营养
   - 3分：大面积烧伤/多器官衰竭+紧急营养
3. 年龄（0-1分）：≥70岁得1分，否则0分
4. 总分=三部分之和（缺失参数默认0分，总分范围0-7分）

要求：
1. 先分析患者信息匹配哪条规则，再计算各维度分数和总分；
2. 输出JSON必须包含"score"（总分）、"nutritional_impairment"（营养受损分）、"disease_severity"（疾病严重度分）、"age"（年龄分）、"basis"（评分依据，说明匹配的规则条款）；
3. 仅输出JSON，无多余文字。

用户问题（患者信息）：{question}
参考上下文（NRS2002规则片段）：{context}

输出格式示例：
{{
  "score": 2,
  "nutritional_impairment": 1,
  "disease_severity": 1,
  "age": 0,
  "basis": "1.营养受损：BMI19.2（18.5-20.4）→1分；2.疾病严重度：COPD急性加重→1分；3.年龄65岁＜70→0分；总分1+1+0=2分"
}}
""",
            input_variables=["context", "question"]
        )

    def _init_qa_chain(self) -> RetrievalQA:
        """初始化向量检索QA链"""
        nrs2002_prompt = self._get_nrs2002_prompt()
        return RetrievalQA.from_chain_type(
            llm=self.llm,
            chain_type="stuff",
            retriever=self.vector_store.as_retriever(search_kwargs={"k": 2}),
            chain_type_kwargs={"prompt": nrs2002_prompt},
            return_source_documents=True
        )

    def _init_hybrid_qa_chain(self) -> RetrievalQA:
        """初始化混合检索QA链"""
        nrs2002_prompt = self._get_nrs2002_prompt()

        class HybridRetriever(BaseRetriever):
            service: "RAGQueryService"

            def _get_relevant_documents(
                    self, query: str, *, run_manager: Optional[Any] = None
            ) -> List[Document]:
                return self.service.hybrid_search(
                    query=query,
                    k=self.service.bm25_k,
                    vector_weight=self.service.hybrid_vector_weight,
                    bm25_weight=self.service.hybrid_bm25_weight
                )

            async def _aget_relevant_documents(
                    self, query: str, *, run_manager: Optional[Any] = None
            ) -> List[Document]:
                raise NotImplementedError("异步混合检索暂未实现")

        hybrid_retriever = HybridRetriever(service=self)
        return RetrievalQA.from_chain_type(
            llm=self.llm,
            chain_type="stuff",
            retriever=hybrid_retriever,
            chain_type_kwargs={"prompt": nrs2002_prompt},
            return_source_documents=True
        )

    def _init_multi_retrieval_qa_chain(self) -> RetrievalQA:
        """初始化多路召回+重排QA链"""
        nrs2002_prompt = self._get_nrs2002_prompt()

        class MultiRetrievalRerankRetriever(BaseRetriever):
            service: "RAGQueryService"

            def _get_relevant_documents(
                    self, query: str, *, run_manager: Optional[Any] = None
            ) -> List[Document]:
                return self.service.multi_retrieval_rerank(
                    query=query,
                    k=self.service.bm25_k,
                    rerank_top_n=self.service.rerank_top_n
                )

            async def _aget_relevant_documents(
                    self, query: str, *, run_manager: Optional[Any] = None
            ) -> List[Document]:
                raise NotImplementedError("异步多路召回重排暂未实现")

        multi_retriever = MultiRetrievalRerankRetriever(service=self)
        return RetrievalQA.from_chain_type(
            llm=self.llm,
            chain_type="stuff",
            retriever=multi_retriever,
            chain_type_kwargs={"prompt": nrs2002_prompt},
            return_source_documents=True
        )

    def _ensure_collection_loaded(self, use_second_model: bool = False):
        """确保集合在搜索前已加载"""
        try:
            coll_name = f"{self.collection_name}_v2" if use_second_model else self.collection_name
            coll = Collection(coll_name)
            if not coll.is_empty:
                logger.info(f"加载集合: {coll_name}")
                coll.load()
        except Exception as e:
            logger.warning(f"检查集合加载状态失败: {str(e)}")

    def _ollama_rerank(self, query: str, documents: List[Document]) -> List[float]:
        """使用Ollama进行重排评分"""
        scores = []
        for doc in documents:
            # 构建重排提示词
            prompt = f"""
请评估以下查询与文档的相关性，返回一个0-1之间的分数（1表示完全相关，0表示完全不相关）：

查询：{query}
文档：{doc.page_content[:500]}  # 限制文档长度

请只返回一个浮点数分数，不要有其他文字：
            """.strip()

            payload = {
                "model": self.rerank_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1
                }
            }

            try:
                response = requests.post(self.ollama_rerank_url, json=payload, timeout=60)
                if response.status_code == 200:
                    result = response.json()
                    response_text = result["response"].strip()

                    # 尝试从响应中提取分数
                    try:
                        # 查找数字模式
                        import re
                        score_match = re.search(r'(\d+\.?\d*)', response_text)
                        if score_match:
                            score = float(score_match.group(1))
                            # 确保分数在0-1范围内
                            score = max(0.0, min(1.0, score))
                            scores.append(score)
                        else:
                            scores.append(0.5)  # 默认分数
                    except (ValueError, TypeError, AttributeError) as parse_error:
                        logger.debug("重排分数解析失败，使用默认分数：%s", parse_error)
                        scores.append(0.5)  # 解析失败时的默认分数
                else:
                    scores.append(0.5)  # 请求失败时的默认分数
            except Exception as e:
                logger.warning(f"重排请求失败: {str(e)}")
                scores.append(0.5)

        return scores

    def multi_retrieval_rerank(self, query: str, k: int = 5, rerank_top_n: int = 5) -> List[Document]:
        """修复：多路召回+重排实现，增加错误处理"""
        try:
            # 1. 多路召回
            all_candidates = []
            retrieved_sources = []

            # 1.1 第一路向量检索
            if "vector1" in self.multi_retrieval_sources:
                try:
                    self._ensure_collection_loaded()
                    vector1_docs = self.vector_store.similarity_search(query=query, k=k * 2)
                    all_candidates.extend(vector1_docs)
                    retrieved_sources.extend(["vector1"] * len(vector1_docs))
                    logger.info(f"向量检索1召回：{len(vector1_docs)}条文档")
                except Exception as e:
                    logger.warning(f"第一路向量检索失败: {str(e)}")

            # 1.2 第二路向量检索（带错误处理）
            if "vector2" in self.multi_retrieval_sources:
                try:
                    # 检查第二路向量存储是否可用（不是占位存储）
                    if hasattr(self.vector_store_2, 'model_type') and self.vector_store_2.model_type == "second_vector":
                        logger.info("第二路向量存储使用占位模式，跳过检索")
                    else:
                        self._ensure_collection_loaded(use_second_model=True)
                        vector2_docs = self.vector_store_2.similarity_search(query=query, k=k * 2)
                        all_candidates.extend(vector2_docs)
                        retrieved_sources.extend(["vector2"] * len(vector2_docs))
                        logger.info(f"向量检索2召回：{len(vector2_docs)}条文档")
                except Exception as e:
                    logger.warning(f"第二路向量检索失败: {str(e)}")

            # 1.3 BM25检索
            if "bm25" in self.multi_retrieval_sources:
                try:
                    bm25_docs = self.bm25_retriever.invoke(query)
                    all_candidates.extend(bm25_docs)
                    retrieved_sources.extend(["bm25"] * len(bm25_docs))
                    logger.info(f"BM25检索召回：{len(bm25_docs)}条文档")
                except Exception as e:
                    logger.warning(f"BM25检索失败: {str(e)}")

            # 如果没有召回任何文档，使用降级方案
            if not all_candidates:
                logger.warning("所有召回源均无结果，尝试使用基础检索")
                try:
                    # 降级到纯BM25检索
                    fallback_docs = self.bm25_retriever.invoke(query)
                    return fallback_docs[:rerank_top_n]
                except Exception as fallback_error:
                    logger.warning(f"BM25 降级检索失败，使用内置兜底文档：{fallback_error}")
                    # 最终降级方案
                    return [Document(page_content="NRS2002营养风险筛查基础规则", metadata={"source": "fallback"})]

            # 2. 去重处理（基于文档内容）
            unique_docs = []
            seen_content = set()
            unique_sources = []

            for doc, source in zip(all_candidates, retrieved_sources):
                content_hash = hash(doc.page_content)
                if content_hash not in seen_content:
                    seen_content.add(content_hash)
                    unique_docs.append(doc)
                    unique_sources.append(source)

            logger.info(f"多路召回去重后：{len(unique_docs)}条文档")

            # 3. 重排阶段
            if self.reranker == "ollama_reranker":
                # 使用Ollama进行重排
                try:
                    rerank_scores = self._ollama_rerank(query, unique_docs)
                    logger.info("使用Ollama重排模型进行排序")
                except Exception as e:
                    logger.warning(f"Ollama重排失败，使用默认排序: {str(e)}")
                    rerank_scores = list(range(len(unique_docs), 0, -1))
            else:
                # 默认排序（降级方案）
                rerank_scores = list(range(len(unique_docs), 0, -1))
                logger.info("使用默认排序")

            # 3.3 结合分数排序
            scored_docs = list(zip(unique_docs, rerank_scores, unique_sources))

            # 按分数降序排列
            scored_docs.sort(key=lambda x: x[1], reverse=True)

            # 3.4 截取前N条结果
            final_docs = [doc for doc, _, source in scored_docs[:rerank_top_n]]

            # 记录重排结果来源分布
            source_counts = {}
            for _, _, source in scored_docs[:rerank_top_n]:
                source_counts[source] = source_counts.get(source, 0) + 1
            logger.info(f"重排后来源分布：{source_counts}，保留{len(final_docs)}条文档")

            return final_docs

        except Exception as e:
            logger.error(f"多路召回重排失败，使用降级方案: {str(e)}")
            # 降级到混合检索或基础检索
            try:
                return self.hybrid_search(query, k=rerank_top_n)
            except Exception as fallback_error:
                logger.warning(f"混合检索降级失败，使用内置兜底文档：{fallback_error}")
                return [Document(page_content="NRS2002营养风险筛查", metadata={"source": "error_fallback"})]

    def hybrid_search(self, query: str, k: int = 5, vector_weight: Optional[float] = None,
                      bm25_weight: Optional[float] = None) -> List[Document]:
        """混合检索实现"""
        try:
            vector_weight = vector_weight or self.hybrid_vector_weight
            bm25_weight = bm25_weight or self.hybrid_bm25_weight
            if round(vector_weight + bm25_weight, 2) != 1.0:
                raise RAGQueryException(400, "混合检索权重之和必须为1.0")

            # 向量检索
            self._ensure_collection_loaded()
            vector_docs_with_score = self.vector_store.similarity_search_with_score(
                query=query,
                k=k * 2
            )

            # BM25检索
            bm25_docs = self.bm25_retriever.invoke(query)
            if not bm25_docs:
                logger.warning("混合检索：BM25无结果，退化为纯向量检索")
                return [doc for doc, _ in vector_docs_with_score[:k]]

            # 计算BM25分数
            query_tokens = self.bm25_tokenizer(query)
            doc_scores = self.bm25_model.get_scores(query_tokens)
            bm25_docs_with_score = list(zip(bm25_docs, doc_scores))

            # BM25得分归一化
            bm25_scores = [score for _, score in bm25_docs_with_score]
            max_bm25 = max(bm25_scores) if bm25_scores else 0
            min_bm25 = min(bm25_scores) if bm25_scores else 0
            normalized_bm25 = []
            for doc, score in bm25_docs_with_score:
                if max_bm25 == min_bm25:
                    norm_score = 0.5
                else:
                    norm_score = (score - min_bm25) / (max_bm25 - min_bm25)
                normalized_bm25.append((doc, norm_score))

            # 合并结果
            doc_map = {}
            for doc, vec_score in vector_docs_with_score:
                doc_key = hash(doc.page_content)
                if doc_key not in doc_map:
                    doc_map[doc_key] = {
                        "doc": doc,
                        "vec_score": vec_score,
                        "bm25_score": 0.0
                    }

            for doc, bm25_score in normalized_bm25:
                doc_key = hash(doc.page_content)
                if doc_key not in doc_map:
                    doc_map[doc_key] = {
                        "doc": doc,
                        "vec_score": 0.0,
                        "bm25_score": bm25_score
                    }
                else:
                    doc_map[doc_key]["bm25_score"] = max(
                        doc_map[doc_key]["bm25_score"],
                        bm25_score
                    )

            # 排序
            sorted_docs = sorted(
                doc_map.values(),
                key=lambda x: (x["vec_score"] * vector_weight) + (x["bm25_score"] * bm25_weight),
                reverse=True
            )[:k]

            final_docs = [item["doc"] for item in sorted_docs]
            logger.info(f"混合检索完成：返回{len(final_docs)}条结果")
            return final_docs
        except Exception as e:
            raise RAGQueryException(500, f"混合检索失败：{str(e)}")

    def query_score(self, user_question: str, file_id: Optional[str] = None, collection_name: Optional[str] = None,
                    retrieval_type: Literal["vector", "bm25", "hybrid", "multi_rerank"] = "vector") -> Dict[str, Any]:
        """NRS2002评分查询主方法"""
        if not user_question.strip():
            raise RAGQueryException(400, "患者信息不能为空")

        try:
            # 确保集合加载
            if retrieval_type in ["vector", "hybrid", "multi_rerank"]:
                self._ensure_collection_loaded()

            # 集合与过滤条件
            target_collection = collection_name if collection_name else self.collection_name
            search_kwargs = {"k": self.bm25_k if retrieval_type in ["bm25", "hybrid", "multi_rerank"] else 2}
            if file_id:
                safe_file_id = str(file_id).replace("'", "''")
                search_kwargs["expr"] = f"file_id == '{safe_file_id}'"
                logger.info(f"Milvus过滤条件expr: {search_kwargs['expr']}")

                # 验证file_id存在性 - 使用正确的字段名
                coll = Collection(target_collection)
                if not coll.is_empty:
                    coll.load()
                # 使用实际存在的字段进行查询
                res = coll.query(expr=search_kwargs["expr"], output_fields=["file_id"], limit=1)
                if len(res) == 0:
                    raise RAGQueryException(404, f"指定file_id={file_id}无匹配数据")

            # 选择QA链
            if retrieval_type == "vector":
                qa_chain = self.qa_chain
                qa_chain.retriever.search_kwargs = search_kwargs
                logger.info("使用【纯向量检索】")
            elif retrieval_type == "bm25":
                bm25_qa_chain = RetrievalQA.from_chain_type(
                    llm=self.llm,
                    chain_type="stuff",
                    retriever=self.bm25_retriever,
                    chain_type_kwargs={"prompt": self._get_nrs2002_prompt()},
                    return_source_documents=True
                )
                qa_chain = bm25_qa_chain
                logger.info("使用【纯BM25检索】")
            elif retrieval_type == "hybrid":
                qa_chain = self.hybrid_qa_chain
                logger.info("使用【混合检索】")
            elif retrieval_type == "multi_rerank":
                qa_chain = self.multi_retrieval_qa_chain
                logger.info("使用【多路召回+重排】")
            else:
                raise RAGQueryException(400, "检索类型仅支持vector/bm25/hybrid/multi_rerank")

            # 执行查询
            logger.info(f"执行查询：{user_question[:50]}...")
            result = qa_chain.invoke({"query": user_question.strip()})

            # 解析与验证结果
            json_match = re.search(r'\{[\s\S]*?\}', result["result"].strip())
            if not json_match:
                raise RAGQueryException(500, f"LLM未输出有效JSON：{result['result'][:100]}")

            try:
                score_data = json.loads(json_match.group())
            except json.JSONDecodeError as e:
                raise RAGQueryException(500, f"JSON解析失败：{str(e)}（原始输出：{json_match.group()[:50]}）")

            # 验证必需字段
            required_fields = ["score", "nutritional_impairment", "disease_severity", "age", "basis"]
            for field in required_fields:
                if field not in score_data:
                    raise RAGQueryException(500, f"评分结果缺失必需字段：{field}")
                if field != "basis" and not isinstance(score_data[field], int):
                    raise RAGQueryException(500, f"字段{field}必须为整数（当前：{score_data[field]}）")

            if not (0 <= score_data["score"] <= 7):
                raise RAGQueryException(500, f"总分必须在0-7分之间（当前：{score_data['score']}）")
            for field in ["nutritional_impairment", "disease_severity"]:
                if not (0 <= score_data[field] <= 3):
                    raise RAGQueryException(500, f"{field}必须在0-3分之间（当前：{score_data[field]}）")
            if score_data["age"] not in [0, 1]:
                raise RAGQueryException(500, f"年龄分必须为0或1（当前：{score_data['age']}）")

            # 整理源文档
            source_basis = []
            for doc in result["source_documents"]:
                source_basis.append({
                    "file_name": doc.metadata.get("file_name", "unknown"),
                    "file_id": doc.metadata.get("file_id", "unknown"),
                    "rule_fragment": doc.page_content.strip()[:200] + "..." if len(
                        doc.page_content) > 200 else doc.page_content.strip(),
                    "retrieval_type": retrieval_type
                })

            return {
                "code": 200,
                "message": "查询成功",
                "retrieval_type": retrieval_type,
                "score_result": score_data,
                "source_basis": source_basis
            }

        except RAGQueryException as e:
            logger.error(f"查询失败：{e.message}")
            raise
        except Exception as e:
            raise RAGQueryException(500, f"查询流程异常：{str(e)}")

    def nutrition_assessment_qa(self, query: str,
                                retrieval_type: Literal["vector", "bm25", "hybrid", "multi_rerank"] = "multi_rerank") -> \
            Dict[str, Any]:
        """通用营养测评问答"""
        try:
            # 确保集合加载
            if retrieval_type in ["vector", "hybrid", "multi_rerank"]:
                self._ensure_collection_loaded()

            # 执行检索
            if retrieval_type == "vector":
                retrieved_docs = self.vector_store.similarity_search(query, k=self.bm25_k)
            elif retrieval_type == "bm25":
                retrieved_docs = self.bm25_retriever.invoke(query)
            elif retrieval_type == "hybrid":
                retrieved_docs = self.hybrid_search(query, k=self.bm25_k)
            elif retrieval_type == "multi_rerank":
                retrieved_docs = self.multi_retrieval_rerank(query, k=self.bm25_k, rerank_top_n=self.rerank_top_n)
            else:
                raise RAGQueryException(400, "检索类型仅支持vector/bm25/hybrid/multi_rerank")

            if not retrieved_docs:
                return {
                    "code": 200,
                    "has_result": False,
                    "query": query,
                    "retrieval_type": retrieval_type,
                    "answer": "未找到相关营养测评资料，请补充具体关键词（如NRS2002、MUST、营养风险筛查）",
                    "retrieved_docs_count": 0
                }

            # 构建QA提示词
            prompt_template = """
你是专业的临床营养测评顾问，基于以下参考资料精准回答用户问题，严格遵守：
1. 仅使用参考资料中的信息，不编造内容；
2. 结构清晰：分点说明核心观点，重点突出营养测评的指标、适用人群、判断标准；
3. 若资料未提及用户问题的细节，需明确说明"参考资料中未提及该细节"，不误导用户。

参考资料：
{context}

用户问题：{query}

回答：
            """

            # 组装上下文
            context = "\n\n".join([
                f"【资料{idx + 1}】{doc.page_content}"
                for idx, doc in enumerate(retrieved_docs)
            ])

            # 调用LLM生成回答
            chain = (
                    {"context": RunnablePassthrough(), "query": RunnablePassthrough()}
                    | PromptTemplate.from_template(prompt_template)
                    | self.llm
                    | StrOutputParser()
            )
            answer = chain.invoke({"context": context, "query": query})

            # 整理源文档元数据
            retrieved_metadata = [
                {
                    "file_id": doc.metadata.get("file_id", "unknown"),
                    "file_name": doc.metadata.get("file_name", "unknown"),
                    "chunk_id": doc.metadata.get("chunk_id", "unknown"),
                    "source": doc.metadata.get("source", "unknown")
                }
                for doc in retrieved_docs
            ]

            return {
                "code": 200,
                "has_result": True,
                "query": query,
                "retrieval_type": retrieval_type,
                "answer": answer.strip(),
                "retrieved_docs_count": len(retrieved_docs),
                "retrieved_docs_metadata": retrieved_metadata
            }
        except RAGQueryException as e:
            logger.error(f"营养测评问答失败：{e.message}")
            raise
        except Exception as e:
            raise RAGQueryException(500, f"营养测评问答流程异常：{str(e)}")


def diagnose_collection_status(milvus_host="localhost", milvus_port="19530"):
    """诊断Milvus集合状态"""
    try:
        connections.connect("default", host=milvus_host, port=milvus_port)

        collections = utility.list_collections()
        print(f"可用集合: {collections}")

        for coll_name in ["nrs2002_collection", "nrs2002_collection_v2"]:
            if coll_name in collections:
                coll = Collection(coll_name)
                print(f"\n=== 集合 {coll_name} 诊断 ===")
                print(f"是否为空: {coll.is_empty}")
                print(f"实体数量: {coll.num_entities}")

                # 尝试查询 - 使用实际存在的字段
                try:
                    if not coll.is_empty:
                        coll.load()
                    # 使用实际存在的字段进行查询
                    result = coll.query(expr="file_id != ''", limit=1, output_fields=["file_id"])
                    print(f"查询测试: {len(result)} 条结果")
                except Exception as e:
                    print(f"查询失败: {str(e)}")
            else:
                print(f"\n集合 {coll_name} 不存在")

    except Exception as e:
        print(f"诊断失败: {str(e)}")


# 测试入口
if __name__ == "__main__":
    try:
        # 先运行诊断
        print("=== Milvus集合状态诊断 ===")
        diagnose_collection_status()
        print("\n" + "=" * 50 + "\n")

        # 初始化服务
        logger.info("初始化NRS2002 RAG查询服务（含多路召回和重排）")
        rag_service = RAGQueryService(
            milvus_host="localhost",
            milvus_port="19530",
            milvus_token=None,
            collection_name="nrs2002",
            embedding_model="bge-m3:latest",
            embedding_model_2="nomic-embed-text:latest",
            llm_model="qwen3:0.6b",
            ollama_base_url="http://127.0.0.1:11434",
            dim=1024,
            dim_2=768,
            require_data=True,
            bm25_k=5,
            hybrid_vector_weight=0.6,
            hybrid_bm25_weight=0.4,
            rerank_model="bge-reranker-v2-m3:latest",  # 使用Ollama模型名称
            rerank_top_n=5,
            multi_retrieval_sources=["vector1", "bm25"]  # 默认只启用vector1和bm25
        )
        logger.info("服务初始化成功")

        # 测试用例
        test_cases = [
            {
                "type": "query_score_vector",
                "name": "NRS2002评分（纯向量）",
                "question": "患者女性，65岁，BMI 19.2，2型糖尿病稳定期（正常进食），近3个月体重从65kg降至62kg（下降4.6%），无进食困难",
                "retrieval_type": "vector"
            },
            {
                "type": "query_score_hybrid",
                "name": "NRS2002评分（混合检索）",
                "question": "胃癌术后化疗患者，68岁，BMI 18.4，近1个月体重下降8%，进食量减少60%，无其他严重疾病",
                "retrieval_type": "hybrid"
            },
            {
                "type": "query_score_multi_rerank",
                "name": "NRS2002评分（多路召回+重排）",
                "question": "72岁男性患者，BMI 17.8，急性脑中风入院，需鼻饲营养，近2个月体重下降12%",
                "retrieval_type": "multi_rerank"
            }
        ]

        # 执行测试
        for case in test_cases:
            logger.info(f"\n{'=' * 70}")
            logger.info(f"测试用例：{case['name']}")
            logger.info(f"{'=' * 70}")

            result = rag_service.query_score(
                user_question=case["question"],
                retrieval_type=case["retrieval_type"]
            )
            print(f"【评分结果】\n{json.dumps(result['score_result'], ensure_ascii=False, indent=2)}")
            print(f"【检索方式】{result['retrieval_type']}")
            print(f"【源文档数】{len(result['source_basis'])}")

        # 测试营养问答
        logger.info(f"\n{'=' * 70}")
        logger.info("测试用例：营养测评问答")
        logger.info(f"{'=' * 70}")

        qa_result = rag_service.nutrition_assessment_qa(
            query="NRS2002评分中，BMI＜18.5属于几分？",
            retrieval_type="multi_rerank"
        )
        print(f"【问答结果】\n{qa_result['answer']}")
        print(f"【检索方式】{qa_result['retrieval_type']}")

        logger.info("\n" + "=" * 50)
        logger.info("所有测试用例执行完成")

    except RAGQueryException as e:
        logger.error(f"\n测试失败：[{e.code}] {e.message}", exc_info=True)
        exit(1)
    except Exception as e:
        logger.error(f"\n未知错误：{str(e)}", exc_info=True)
        exit(1)