from typing import Dict, Optional, List, Any
import logging
import json
import re

from langchain_milvus import Milvus
from langchain_ollama import OllamaEmbeddings,OllamaLLM
# from langchain_community.chat_models import ChatOpenAI
from langchain_openai import OpenAIEmbeddings,ChatOpenAI
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from langchain_core.documents import Document
from pymilvus import Collection, connections, utility
from app.config import settings
from langchain_community.retrievers import BM25Retriever
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from app.exceptions.rag_exception import RAGException
from enum import Enum  # 新增：策略枚举
# 新增：LLM/Embedding 服务策略枚举（明确支持的服务类型）
class LLMStrategy(Enum):
    OLLAMA = "ollama"  # 本地 Ollama
    SILICON_FLOW = "silicon_flow"  # 线上硅基流动

# 自定义异常（仅用于查询流程错误）
class RAGQueryException(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# 初始化日志（仅保留查询相关日志）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - NRS2002-RAG-Query - %(levelname)s - %(message)s"
)
logger = logging.getLogger("RAGQueryService")


class RAGQueryService:
    def __init__(
            self,
            milvus_host: str = settings.MILVUS_HOST,  # 改用host/port方式连接
            milvus_port: str = settings.MILVUS_PORT,
            milvus_token: Optional[str] = None,  # Milvus认证令牌（如无则为None）
            collection_name: str = settings.MILVUS_DEFAULT_COLLECTION,  # Milvus集合名称
            embedding_model: str = settings.EMBEDDING_MODEL,
            # 新增策略配置
            llm_strategy: str = settings.LLM_STRATEGY,  # "ollama" 或 "siliconflow"
            embedding_strategy: str = settings.EMBEDDING_STRATEGY,  # "ollama" 或 "siliconflow"
            llm_model: str = settings.LLM_MODEL,
            ollama_base_url: str = settings.OLLAMA_BASE_URL,
            dim: int = 1024,  # 嵌入向量维度，根据模型调整（bge-m3为1024）
            require_data: bool = True  # 新增：是否要求集合必须有数据
    ):
        """初始化查询必需组件：嵌入模型、Milvus向量库、LLM、QA链"""
        # 打印Ollama基础URL用于调试
        print(f"self.ollama_base_url:{ollama_base_url}")
        logger.info(f"Ollama基础URL: {ollama_base_url}")

        self.milvus_host = milvus_host
        self.milvus_port = milvus_port
        self.milvus_token = milvus_token
        self.collection_name = collection_name
        # 策略配置
        self.llm_strategy = llm_strategy
        self.embedding_strategy = embedding_strategy
        self.embedding_model = embedding_model
        self.llm_model = llm_model
        self.ollama_base_url = ollama_base_url
        self.dim = dim  # 向量维度
        self.require_data = require_data  # 控制是否检查数据存在性
        # Milvus连接参数
        milvus_connection = {
            "host": milvus_host,
            "port": milvus_port,
            "alias": settings.MILVUS_DEFAULT_ALIAS
        }
        # 初始化核心组件（顺序不可变）
        self.silicon_flow_api_key = settings.SILICON_FLOW_API_KEY
        self.silicon_flow_base_url = settings.SILICON_FLOW_BASE_URL
        self.embeddings = self._init_embeddings_by_strategy()
        # logger.info(f"milvus_connection: {milvus_connection},collection_name:{collection_name}")
        self.vector_store = self._init_vector_store(milvus_connection,collection_name)
        self.llm = self._init_llm_by_strategy()
        self.qa_chain = self._init_qa_chain()
        # self.bm25_retriever = self._init_bm25_retriever()

    def _init_embeddings_by_strategy(self):
        """根据策略初始化嵌入模型"""
        try:
            if self.embedding_strategy == "ollama":
                embeddings = OllamaEmbeddings(
                    model=self.embedding_model,
                    base_url=self.ollama_base_url,
                    client_kwargs={"timeout": 600}
                )
                logger.info(f"使用Ollama嵌入模型: {self.embedding_model}")

            elif self.embedding_strategy == "silicon_flow":
                self.embedding_model = settings.SILICON_FLOW_EMBEDDING_MODEL
                embeddings = OpenAIEmbeddings(
                    model=settings.SILICON_FLOW_EMBEDDING_MODEL,
                    openai_api_key=self.silicon_flow_api_key,
                    openai_api_base=self.silicon_flow_base_url
                )
                logger.info(f"使用硅基流动嵌入模型: {self.embedding_model}")
            else:
                raise RAGQueryException(400, f"不支持的嵌入策略: {self.embedding_strategy}")

            # 验证嵌入输出有效性并获取实际维度
            test_emb = embeddings.embed_query("NRS2002营养风险筛查")
            if not (isinstance(test_emb, list) and len(test_emb) > 0 and isinstance(test_emb[0], float)):
                raise RAGQueryException(503, "嵌入模型返回无效向量")

            # 检查维度是否匹配配置
            actual_dim = len(test_emb)
            if actual_dim != self.dim:
                logger.warning(f"嵌入维度不匹配，配置维度: {self.dim}, 实际维度: {actual_dim}，将使用实际维度")
                self.dim = actual_dim

            logger.info(f"嵌入模型就绪：{self.embedding_model}（向量维度：{self.dim}）")
            return embeddings

        except Exception as e:
            if isinstance(e, RAGQueryException):
                raise e
            raise RAGQueryException(500, f"嵌入模型初始化失败：{str(e)}")

    # 在 RAGQueryService 初始化时添加连接测试
    def _test_milvus_connection(self):
        """测试 Milvus 连接"""
        try:
            # 先断开可能存在的旧连接
            if connections.has_connection("default"):
                connections.disconnect("default")

            # 测试连接
            connections.connect(
                alias="default",
                host=self.milvus_host,
                port=self.milvus_port,
                token=self.milvus_token
            )

            # 检查连接状态
            if connections.has_connection("default"):
                logger.info("✅ Milvus 连接测试成功")
                return True
            else:
                logger.error("❌ Milvus 连接测试失败")
                return False

        except Exception as e:
            logger.error(f"❌ Milvus 连接异常: {str(e)}")
            return False

    def _init_vector_store(self, connection_args: Dict, collection_name: str) -> Milvus:
        """初始化Milvus向量存储（彻底修复连接不存在问题）"""
        try:
            # 1. 补充关键连接参数（协议和异步配置）
            connection_args = {
                **connection_args,
                "protocol": "grpc",  # 显式指定协议（与19530端口匹配）
                "enable_async": False,  # 禁用异步，避免事件循环冲突
                "token": self.milvus_token if self.milvus_token else None
            }
            alias = connection_args["alias"]
            logger.info(f"开始初始化Milvus连接：{alias}（{connection_args['host']}:{connection_args['port']}）")

            # 2. 清理旧连接（避免冲突）
            if connections.has_connection(alias):
                connections.disconnect(alias)
                logger.info(f"已断开旧连接：{alias}")

            # 3. 建立新连接（显式指定所有参数）
            connections.connect(
                alias=alias,
                host=connection_args["host"],
                port=connection_args["port"],
                token=connection_args["token"],
                db_name=settings.MILVUS_DEFAULT_DB,
                protocol=connection_args["protocol"],
                enable_async=connection_args["enable_async"]
            )

            # 4. 强制验证连接是否存在（核心检查）
            if not connections.has_connection(alias):
                raise RAGException(500, f"连接创建失败：{alias}（请检查网络和参数）")
            logger.info(f"✅ 连接创建成功：{alias}")

            # 5. 检查集合是否存在（必须指定连接别名）
            if utility.has_collection(collection_name, using=alias):
                logger.info(f"集合 '{collection_name}' 已存在（连接：{alias}）")
            else:
                logger.info(f"集合 '{collection_name}' 不存在，将自动创建（连接：{alias}）")

            # 6. 创建向量存储（传递完整连接参数）
            vector_store = Milvus(
                embedding_function=self.embeddings,
                collection_name=collection_name,
                connection_args=connection_args,  # 传递完整参数确保一致性
                auto_id=True,
                drop_old=False
            )

            # 7. 检查集合数据（仅当require_data为True时）
            if self.require_data:
                # 显式指定连接别名获取集合
                coll = Collection(name=collection_name, using=alias)
                coll.load()
                logger.info(f"集合 '{collection_name}'")

            logger.info(f"Milvus向量库初始化成功：{collection_name}（连接：{alias}）")
            return vector_store

        except Exception as e:
            # 捕获并增强错误信息
            error_msg = f"Milvus初始化失败：{str(e)}"
            if "connection refused" in str(e).lower():
                error_msg += f"（检查 {connection_args['host']}:{connection_args['port']} 是否可达）"
            elif "authentication failed" in str(e).lower():
                error_msg += "（检查token是否正确）"
            raise RAGException(500, error_msg)

    def _init_llm_by_strategy(self):
        """根据策略初始化LLM"""
        try:
            logger.info(f"llm_strategy:{self.llm_strategy}")
            if self.llm_strategy == "ollama":
                from langchain_ollama import OllamaLLM
                llm = OllamaLLM(
                    model=self.llm_model,
                    base_url=self.ollama_base_url,
                    temperature=0.1,
                    client_kwargs={"timeout": 600}
                )
                logger.info(f"使用Ollama LLM: {self.llm_model}")

            elif self.llm_strategy == "silicon_flow":
                # 尝试不同的模型名称
                self.llm_model = settings.SILICON_FLOW_LLM_MODEL
                llm = ChatOpenAI(
                    model=settings.SILICON_FLOW_LLM_MODEL,
                    openai_api_key=self.silicon_flow_api_key,
                    openai_api_base=self.silicon_flow_base_url,
                    temperature=0.1,
                    timeout=600
                )
                logger.info(f"使用硅基流动 LLM: {settings.SILICON_FLOW_LLM_MODEL}")
            else:
                raise RAGQueryException(400, f"不支持的LLM策略: {self.llm_strategy}")

            # 测试LLM响应
            test_resp = llm.invoke("仅返回'pong'")
            if "pong" not in str(test_resp).strip().lower():
                raise RAGQueryException(503, f"LLM测试失败：返回'{str(test_resp)[:30]}'")

            logger.info(f"LLM就绪：{self.llm_model}")
            return llm

        except Exception as e:
            if isinstance(e, RAGQueryException):
                raise e
            error_msg = str(e)
            if "connection refused" in error_msg.lower():
                if self.llm_strategy == LLMStrategy.OLLAMA:
                    error_msg = f"Ollama服务连接失败（地址：{self.ollama_base_url}）"
                else:
                    error_msg = f"硅基流动服务连接失败（地址：{self.silicon_flow_base_url}）"
            elif "model not found" in error_msg.lower():
                if self.llm_strategy == LLMStrategy.OLLAMA:
                    error_msg = f"LLM模型不存在（需执行'ollama pull {self.llm_model}'）"
                else:
                    error_msg = f"LLM模型不存在或无权访问：{self.llm_model}"
            elif "authentication" in error_msg.lower():
                error_msg = "API密钥认证失败，请检查硅基流动API密钥"
            raise RAGQueryException(503, error_msg)

    def _init_qa_chain(self) -> RetrievalQA:
        """初始化QA链（绑定NRS2002规则Prompt，确保输出带依据）"""
#         nrs2002_prompt = PromptTemplate(
#             template="""
        # 任务：根据NRS2002营养风险筛查规则，基于参考上下文计算患者评分，输出JSON（含评分和依据说明）。
        #
        # NRS2002核心规则（必须严格遵守）：
        # 1. 营养状态受损（0-3分）：
        #    - 0分：BMI≥20.5且体重无下降且无进食困难
        #    - 1分：BMI18.5-20.4/近3月体重降3%-5%/进食减25%-50%
        #    - 2分：BMI＜18.5/近3月体重降5%-10%/进食减50%-75%
        #    - 3分：近3月体重降＞10%/进食减＞75%/BMI＜18.5+重病
        # 2. 疾病严重程度（0-3分）：
        #    - 0分：良性疾病+正常进食（如稳定期慢性病）
        #    - 1分：慢病急性发作+需卧床（如COPD急性加重）
        #    - 2分：大手术/中风/ICU+需人工营养
        #    - 3分：大面积烧伤/多器官衰竭+紧急营养
        # 3. 年龄（0-1分）：≥70岁得1分，否则0分
        # 4. 总分=三部分之和（缺失参数默认0分，总分范围0-7分）
        #
        # 要求：
        # 1. 先分析患者信息匹配哪条规则，再计算各维度分数和总分；
        # 2. 输出JSON必须包含"score"（总分）、"nutritional_impairment"（营养受损分）、"disease_severity"（疾病严重度分）、"age"（年龄分）、"basis"（评分依据，说明匹配的规则条款）；
        # 3. 仅输出JSON，无多余文字。
        #
        # 用户问题（患者信息）：{question}
        # 参考上下文（NRS2002规则片段）：{context}
        #
        # 输出格式示例：
        # {{
        #   "score": 2,
        #   "nutritional_impairment": 1,
        #   "disease_severity": 1,
        #   "age": 0,
        #   "basis": "1.营养受损：BMI19.2（18.5-20.4）→1分；2.疾病严重度：COPD急性加重→1分；3.年龄65岁＜70→0分；总分1+1+0=2分"
        # }}
        # """,
        #             input_variables=["context", "question"]
        #         )

        nrs2002_prompt = PromptTemplate(
            template="""
        任务：根据NRS2002营养风险筛查规则，基于参考上下文计算患者评分，输出JSON（含评分和依据说明）。
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

        return RetrievalQA.from_chain_type(
            llm=self.llm,
            chain_type="stuff",
            retriever=self.vector_store.as_retriever(search_kwargs={"k": 2}),  # 取最相关的2个规则片段
            chain_type_kwargs={"prompt": nrs2002_prompt},
            return_source_documents=True  # 返回源文档作为验证依据
        )

    def query_score(self, user_question: str, file_id: Optional[str] = None,collection_name: Optional[str] = None) -> Dict[str, Any]:
        """核心查询方法：输入患者信息，返回NRS2002评分、依据说明和源文档片段"""
        # 1. 输入校验
        if not user_question.strip():
            raise RAGQueryException(400, "患者信息不能为空（需包含BMI、体重变化、疾病状态、年龄等关键信息）")

        try:
            # 2. 构建检索过滤条件
            search_kwargs = {"k": 2}
            if file_id:
                # 处理file_id中的单引号
                safe_file_id = str(file_id).replace("'", "''")
                search_kwargs["expr"] = f"file_id == '{safe_file_id}'"
                logger.info(f"Milvus过滤条件expr: {search_kwargs['expr']}")

                # 修复计数逻辑：使用Collection的query方法
                selected_collection = collection_name if collection_name else self.collection_name
                # 关键修复：创建Collection时指定连接别名
                coll = Collection(
                    name=selected_collection,
                    using=settings.MILVUS_DEFAULT_ALIAS  # 与初始化时的别名保持一致
                )
                if not coll.is_empty:
                    coll.load()

                # 直接查询判断是否存在匹配数据
                res = coll.query(
                    expr=search_kwargs["expr"],
                    output_fields=["pk","text"],
                    limit=10
                )
                count = len(res)
                logger.info(f"符合条件的实体数量: {count}")

                if count == 0:
                    raise RAGQueryException(404, f"指定file_id={file_id}无匹配的NRS2002规则数据")

            self.qa_chain.retriever.search_kwargs = search_kwargs

            # 3. 执行RAG查询
            logger.info(f"执行查询：{user_question[:50]}...")
            result = self.qa_chain.invoke({"query": user_question.strip()})

            # 4. 解析并验证LLM输出
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

            # 验证分数范围
            if not (0 <= score_data["score"] <= 7):
                raise RAGQueryException(500, f"总分必须在0-7分之间（当前：{score_data['score']}）")
            for field in ["nutritional_impairment", "disease_severity"]:
                if not (0 <= score_data[field] <= 3):
                    raise RAGQueryException(500, f"{field}必须在0-3分之间（当前：{score_data[field]}）")
            if score_data["age"] not in [0, 1]:
                raise RAGQueryException(500, f"年龄分必须为0或1（当前：{score_data['age']}）")

            # 5. 整理源文档依据
            source_basis = []
            for doc in result["source_documents"]:
                source_basis.append({
                    "pk": doc.metadata.get("pk", "unknown"),
                    "file_name": doc.metadata.get("file_name", "unknown"),
                    "file_id": doc.metadata.get("file_id", "unknown"),
                    "rule_fragment": doc.page_content.strip()[:200] + "..." if len(
                        doc.page_content) > 200 else doc.page_content.strip()
                })

            # 6. 返回最终结果
            return {
                "code": 200,
                "message": "查询成功",
                "score_result": score_data,
                "source_basis": source_basis
            }

        except RAGQueryException as e:
            logger.error(f"查询失败：{e.message}")
            raise
        except Exception as e:
            raise RAGQueryException(500, f"查询流程异常：{str(e)}")