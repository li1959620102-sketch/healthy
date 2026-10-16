import logging
import os
import time
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from vanna.ollama.ollama import Ollama
from vanna.chromadb.chromadb_vector import ChromaDB_VectorStore
import pymysql
from app.config import settings

logger = logging.getLogger(__name__)


class TrainingDataLoader:
    """训练数据加载器"""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            base_dir = Path(__file__).parent.parent
            config_path = base_dir / 'config' / 'training_data.json'

        self.config_path = Path(config_path)
        self.training_data = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """加载训练配置文件"""
        try:
            if not self.config_path.exists():
                logger.warning(f"训练配置文件不存在: {self.config_path}")
                return self._get_default_config()

            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info(f"成功加载训练配置文件: {self.config_path}")
                return config
        except Exception as e:
            logger.error(f"加载训练配置文件失败: {str(e)}")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """返回默认配置"""
        return {
            "table_definitions": [],
            "documentations": [],
            "example_queries": [],
            "training_config": {
                "enable_ddl_training": True,
                "enable_documentation_training": True,
                "enable_query_training": True
            }
        }

    def get_table_definitions(self) -> List[Dict[str, str]]:
        """获取表定义"""
        return self.training_data.get("table_definitions", [])

    def get_documentations(self) -> List[Dict[str, str]]:
        """获取文档"""
        return self.training_data.get("documentations", [])

    def get_example_queries(self) -> List[Dict[str, str]]:
        """获取示例查询"""
        return self.training_data.get("example_queries", [])

    def get_training_config(self) -> Dict[str, Any]:
        """获取训练配置"""
        return self.training_data.get("training_config", {})


class HealthRiskVanna(ChromaDB_VectorStore, Ollama):
    """健康风险评估 Vanna 实例"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if config is None:
            config = {}

        # 初始化训练数据加载器
        self.training_loader = TrainingDataLoader()

        # 配置 ChromaDB 路径
        self._setup_chromadb_path(config)

        # 配置模型参数
        self._setup_model_config(config)

        # 初始化父类
        self._initialize_with_retry(config)

        # 配置数据库连接
        self._setup_database_config()
        self.connection = None

    def _setup_chromadb_path(self, config: Dict[str, Any]) -> None:
        """设置 ChromaDB 路径"""
        base_dir = Path(__file__).parent.parent
        chroma_path = config.get('chroma_db_path', str(base_dir / 'chromadb'))
        chroma_path = os.path.abspath(chroma_path)
        os.makedirs(chroma_path, exist_ok=True)
        config['chroma_db_path'] = chroma_path
        logger.info(f"初始化 ChromaDB，路径: {chroma_path}")

    def _setup_model_config(self, config: Dict[str, Any]) -> None:
        """设置模型配置"""
        # 设置嵌入模型
        if 'embedding_model' not in config:
            config['embedding_model'] = 'bge-m3:latest'

        # 设置 Ollama 主机
        if 'ollama_host' not in config:
            config['ollama_host'] = settings.OLLAMA_BASE_URL

        # 设置主模型
        if 'model' not in config:
            config['model'] = settings.LLM_MODEL

        self.model_config = config
        logger.info(f"模型配置 - 主模型: {config['model']}, 嵌入模型: {config['embedding_model']}")

    def _initialize_with_retry(self, config: Dict[str, Any]) -> None:
        """带重试机制的初始化"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                ChromaDB_VectorStore.__init__(self, config=config)
                Ollama.__init__(self, config=config)
                logger.info(f"Vanna 初始化成功，使用嵌入模型: {config.get('embedding_model')}")
                return
            except Exception as e:
                logger.warning(f"初始化尝试 {attempt + 1} 失败: {str(e)}")
                if attempt == max_retries - 1:
                    logger.warning("使用内存模式作为备选")
                    config['chroma_db_path'] = None
                    ChromaDB_VectorStore.__init__(self, config=config)
                    Ollama.__init__(self, config=config)
                else:
                    time.sleep(2)

    def _setup_database_config(self) -> None:
        """设置数据库配置"""
        self.db_config = {
            'host': settings.MYSQL_HOST,
            'port': int(settings.MYSQL_PORT),
            'user': settings.MYSQL_USER,
            'password': settings.MYSQL_PASSWORD,
            'database': settings.MYSQL_DB,
            'charset': 'utf8mb4',
            'connect_timeout': 10,
            'read_timeout': 30,
            'write_timeout': 30
        }

    def connect_to_mysql(self) -> bool:
        """连接 MySQL 数据库"""
        try:
            if self.connection is None or not self.connection.open:
                self.connection = pymysql.connect(**self.db_config)
                logger.info("成功连接到 MySQL 数据库")
            return True
        except Exception as e:
            logger.error(f"数据库连接失败: {str(e)}")
            return False

    def execute_sql(self, sql: str, retry: bool = True) -> List[Dict[str, Any]]:
        """执行 SQL 查询"""
        try:
            if not self.connect_to_mysql():
                raise Exception("无法连接到数据库")

            with self.connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql)
                result = cursor.fetchall()
                return result
        except Exception as e:
            logger.error(f"SQL执行失败: {str(e)}")
            if retry:
                logger.info("尝试重新连接并重试...")
                self.connection = None
                return self.execute_sql(sql, retry=False)
            raise

    def train_from_config(self) -> bool:
        """从配置文件训练数据"""
        training_config = self.training_loader.get_training_config()

        if not training_config.get('enable_ddl_training', True):
            logger.info("DDL 训练已禁用")
        else:
            self._train_table_definitions()

        if not training_config.get('enable_documentation_training', True):
            logger.info("文档训练已禁用")
        else:
            self._train_documentations()

        if not training_config.get('enable_query_training', True):
            logger.info("查询训练已禁用")
        else:
            self._train_example_queries()

        logger.info("配置文件训练完成")
        return True

    def train_from_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """从上传的 JSON 数据训练模型"""
        result = {
            "success": True,
            "ddl_trained": 0,
            "documentation_trained": 0,
            "queries_trained": 0,
            "errors": []
        }

        training_config = data.get("training_config", {})

        # 训练表定义 (DDL)
        if training_config.get("enable_ddl_training", True):
            for table in data.get("table_definitions", []):
                try:
                    ddl = table.get("ddl")
                    if ddl:
                        self.train(ddl=ddl)
                        result["ddl_trained"] += 1
                        logger.info(f"训练表定义: {table.get('name', 'unknown')}")
                except Exception as e:
                    msg = f"训练表 {table.get('name', 'unknown')} 失败: {str(e)}"
                    logger.error(msg)
                    result["errors"].append(msg)

        # 训练文档
        if training_config.get("enable_documentation_training", True):
            for doc in data.get("documentations", []):
                try:
                    content = doc.get("content")
                    if content:
                        self.train(documentation=content)
                        result["documentation_trained"] += 1
                        logger.info(f"训练文档: {doc.get('title', 'unknown')}")
                except Exception as e:
                    msg = f"训练文档 {doc.get('title', 'unknown')} 失败: {str(e)}"
                    logger.error(msg)
                    result["errors"].append(msg)

        # 训练示例查询 (SQL)
        if training_config.get("enable_query_training", True):
            for query in data.get("example_queries", []):
                try:
                    sql = query.get("sql")
                    if sql:
                        self.train(sql=sql)
                        result["queries_trained"] += 1
                        logger.info(f"训练查询: {query.get('description', 'unknown')}")
                except Exception as e:
                    msg = f"训练查询 {query.get('description', 'unknown')} 失败: {str(e)}"
                    logger.error(msg)
                    result["errors"].append(msg)

        total = result["ddl_trained"] + result["documentation_trained"] + result["queries_trained"]
        if result["errors"]:
            result["success"] = total > 0
            result["message"] = f"训练完成（部分失败）：共训练 {total} 条，{len(result['errors'])} 条失败"
        else:
            result["message"] = f"训练完成：共训练 DDL {result['ddl_trained']} 条、文档 {result['documentation_trained']} 条、查询 {result['queries_trained']} 条"

        logger.info(result["message"])
        return result

    def _train_table_definitions(self) -> None:
        """训练表定义"""
        tables = self.training_loader.get_table_definitions()
        if not tables:
            logger.info("没有找到表定义，跳过 DDL 训练")
            return

        for table in tables:
            try:
                ddl = table.get('ddl')
                if ddl:
                    self.train(ddl=ddl)
                    logger.info(f"训练表定义: {table.get('name', 'unknown')}")
                else:
                    logger.warning(f"表 {table.get('name', 'unknown')} 没有 DDL 定义")
            except Exception as e:
                logger.error(f"训练表 {table.get('name', 'unknown')} 失败: {str(e)}")

    def _train_documentations(self) -> None:
        """训练文档"""
        docs = self.training_loader.get_documentations()
        if not docs:
            logger.info("没有找到文档，跳过文档训练")
            return

        for doc in docs:
            try:
                content = doc.get('content')
                if content:
                    self.train(documentation=content)
                    logger.info(f"训练文档: {doc.get('title', 'unknown')}")
                else:
                    logger.warning(f"文档 {doc.get('title', 'unknown')} 没有内容")
            except Exception as e:
                logger.error(f"训练文档 {doc.get('title', 'unknown')} 失败: {str(e)}")

    def _train_example_queries(self) -> None:
        """训练示例查询"""
        queries = self.training_loader.get_example_queries()
        if not queries:
            logger.info("没有找到示例查询，跳过查询训练")
            return

        success_count = 0
        for query in queries:
            try:
                sql = query.get('sql')
                if sql:
                    self.train(sql=sql)
                    success_count += 1
                    logger.info(f"训练查询: {query.get('description', 'unknown')}")
                else:
                    logger.warning(f"查询 {query.get('description', 'unknown')} 没有 SQL")
            except Exception as e:
                logger.error(f"训练查询 {query.get('description', 'unknown')} 失败: {str(e)}")

        logger.info(f"成功训练 {success_count}/{len(queries)} 个示例查询")

    def generate_sql(self, question: str, **kwargs) -> str:
        """生成 SQL 查询"""
        try:
            sql = super().generate_sql(question, **kwargs)
            logger.info(f"生成 SQL: {sql}")
            return sql
        except Exception as e:
            logger.error(f"生成 SQL 失败: {str(e)}")
            raise

    def ask(self, question: str, **kwargs) -> str:
        """询问问题并返回结果"""
        try:
            response = super().ask(question, **kwargs)
            return response
        except Exception as e:
            logger.error(f"询问失败: {str(e)}")
            return f"抱歉，处理您的问题时出现错误: {str(e)}"


def create_vanna_instance() -> HealthRiskVanna:
    """创建 Vanna 实例的工厂函数"""
    base_dir = Path(__file__).parent.parent
    chroma_db_path = base_dir / 'chromadb'
    chroma_db_path.mkdir(parents=True, exist_ok=True)

    vanna_config = {
        'model': settings.LLM_MODEL,
        'embedding_model': 'bge-m3:latest',
        'ollama_host': settings.OLLAMA_BASE_URL,
        'chroma_db_path': str(chroma_db_path)
    }

    logger.info("正在创建 Vanna 实例...")
    logger.info(f"  - 主模型: {vanna_config['model']}")
    logger.info(f"  - 嵌入模型: {vanna_config['embedding_model']}")
    logger.info(f"  - Ollama 地址: {vanna_config['ollama_host']}")
    logger.info(f"  - ChromaDB 路径: {vanna_config['chroma_db_path']}")

    instance = HealthRiskVanna(config=vanna_config)

    # 注释掉自动训练，改为需要时手动调用
    # try:
    #     logger.info("开始从配置文件训练数据...")
    #     instance.train_from_config()
    # except Exception as e:
    #     logger.warning(f"自动训练失败: {str(e)}")

    # 可选：在后台线程中训练
    import threading
    def train_in_background():
        try:
            logger.info("后台线程开始训练...")
            instance.train_from_config()
            logger.info("后台训练完成")
        except Exception as e:
            logger.error(f"后台训练失败: {str(e)}")

    # 启动后台训练（不阻塞主线程）
    train_thread = threading.Thread(target=train_in_background, daemon=True)
    train_thread.start()

    return instance
vn = create_vanna_instance()